import asyncio
import base64
import contextlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soxr
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types
from starlette.websockets import WebSocketState


# =====================================================
# ENV LOADING (searches this folder, then every parent folder)
# =====================================================

def load_env_file() -> Path | None:
    for folder in Path(__file__).resolve().parents:
        candidate = folder / ".env"
        if candidate.is_file():
            load_dotenv(dotenv_path=candidate, override=True)
            return candidate
    return None


ENV_PATH = load_env_file()
print(f"[CONFIG] .env loaded from: {ENV_PATH}" if ENV_PATH else "[CONFIG] No .env file found.")


# =====================================================
# CONFIGURATION
# =====================================================

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("GEMINI_LIVE_MODEL", "").strip()
EXOTEL_WS_URL = os.getenv("EXOTEL_WS_URL", "").strip()

_missing = [
    name
    for name, value in (
        ("GEMINI_API_KEY", API_KEY),
        ("GEMINI_LIVE_MODEL", MODEL),
        ("EXOTEL_WS_URL", EXOTEL_WS_URL),
    )
    if not value
]
if _missing:
    raise RuntimeError(f"Missing required .env keys: {', '.join(_missing)}")

client = genai.Client(api_key=API_KEY)
app = FastAPI(title="Gemini + Exotel AI Phone Agent")

GEMINI_INPUT_RATE = 16000          # Gemini Live expects 16 kHz PCM16 input
GEMINI_OUTPUT_RATE = 24000         # Gemini Live returns 24 kHz PCM16 output
EXOTEL_OUTPUT_PACKET_SIZE = 3200   # Exotel minimum packet size; multiple of 320 bytes
GOODBYE_TIMEOUT_SECONDS = 10.0     # force hang-up if Gemini never finishes its goodbye
MAX_PLAYBACK_WAIT_SECONDS = 15.0   # cap on waiting for goodbye audio to finish playing
PLAYBACK_MARGIN_SECONDS = 0.5      # extra buffer so the last word isn't clipped
IDLE_WARN_SECONDS = 10.0           # warn if Gemini sends nothing for this long
RAW_RESPONSES_TO_LOG = 5           # print the first N raw Gemini messages (truncated)
RECENT_TURN_SECONDS = 5.0          # end_call arriving this soon after a turn = that turn was the goodbye
START_EVENT_TIMEOUT_SECONDS = 30   # how long to wait for Exotel's "start" event

# Hidden first message sent from code to Gemini the moment the call is live.
# Gemini answers it out loud, so the caller hears the AI speak first.
GREETING_TRIGGER = (
    "The phone call just connected. Greet the caller with one short, warm hello "
    "and ask how you can help. Say nothing else."
)


# =====================================================
# HTTP ROUTES
# =====================================================

@app.get("/")
async def exotel_resolver():
    """Exotel dynamic-URL resolver: returns the Voicebot WebSocket URL."""
    return {"url": EXOTEL_WS_URL}


@app.get("/health")
async def health():
    return {
        "status": "online",
        "service": "Gemini Exotel Voice Agent",
        "model": MODEL,
    }


# =====================================================
# HANG-UP INTENT DETECTION
# =====================================================

# Only explicit caller phrases should end the call. Avoid broad phrases like
# "I'm done" because a customer may mean they have finished giving details.
HANGUP_PATTERNS = [
    re.compile(r"\bhang\s*up\b", re.IGNORECASE),
    re.compile(r"\bend\s+(?:the\s+|this\s+|our\s+)?call\b", re.IGNORECASE),
    re.compile(r"\bdisconnect\s+(?:the\s+|this\s+)?call\b", re.IGNORECASE),
    re.compile(r"\bdisconnect\s+me\b", re.IGNORECASE),
    re.compile(r"\bcut\s+(?:the\s+|this\s+)?call\b", re.IGNORECASE),
    re.compile(r"\bstop\s+(?:the\s+|this\s+)?call\b", re.IGNORECASE),
    re.compile(r"\b(?:can|could|will)\s+you\s+cut\s+it\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+(?:the\s+|this\s+)?call\b", re.IGNORECASE),
    re.compile(r"\bterminate\s+(?:the\s+|this\s+)?call\b", re.IGNORECASE),
    re.compile(r"\b(?:goodbye|good\s+bye|bye\s+bye)\b", re.IGNORECASE),
    re.compile(r"\bbye\b\s*[.!?]*$", re.IGNORECASE),
]

