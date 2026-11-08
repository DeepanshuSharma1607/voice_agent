"""Gemini Live: config, connection, greeting, end_call tool, diagnostics."""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

from google import genai
from google.genai import types

from .config import Settings
from .prompts import SYSTEM_INSTRUCTION

logger = logging.getLogger("voice_agent.gemini")

END_CALL_TOOL = {
    "function_declarations": [
        {
            "name": "end_call",
            "description": (
                "Ends the phone call. Call this only after you have spoken "
                "a brief goodbye, when the conversation is finished."
            ),
        }
    ]
}

# Event tags that are routine and not worth logging at INFO.
QUIET_TAGS = {"audio", "input_tx", "output_tx", "usage"}
DEBUG_TAGS = {"voice_activity"}


def create_client(settings: Settings) -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


def build_live_config(settings: Settings) -> dict[str, Any]:
    config: dict[str, Any] = {
        "response_modalities": ["AUDIO"],
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": settings.voice_name}
            }
        },
        "system_instruction": SYSTEM_INSTRUCTION,
        "tools": [END_CALL_TOOL],
    }
    if settings.vad_silence_ms > 0:
        # How much silence counts as "caller finished speaking". Lower = faster replies.
        config["realtime_input_config"] = {
            "automatic_activity_detection": {
                "silence_duration_ms": settings.vad_silence_ms
            }
        }
    return config


async def connect(
    stack: contextlib.AsyncExitStack, client: genai.Client, settings: Settings
):
    """Open the Gemini Live session; closing it is the stack's job."""
    started = time.monotonic()
    session = await stack.enter_async_context(
        client.aio.live.connect(
            model=settings.gemini_model, config=build_live_config(settings)
        )
    )
    logger.info("Connected to Gemini in %.2fs.", time.monotonic() - started)
    return session


async def send_greeting(session, greeting_trigger: str, log=logger) -> None:
    """Send the hidden first message so Gemini speaks the hello before the caller."""
    try:
        await session.send_realtime_input(text=greeting_trigger)
    except Exception as exc:
        log.warning(
            "send_realtime_input(text) failed (%r); falling back to send_client_content.",
            exc,
        )
        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=greeting_trigger)]),
            turn_complete=True,
        )
    log.info("Greeting trigger sent.")


async def handle_tool_calls(session, response, log=logger) -> bool:
    """Answer Gemini's tool calls. Returns True if Gemini called end_call."""
    tool_call = getattr(response, "tool_call", None)
    if tool_call is None:
        return False

    pairs = []
    end_requested = False

    for fc in tool_call.function_calls or []:
        if fc.name == "end_call":
            end_requested = True
            log.info("Gemini called end_call.")
            payload = {"result": "Call is ending now. Say nothing more."}
        else:
            log.warning("Unknown tool call ignored: %s", fc.name)
            payload = {"error": f"Unknown function: {fc.name}"}
        pairs.append((fc, payload))

    if not pairs:
        return False

    def build(with_scheduling: bool) -> list:
        items = []
        for fc, payload in pairs:
            kwargs: dict[str, Any] = {"id": fc.id, "name": fc.name, "response": payload}
            if with_scheduling:
                kwargs["scheduling"] = "SILENT"  # don't let Gemini speak after the result
            items.append(types.FunctionResponse(**kwargs))
        return items

    try:
        await session.send_tool_response(function_responses=build(True))
    except Exception as exc:
        log.warning("Tool response with SILENT scheduling failed (%r); retrying without it.", exc)
        await session.send_tool_response(function_responses=build(False))

    return end_requested


def describe_response(response) -> list[str]:
    """List which fields a Gemini message carries (for diagnostics)."""
    tags: list[str] = []

    if getattr(response, "setup_complete", None) is not None:
        tags.append("setup_complete")
    if getattr(response, "go_away", None) is not None:
        tags.append("go_away")
    if getattr(response, "tool_call", None) is not None:
        tags.append("tool_call")
    if getattr(response, "session_resumption_update", None) is not None:
        tags.append("session_resumption_update")
    if getattr(response, "usage_metadata", None) is not None:
        tags.append("usage")
    if (
        getattr(response, "voice_activity", None) is not None
        or getattr(response, "voice_activity_detection_signal", None) is not None
    ):
        tags.append("voice_activity")

    sc = getattr(response, "server_content", None)
    if sc is not None:
        if getattr(sc, "input_transcription", None):
            tags.append("input_tx")
        if getattr(sc, "output_transcription", None):
            tags.append("output_tx")
        if getattr(sc, "interrupted", None):
            tags.append("interrupted")
        if getattr(sc, "generation_complete", None):
            tags.append("generation_complete")
        if getattr(sc, "turn_complete", None):
            tags.append("turn_complete")
        model_turn = getattr(sc, "model_turn", None)
        if model_turn is not None:
            for part in model_turn.parts or []:
                if getattr(part, "inline_data", None) is not None:
                    tags.append("audio")
                    break
            for part in model_turn.parts or []:
                if getattr(part, "text", None):
                    tags.append("text")
                    break

    return tags or ["empty/unknown"]