NEGATED_HANGUP_PATTERNS = [
    re.compile(r"\b(?:don't|do not|never|not)\s+(?:hang\s*up|end|disconnect|cut|drop)\b", re.IGNORECASE),
    re.compile(r"\bwait\b", re.IGNORECASE),
    re.compile(r"\bhold on\b", re.IGNORECASE),
    re.compile(r"\bnot yet\b", re.IGNORECASE),
]


def is_explicit_hangup_request(text: str) -> bool:
    """Return True only for a clear request to end the phone call."""
    if not text:
        return False

    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return False

    if any(pattern.search(cleaned) for pattern in NEGATED_HANGUP_PATTERNS):
        return False

    return any(pattern.search(cleaned) for pattern in HANGUP_PATTERNS)


# =====================================================
# GEMINI LIVE CONFIGURATION
# =====================================================

# NOTE: realtime_input_config (voice-activity detection) is intentionally
# omitted so Gemini uses its defaults. Add it back only after the call flow
# is confirmed working.
GEMINI_CONFIG: dict[str, Any] = {
    "response_modalities": ["AUDIO"],
    "input_audio_transcription": {},
    "output_audio_transcription": {},
    "speech_config": {
        "voice_config": {
            "prebuilt_voice_config": {
                "voice_name": "Puck"
            }
        }
    },
    "system_instruction": """
You are a friendly, smart voice assistant that people talk to over a phone call.
Anyone can call you: friends, family, anybody. Help with whatever they ask,
the way a knowledgeable, warm friend would. You can answer general questions,
explain things, give advice and ideas, help with everyday problems, chat casually,
tell short stories or jokes, help with learning, math, cooking, travel, health
basics, tech, and more.

HOW TO SPEAK:
1. Sound natural and warm, like a real person on a phone call.
2. Keep answers short: usually one to three sentences. Go longer only if the
   caller asks for detail, and then explain in simple spoken language.
3. Never use bullet points, lists, markdown, emojis, or symbols. The caller is
   listening, not reading. Say things in flowing sentences.
4. Reply in the same language the caller speaks (English, Hindi, Hinglish, etc.)
   and match their tone.
5. Ask at most one question at a time, and only when you really need it.
6. If you did not catch something, politely ask the caller to repeat it.

HONESTY AND SAFETY:
7. Never make up facts. If you are not sure, say so plainly instead of guessing.
8. You cannot look up live information such as today's news, weather, or sports
   scores, so say so if asked.
9. For medical, legal, or money questions, give helpful general information and
   gently suggest a professional for serious matters.
10. If someone sounds like they are in danger or serious distress, respond with
    care and encourage them to contact local emergency services or a trusted person.
11. Politely decline anything harmful or illegal.

ENDING THE CALL:
12. You have a function called end_call. Whenever the call is over, first say a
    very brief, friendly goodbye out loud, and only then call end_call. The call
    is over when the caller says goodbye or asks you to end or hang up, or when
    they confirm they need nothing else.
13. Never call end_call before speaking your goodbye, and never call it while
    the caller still has an open question or request.
14. If the caller asks you to hang up, do not ask more questions. Just say a
    brief goodbye.
""",
    "tools": [
        {
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
    ],
}


# =====================================================
# STREAMING AUDIO RESAMPLER (stateful -> no clicks between chunks)
# =====================================================

def _to_pcm16(samples: np.ndarray) -> bytes:
    if samples.size == 0:
        return b""
    return np.clip(np.round(samples * 32768.0), -32768, 32767).astype("<i2").tobytes()


def pcm16_rms(pcm: bytes) -> float:
    """RMS level of PCM16 audio in int16 units (silence is near 0, speech is hundreds+)."""
    usable = len(pcm) - (len(pcm) % 2)
    if usable == 0:
        return 0.0
    samples = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples)))


class StreamResampler:
    """Mono PCM16 little-endian streaming resampler with odd-byte carry-over."""

    def __init__(self, in_rate: int, out_rate: int) -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        self.passthrough = in_rate == out_rate
        self._carry = b""
        self._stream = self._new_stream()

    def _new_stream(self):
        if self.passthrough:
            return None
        return soxr.ResampleStream(
            self.in_rate, self.out_rate, 1, dtype="float32", quality="HQ"
        )

    def process(self, data: bytes) -> bytes:
        if not data:
            return b""

        data = self._carry + data
        usable = len(data) - (len(data) % 2)
        self._carry = data[usable:]
        data = data[:usable]
        if not data:
            return b""

        if self.passthrough:
            return data

        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        return _to_pcm16(self._stream.resample_chunk(samples))

    def flush(self) -> bytes:
        """Drain the resampler's tail and start a fresh stream."""
        if self.passthrough:
            return b""
        tail = self._stream.resample_chunk(np.zeros(0, dtype=np.float32), last=True)
        self.reset()
        return _to_pcm16(tail)

    def reset(self) -> None:
        self._carry = b""
        self._stream = self._new_stream()


# =====================================================
# CALL STATE
# =====================================================

@dataclass
class CallState:
    stream_sid: str
    sample_rate: int
    hangup_event: asyncio.Event = field(default_factory=asyncio.Event)
    hangup_pending: bool = False          # caller asked to end; waiting for AI goodbye
    caller_turn_text: str = ""            # caller transcript accumulated for current turn
    goodbye_bytes: int = 0                # PCM bytes of goodbye audio sent to Exotel
    goodbye_started_at: float | None = None
    # Diagnostics
    packets_to_gemini: int = 0
    last_gemini_msg_at: float = field(default_factory=time.monotonic)
    gemini_msg_count: int = 0
    # Per-turn audio tracking (so a goodbye spoken BEFORE end_call is still timed correctly)
    cur_audio_bytes: int = 0
    cur_audio_started: float | None = None
    last_audio_bytes: int = 0
    last_audio_started: float | None = None
    last_turn_complete_at: float | None = None


# =====================================================
# EXOTEL OUTGOING AUDIO
# =====================================================

async def send_media(websocket: WebSocket, stream_sid: str, packet: bytes) -> None:
    await websocket.send_json(
        {
            "event": "media",
            "stream_sid": stream_sid,
            "media": {"payload": base64.b64encode(packet).decode("ascii")},
        }
    )


async def send_audio_to_exotel(
    websocket: WebSocket,
    stream_sid: str,
    audio_bytes: bytes,
    audio_buffer: bytearray,
) -> None:
    """Buffer PCM and send Exotel-sized base64 media packets."""
    audio_buffer.extend(audio_bytes)

    while len(audio_buffer) >= EXOTEL_OUTPUT_PACKET_SIZE:
        packet = bytes(audio_buffer[:EXOTEL_OUTPUT_PACKET_SIZE])
        del audio_buffer[:EXOTEL_OUTPUT_PACKET_SIZE]
        await send_media(websocket, stream_sid, packet)


async def flush_audio_buffer(
    websocket: WebSocket,
    stream_sid: str,
    audio_buffer: bytearray,
) -> None:
    """Send the final partial packet, padded with silence to Exotel's minimum size."""
    if not audio_buffer:
        return

    remaining = bytes(audio_buffer)
    audio_buffer.clear()

    if len(remaining) < EXOTEL_OUTPUT_PACKET_SIZE:
        remaining += b"\x00" * (EXOTEL_OUTPUT_PACKET_SIZE - len(remaining))

    await send_media(websocket, stream_sid, remaining)


# =====================================================
# GRACEFUL HANG-UP HELPERS
# =====================================================

async def force_hangup_after(state: CallState, delay: float) -> None:
    """Safety net: hang up even if Gemini never completes its goodbye."""
    await asyncio.sleep(delay)
    if not state.hangup_event.is_set():
        print(f"[CALL] Goodbye not completed within {delay:.0f}s. Forcing hang-up.")
        state.hangup_event.set()


async def finish_goodbye(state: CallState) -> None:
    """Wait for the goodbye audio to finish playing on the phone, then hang up."""
    duration = state.goodbye_bytes / (state.sample_rate * 2)  # PCM16 mono
    elapsed = 0.0
    if state.goodbye_started_at is not None:
        elapsed = time.monotonic() - state.goodbye_started_at

    wait = min(
        MAX_PLAYBACK_WAIT_SECONDS,
        max(0.0, duration - elapsed) + PLAYBACK_MARGIN_SECONDS,
    )
    print(f"[CALL] Goodbye sent ({duration:.1f}s of audio). Waiting {wait:.1f}s for playback.")
    await asyncio.sleep(wait)
    state.hangup_event.set()


# =====================================================
# GEMINI CONNECTION + INSTANT GREETING
# =====================================================

async def connect_gemini(stack: contextlib.AsyncExitStack):
    """Open the Gemini Live session (runs in parallel with waiting for Exotel's start event)."""
    t0 = time.monotonic()
    session = await stack.enter_async_context(
        client.aio.live.connect(model=MODEL, config=GEMINI_CONFIG)
    )
    print(f"[GEMINI] Connected in {time.monotonic() - t0:.2f}s.")
    return session


async def send_greeting(session) -> None:
    """Send the hidden first message from code so Gemini speaks the hello first."""
    try:
        await session.send_realtime_input(text=GREETING_TRIGGER)
    except Exception as exc:
        print(f"[WARN] send_realtime_input(text) failed ({exc!r}); falling back to send_client_content.")
        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=GREETING_TRIGGER)]),
            turn_complete=True,
        )
    print("[GEMINI] Greeting trigger sent.")


# =====================================================
# GEMINI DIAGNOSTICS
# =====================================================

QUIET_TAGS = {"audio", "input_tx", "output_tx", "usage"}


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


# =====================================================
# GEMINI -> QUEUE (reader: keeps the connection drained)
# =====================================================

async def gemini_reader(session, queue: asyncio.Queue, state: CallState) -> None:
    """Continuously pull every Gemini message into a queue, never blocking on processing."""
    empty_rounds = 0
    print("[GEMINI] Reader started.")

    while not state.hangup_event.is_set():
        got_any = False

        # session.receive() ends at each turn boundary, so loop around it.
        async for response in session.receive():
            got_any = True
            state.last_gemini_msg_at = time.monotonic()
            state.gemini_msg_count += 1

            if state.gemini_msg_count <= RAW_RESPONSES_TO_LOG:
                print(f"[GEMINI RAW #{state.gemini_msg_count}]: {repr(response)[:300]}")

            queue.put_nowait(response)

        if got_any:
            empty_rounds = 0
        else:
            empty_rounds += 1
            if empty_rounds >= 5:
                raise RuntimeError("Gemini session closed unexpectedly (receive() yields nothing).")

        await asyncio.sleep(0.01)


# =====================================================
# GEMINI TOOL CALLS (end_call)
# =====================================================

async def respond_to_tool_calls(session, response, state: CallState) -> bool:
    """Answer Gemini's tool calls. Returns True if Gemini called end_call."""
    tool_call = getattr(response, "tool_call", None)
    if tool_call is None:
        return False

    pairs = []
    end_requested = False

    for fc in tool_call.function_calls or []:
        if fc.name == "end_call":
            end_requested = True
            print("[CALL] Gemini called end_call.")
            payload = {"result": "Call is ending now. Say nothing more."}
        else:
            print(f"[GEMINI] Unknown tool call ignored: {fc.name}")
            payload = {"error": f"Unknown function: {fc.name}"}
        pairs.append((fc, payload))

    if not pairs:
        return False

    def build(with_scheduling: bool) -> list:
        items = []
        for fc, payload in pairs:
            kwargs: dict[str, Any] = {"id": fc.id, "name": fc.name, "response": payload}
            if with_scheduling:
                kwargs["scheduling"] = "SILENT"  # don't let Gemini speak again after the tool result
            items.append(types.FunctionResponse(**kwargs))
        return items

    try:
        await session.send_tool_response(function_responses=build(True))
    except Exception as exc:
        print(f"[WARN] Tool response with SILENT scheduling failed ({exc!r}); retrying without it.")
        await session.send_tool_response(function_responses=build(False))

    return end_requested


# =====================================================
# QUEUE -> EXOTEL (processor)
# =====================================================

async def gemini_processor(session, queue: asyncio.Queue, websocket: WebSocket, state: CallState) -> None:
    out_resampler = StreamResampler(GEMINI_OUTPUT_RATE, state.sample_rate)
    audio_buffer = bytearray()
    watchdog: asyncio.Task | None = None
    audio_chunks = 0
    first_audio_logged = False

    print("[GEMINI] Processor started.")

    try:
        while True:
            try:
                response = await asyncio.wait_for(queue.get(), timeout=IDLE_WARN_SECONDS)
            except asyncio.TimeoutError:
                silent_for = time.monotonic() - state.last_gemini_msg_at
                print(
                    f"[WARN] No message from Gemini for {silent_for:.0f}s "
                    f"(audio packets sent to Gemini so far: {state.packets_to_gemini}, "
                    f"Gemini messages received so far: {state.gemini_msg_count})."
                )
                continue

            # ---- Diagnostic: log anything beyond plain audio/transcripts ----
            tags = describe_response(response)
            if any(tag not in QUIET_TAGS for tag in tags):
                print(f"[GEMINI EVENT]: {', '.join(tags)}")

            if getattr(response, "go_away", None) is not None:
                print("[GEMINI] Server sent GoAway:", response.go_away)

            # ---- Tool calls: Gemini pressing the "end call" button ----
            if await respond_to_tool_calls(session, response, state):
                now = time.monotonic()
                if not state.hangup_pending:
                    state.hangup_pending = True
                    watchdog = asyncio.create_task(
                        force_hangup_after(state, GOODBYE_TIMEOUT_SECONDS)
                    )

                if state.cur_audio_bytes > 0:
                    # Goodbye is still being spoken in this turn; finish at turn_complete.
                    state.goodbye_bytes = max(state.goodbye_bytes, state.cur_audio_bytes)
                    if state.goodbye_started_at is None or (
                        state.cur_audio_started is not None
                        and state.cur_audio_started < state.goodbye_started_at
                    ):
                        state.goodbye_started_at = state.cur_audio_started
                elif (
                    state.last_turn_complete_at is not None
                    and now - state.last_turn_complete_at < RECENT_TURN_SECONDS
                    and state.last_audio_bytes > 0
                ):
                    # Goodbye turn already finished just before end_call arrived.
                    state.goodbye_bytes = max(state.goodbye_bytes, state.last_audio_bytes)
                    if state.goodbye_started_at is None:
                        state.goodbye_started_at = state.last_audio_started
                    await finish_goodbye(state)
                    return
                # Otherwise: wait for the goodbye audio / turn_complete (watchdog covers failures).

            server_content = response.server_content
            if server_content is None:
                continue

            # ---- Caller transcript (arrives in fragments) ----
            transcription = server_content.input_transcription
            if transcription and transcription.text:
                state.caller_turn_text += transcription.text
                print(f"[CUSTOMER]: {transcription.text.strip()}", flush=True)

                if not state.hangup_pending and is_explicit_hangup_request(
                    state.caller_turn_text
                ):
                    print("[CALL] Hang-up request detected:", state.caller_turn_text.strip())
                    print("[CALL] Letting Gemini say goodbye before closing.")
                    state.hangup_pending = True
                    watchdog = asyncio.create_task(
                        force_hangup_after(state, GOODBYE_TIMEOUT_SECONDS)
                    )

            # ---- Gemini's own speech transcript ----
            output_text = server_content.output_transcription
            if output_text and output_text.text:
                print(f"[GEMINI SAYS]: {output_text.text}", flush=True)

            # ---- Barge-in: caller spoke over the AI ----
            if server_content.interrupted:
                print("[GEMINI] Caller interrupted the AI.")
                audio_buffer.clear()
                out_resampler.reset()
                audio_chunks = 0
                state.cur_audio_bytes = 0
                state.cur_audio_started = None
                await websocket.send_json(
                    {"event": "clear", "stream_sid": state.stream_sid}
                )

            # ---- Forward Gemini audio to Exotel ----
            model_turn = server_content.model_turn
            if model_turn is not None:
                for part in model_turn.parts or []:
                    if part.inline_data is None or not part.inline_data.data:
                        continue

                    audio_chunks += 1
                    converted = out_resampler.process(part.inline_data.data)
                    if not converted:
                        continue

                    if not first_audio_logged:
                        first_audio_logged = True
                        print("[GEMINI] First audio chunk received (greeting is playing).")

                    if state.cur_audio_started is None:
                        state.cur_audio_started = time.monotonic()
                    state.cur_audio_bytes += len(converted)

                    if state.hangup_pending:
                        if state.goodbye_started_at is None:
                            state.goodbye_started_at = time.monotonic()
                        state.goodbye_bytes += len(converted)

                    await send_audio_to_exotel(
                        websocket, state.stream_sid, converted, audio_buffer
                    )

            # ---- End of Gemini's turn ----
            if server_content.turn_complete:
                tail = out_resampler.flush()
                if tail:
                    if state.hangup_pending:
                        state.goodbye_bytes += len(tail)
                    await send_audio_to_exotel(
                        websocket, state.stream_sid, tail, audio_buffer
                    )
                await flush_audio_buffer(websocket, state.stream_sid, audio_buffer)

                state.caller_turn_text = ""

                # Remember this turn's audio so a later end_call can time the goodbye.
                if tail:
                    state.cur_audio_bytes += len(tail)
                if state.cur_audio_bytes > 0:
                    state.last_audio_bytes = state.cur_audio_bytes
                    state.last_audio_started = state.cur_audio_started
                    state.last_turn_complete_at = time.monotonic()
                state.cur_audio_bytes = 0
                state.cur_audio_started = None

                print(f"[GEMINI] Response completed ({audio_chunks} audio chunks).")
                audio_chunks = 0

                # Only finish once actual goodbye audio has been sent.
                if state.hangup_pending and state.goodbye_bytes > 0:
                    await finish_goodbye(state)
                    return

    finally:
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)


# =====================================================
# EXOTEL -> GEMINI
# =====================================================

async def send_exotel_to_gemini(session, websocket: WebSocket, state: CallState) -> None:
    in_resampler = StreamResampler(state.sample_rate, GEMINI_INPUT_RATE)
    packet_count = 0
    peak_rms = 0.0

    while True:
        raw = await websocket.receive_text()
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            print("[EXOTEL] Ignoring non-JSON message.")
            continue

        event = message.get("event")

        if event == "media":
            payload = message.get("media", {}).get("payload")
            if not payload:
                continue

            pcm = in_resampler.process(base64.b64decode(payload, validate=False))
            if not pcm:
                continue

            peak_rms = max(peak_rms, pcm16_rms(pcm))

            await session.send_realtime_input(
                audio=types.Blob(
                    data=pcm,
                    mime_type=f"audio/pcm;rate={GEMINI_INPUT_RATE}",
                )
            )

            packet_count += 1
            state.packets_to_gemini = packet_count
            if packet_count == 1:
                print("[EXOTEL] First audio packet received.")
            if packet_count % 50 == 0:
                print(
                    f"[EXOTEL] Packets received: {packet_count} "
                    f"| input level (peak RMS, last 50): {peak_rms:.0f}"
                )
                peak_rms = 0.0

        elif event == "dtmf":
            print("[EXOTEL] DTMF:", message.get("dtmf", {}).get("digit"))

        elif event == "stop":
            print("[EXOTEL] Call/stream stopped:", message.get("stop", {}))
            print(f"[EXOTEL] Total packets: {packet_count}")
            return

        elif event == "clear":
            print("[EXOTEL] Clear event received.")

        else:
            print("[EXOTEL] Event:", event)


# =====================================================
# EXOTEL WEBSOCKET ROUTE
# =====================================================

@app.websocket("/exotel/media")
async def exotel_media(websocket: WebSocket):
    await websocket.accept()
    print("\n[EXOTEL] WebSocket connected.")

    state: CallState | None = None
    tasks: list[asyncio.Task] = []

    # Start connecting to Gemini RIGHT AWAY, in parallel with waiting for
    # Exotel's "start" event, so connection setup time is not added to the wait.
    gemini_stack = contextlib.AsyncExitStack()
    gemini_connect_task = asyncio.create_task(
        connect_gemini(gemini_stack), name="gemini-connect"
    )

    try:
        # ---- Wait for Exotel's start event ----
        while True:
            raw = await asyncio.wait_for(
                websocket.receive_text(), timeout=START_EVENT_TIMEOUT_SECONDS
            )
            message = json.loads(raw)
            event = message.get("event")

            if event == "connected":
                print("[EXOTEL] Connection established.")

            elif event == "start":
                start = message.get("start", {})
                stream_sid = start.get("stream_sid") or message.get("stream_sid")
                media_format = start.get("media_format", {})
                sample_rate = int(media_format.get("sample_rate", 16000))

                if sample_rate not in (8000, 16000, 24000):
                    raise ValueError(f"Unsupported Exotel sample rate: {sample_rate}")
                if not stream_sid:
                    raise RuntimeError("Exotel did not provide a stream SID.")

                print("[EXOTEL] Call started.")
                print("[EXOTEL] Call SID:", start.get("call_sid"))
                print("[EXOTEL] Stream SID:", stream_sid)
                print("[EXOTEL] Audio format:", media_format)

                state = CallState(stream_sid=stream_sid, sample_rate=sample_rate)
                break

            else:
                print("[EXOTEL] Event before start:", event)

        # ---- Gemini connection (usually already open by now) ----
        session = await gemini_connect_task

        # ---- Instant greeting: hidden hello goes from code -> Gemini -> caller ----
        await send_greeting(session)

        queue: asyncio.Queue = asyncio.Queue()

        tasks = [
            asyncio.create_task(
                send_exotel_to_gemini(session, websocket, state), name="exotel->gemini"
            ),
            asyncio.create_task(
                gemini_reader(session, queue, state), name="gemini-reader"
            ),
            asyncio.create_task(
                gemini_processor(session, queue, websocket, state), name="gemini->exotel"
            ),
            asyncio.create_task(state.hangup_event.wait(), name="hangup"),
        ]

        done, _pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED
        )

        # Surface errors from whichever task finished first
        # (WebSocketDisconnect included; handled below).
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                print(f"[ERROR] Task '{task.get_name()}' failed.")
                raise task.exception()

    except asyncio.TimeoutError:
        print("[EXOTEL] Timed out waiting for the start event.")

    except WebSocketDisconnect:
        print("[EXOTEL] Caller disconnected / Exotel closed the stream.")

    except Exception as error:
        print("[ERROR]", repr(error))

    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Make sure the Gemini connect task is finished/cancelled, then close the session.
        if not gemini_connect_task.done():
            gemini_connect_task.cancel()
        await asyncio.gather(gemini_connect_task, return_exceptions=True)
        try:
            await gemini_stack.aclose()
        except Exception as exc:
            print("[GEMINI] Session close error:", repr(exc))

        # Closing the WebSocket makes Exotel proceed to its Next Applet.
        # Set that Next Applet to Hangup.
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                hung_up = state is not None and state.hangup_event.is_set()
                reason = "caller-requested-hangup" if hung_up else "voicebot-session-ended"
                await websocket.close(code=1000, reason=reason)
                if hung_up:
                    print("[CALL] WebSocket closed. Exotel should execute the Hangup applet.")
            except Exception as exc:
                print("[CALL] WebSocket close error:", repr(exc))

        print("[EXOTEL] Session closed.")