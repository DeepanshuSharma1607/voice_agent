"""CallSession: runs one phone call end to end (Exotel <-> Gemini)."""

from __future__ import annotations

import asyncio
import contextlib
import time

from fastapi import WebSocket, WebSocketDisconnect
from google import genai
from starlette.websockets import WebSocketState
from google.genai import types

from . import exotel, gemini
from .audio import (
    GEMINI_INPUT_RATE,
    GEMINI_OUTPUT_RATE,
    StreamResampler,
    pcm16_rms,
)
from .config import Settings
from .hangup import is_explicit_hangup_request
from .logging_setup import call_logger
from .metrics import LatencyTracker


class CallSession:
    """Owns all state for a single call and runs the four concurrent tasks:

    1. exotel -> gemini   caller audio in
    2. gemini reader      drains Gemini into a queue (never blocks)
    3. gemini processor   queue -> audio/transcripts/tools -> Exotel
    4. hang-up watcher    ends the call when the hang-up event fires
    """

    def __init__(self, websocket: WebSocket, settings: Settings, client: genai.Client) -> None:
        self.websocket = websocket
        self.settings = settings
        self.client = client
        self.log = call_logger("voice_agent.session")

        self.start: exotel.StartInfo | None = None
        self.session = None  # Gemini live session

        # Hang-up / goodbye state
        self.hangup_event = asyncio.Event()
        self.hangup_pending = False          # caller asked to end; waiting for AI goodbye
        self.caller_turn_text = ""           # caller transcript for the current turn
        self.goodbye_bytes = 0               # PCM bytes of goodbye audio sent to Exotel
        self.goodbye_started_at: float | None = None
        self._watchdog: asyncio.Task | None = None

        # Per-turn audio tracking (so a goodbye spoken BEFORE end_call is still timed)
        self.cur_audio_bytes = 0
        self.cur_audio_started: float | None = None
        self.last_audio_bytes = 0
        self.last_audio_started: float | None = None
        self.last_turn_complete_at: float | None = None

        # Diagnostics
        self.packets_to_gemini = 0
        self.gemini_msg_count = 0
        self.last_gemini_msg_at = time.monotonic()
        self.latency = LatencyTracker()

        # Greeting protection: don't forward caller audio while the greeting plays
        self._greeting_active = False
        self._greeting_deadline = 0.0

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        stack = contextlib.AsyncExitStack()
        # Connect to Gemini in parallel with waiting for Exotel's start event.
        connect_task = asyncio.create_task(
            gemini.connect(stack, self.client, self.settings), name="gemini-connect"
        )
        tasks: list[asyncio.Task] = []

        try:
            await self._wait_for_start()
            self.session = await connect_task
            await self._start_greeting()

            queue: asyncio.Queue = asyncio.Queue()
            tasks = [
                asyncio.create_task(self._exotel_to_gemini(), name="exotel->gemini"),
                asyncio.create_task(self._gemini_reader(queue), name="gemini-reader"),
                asyncio.create_task(self._gemini_processor(queue), name="gemini->exotel"),
                asyncio.create_task(self.hangup_event.wait(), name="hangup"),
            ]

            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            # Surface errors from whichever task finished first.
            for task in done:
                if not task.cancelled() and task.exception() is not None:
                    self.log.error("Task '%s' failed.", task.get_name())
                    raise task.exception()

        except asyncio.TimeoutError:
            self.log.warning("Timed out waiting for the Exotel start event.")
        except WebSocketDisconnect:
            self.log.info("Caller disconnected / Exotel closed the stream.")
        except Exception as error:
            self.log.error("Call failed: %r", error)
        finally:
            await self._cleanup(tasks, connect_task, stack)

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    async def _wait_for_start(self) -> None:
        while True:
            raw = await asyncio.wait_for(
                self.websocket.receive_text(),
                timeout=self.settings.start_event_timeout_seconds,
            )
            message = exotel.parse_message(raw)
            if message is None:
                self.log.debug("Ignoring non-JSON message before start.")
                continue

            event = message.get("event")
            if event == "connected":
                self.log.info("Exotel connection established.")
            elif event == "start":
                self.start = exotel.parse_start(message)
                self.log.extra["call"] = (self.start.call_sid or self.start.stream_sid)[:12]
                self.log.info(
                    "Call started. stream_sid=%s audio=%s",
                    self.start.stream_sid,
                    self.start.media_format,
                )
                return
            else:
                self.log.info("Event before start: %s", event)

    async def _start_greeting(self) -> None:
        protect = self.settings.greeting_protect_seconds
        if protect > 0:
            self._greeting_active = True
            self._greeting_deadline = time.monotonic() + protect
        self.latency.mark_greeting_sent()
        await gemini.send_greeting(self.session, self.settings.greeting_trigger, self.log)

    async def _cleanup(self, tasks, connect_task, stack) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if self._watchdog is not None and not self._watchdog.done():
            self._watchdog.cancel()
            await asyncio.gather(self._watchdog, return_exceptions=True)

        if not connect_task.done():
            connect_task.cancel()
        await asyncio.gather(connect_task, return_exceptions=True)
        try:
            await stack.aclose()
        except Exception as exc:
            self.log.warning("Gemini session close error: %r", exc)

        # Closing the WebSocket makes Exotel proceed to its Next Applet
        # (set that applet to Hangup).
        if self.websocket.application_state == WebSocketState.CONNECTED:
            try:
                hung_up = self.hangup_event.is_set()
                reason = "caller-requested-hangup" if hung_up else "voicebot-session-ended"
                await self.websocket.close(code=1000, reason=reason)
                if hung_up:
                    self.log.info("WebSocket closed. Exotel should run the Hangup applet.")
            except Exception as exc:
                self.log.warning("WebSocket close error: %r", exc)

        self.log.info("Session closed.")

    # ------------------------------------------------------------------
    # Hang-up helpers
    # ------------------------------------------------------------------

    def _arm_hangup(self) -> None:
        """Mark the call as ending and start the safety-net timer (once)."""
        if self.hangup_pending:
            return
        self.hangup_pending = True
        self._watchdog = asyncio.create_task(self._force_hangup_after())

    async def _force_hangup_after(self) -> None:
        delay = self.settings.goodbye_timeout_seconds
        await asyncio.sleep(delay)
        if not self.hangup_event.is_set():
            self.log.warning("Goodbye not completed within %.0fs. Forcing hang-up.", delay)
            self.hangup_event.set()

    async def _finish_goodbye(self) -> None:
        """Wait for the goodbye audio to finish playing on the phone, then hang up."""
        duration = self.goodbye_bytes / (self.start.sample_rate * 2)  # PCM16 mono
        elapsed = 0.0
        if self.goodbye_started_at is not None:
            elapsed = time.monotonic() - self.goodbye_started_at

        wait = min(
            self.settings.max_playback_wait_seconds,
            max(0.0, duration - elapsed) + self.settings.playback_margin_seconds,
        )
        self.log.info("Goodbye sent (%.1fs of audio). Waiting %.1fs for playback.", duration, wait)
        await asyncio.sleep(wait)
        self.hangup_event.set()

    async def _on_end_call(self) -> bool:
        """Gemini called end_call. Returns True if the goodbye already played
        and the processor should stop now."""
        now = time.monotonic()
        self._arm_hangup()

        if self.cur_audio_bytes > 0:
            # Goodbye is still being spoken in this turn; finish at turn_complete.
            self.goodbye_bytes = max(self.goodbye_bytes, self.cur_audio_bytes)
            if self.goodbye_started_at is None or (
                self.cur_audio_started is not None
                and self.cur_audio_started < self.goodbye_started_at
            ):
                self.goodbye_started_at = self.cur_audio_started
            return False

        if (
            self.last_turn_complete_at is not None
            and now - self.last_turn_complete_at < self.settings.recent_turn_seconds
            and self.last_audio_bytes > 0
        ):
            # Goodbye turn already finished just before end_call arrived.
            self.goodbye_bytes = max(self.goodbye_bytes, self.last_audio_bytes)
            if self.goodbye_started_at is None:
                self.goodbye_started_at = self.last_audio_started
            await self._finish_goodbye()
            return True

        # Otherwise wait for goodbye audio / turn_complete (the watchdog covers failures).
        return False

    # ------------------------------------------------------------------
    # Greeting protection
    # ------------------------------------------------------------------

    def _greeting_blocks_input(self) -> bool:
        if not self._greeting_active:
            return False
        if time.monotonic() >= self._greeting_deadline:
            self._greeting_active = False
            self.log.info("Greeting protection timed out; caller audio is now live.")
            return False
        return True

    # ------------------------------------------------------------------
    # Task 1: Exotel -> Gemini
    # ------------------------------------------------------------------

    async def _exotel_to_gemini(self) -> None:
        in_resampler = StreamResampler(self.start.sample_rate, GEMINI_INPUT_RATE)
        peak_rms = 0.0

        while True:
            raw = await self.websocket.receive_text()
            message = exotel.parse_message(raw)
            if message is None:
                self.log.debug("Ignoring non-JSON message.")
                continue

            event = message.get("event")

            if event == "media":
                if self._greeting_blocks_input():
                    continue

                pcm = in_resampler.process(exotel.decode_media(message))
                if not pcm:
                    continue

                peak_rms = max(peak_rms, pcm16_rms(pcm))

                await self.session.send_realtime_input(
                    audio=types.Blob(
                        data=pcm, mime_type=f"audio/pcm;rate={GEMINI_INPUT_RATE}"
                    )
                )

                self.packets_to_gemini += 1
                if self.packets_to_gemini == 1:
                    self.log.info("First caller audio packet sent to Gemini.")
                if self.packets_to_gemini % 50 == 0:
                    self.log.info(
                        "Packets sent: %d | input level (peak RMS, last 50): %.0f",
                        self.packets_to_gemini,
                        peak_rms,
                    )
                    peak_rms = 0.0

            elif event == "dtmf":
                self.log.info("DTMF: %s", (message.get("dtmf") or {}).get("digit"))

            elif event == "stop":
                self.log.info(
                    "Call/stream stopped: %s | total packets: %d",
                    message.get("stop", {}),
                    self.packets_to_gemini,
                )
                return

            elif event == "clear":
                self.log.debug("Clear event received.")

            else:
                self.log.info("Exotel event: %s", event)

    # ------------------------------------------------------------------
    # Task 2: Gemini reader
    # ------------------------------------------------------------------

    async def _gemini_reader(self, queue: asyncio.Queue) -> None:
        """Pull every Gemini message into a queue, never blocking on processing."""
        empty_rounds = 0
        self.log.info("Gemini reader started.")

        while not self.hangup_event.is_set():
            got_any = False

            # session.receive() ends at each turn boundary, so loop around it.
            async for response in self.session.receive():
                got_any = True
                self.last_gemini_msg_at = time.monotonic()
                self.gemini_msg_count += 1

                if self.gemini_msg_count <= self.settings.raw_responses_to_log:
                    self.log.info(
                        "Gemini raw #%d: %s", self.gemini_msg_count, repr(response)[:300]
                    )

                queue.put_nowait(response)

            if got_any:
                empty_rounds = 0
            else:
                empty_rounds += 1
                if empty_rounds >= 5:
                    raise RuntimeError(
                        "Gemini session closed unexpectedly (receive() yields nothing)."
                    )

            await asyncio.sleep(0.01)

    # ------------------------------------------------------------------
    # Task 3: Gemini processor (queue -> Exotel)
    # ------------------------------------------------------------------

    def _log_event(self, tags: list[str]) -> None:
        loud = [tag for tag in tags if tag not in gemini.QUIET_TAGS]
        if not loud:
            return
        quiet_only = all(tag in gemini.DEBUG_TAGS for tag in loud)
        level = 10 if quiet_only else 20  # DEBUG / INFO
        self.log.log(level, "Gemini event: %s", ", ".join(tags))

    async def _gemini_processor(self, queue: asyncio.Queue) -> None:
        out_resampler = StreamResampler(GEMINI_OUTPUT_RATE, self.start.sample_rate)
        sender = exotel.ExotelAudioSender(self.websocket, self.start.stream_sid)
        audio_chunks = 0
        reply_latency_logged = False

        self.log.info("Gemini processor started.")

        while True:
            try:
                response = await asyncio.wait_for(
                    queue.get(), timeout=self.settings.idle_warn_seconds
                )
            except asyncio.TimeoutError:
                silent_for = time.monotonic() - self.last_gemini_msg_at
                self.log.warning(
                    "No message from Gemini for %.0fs (packets sent to Gemini: %d, "
                    "Gemini messages received: %d).",
                    silent_for,
                    self.packets_to_gemini,
                    self.gemini_msg_count,
                )
                continue

            self._log_event(gemini.describe_response(response))

            if getattr(response, "go_away", None) is not None:
                self.log.warning("Server sent GoAway: %s", response.go_away)

            # ---- Tool calls: Gemini pressing the "end call" button ----
            if await gemini.handle_tool_calls(self.session, response, self.log):
                if await self._on_end_call():
                    return

            server_content = response.server_content
            if server_content is None:
                continue

            # ---- Caller transcript (arrives in fragments) ----
            transcription = server_content.input_transcription
            if transcription and transcription.text:
                self.latency.mark_caller_activity()
                self.caller_turn_text += transcription.text
                self.log.info("Caller: %s", transcription.text.strip())

                if not self.hangup_pending and is_explicit_hangup_request(
                    self.caller_turn_text
                ):
                    self.log.info(
                        "Hang-up request detected: %s. Letting Gemini say goodbye.",
                        self.caller_turn_text.strip(),
                    )
                    self._arm_hangup()

            # ---- Gemini's own speech transcript ----
            output_text = server_content.output_transcription
            if output_text and output_text.text:
                self.log.info("Agent: %s", output_text.text)

            # ---- Barge-in: caller spoke over the AI ----
            if server_content.interrupted:
                self.log.info("Caller interrupted the AI.")
                out_resampler.reset()
                await sender.clear()
                self.cur_audio_bytes = 0
                self.cur_audio_started = None
                reply_latency_logged = False

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

                    if not reply_latency_logged:
                        reply_latency_logged = True
                        latency_ms = self.latency.first_audio()
                        if latency_ms is not None:
                            self.log.info("latency_ms=%d (first reply audio)", latency_ms)

                    if self.cur_audio_started is None:
                        self.cur_audio_started = time.monotonic()
                    self.cur_audio_bytes += len(converted)

                    if self.hangup_pending:
                        if self.goodbye_started_at is None:
                            self.goodbye_started_at = time.monotonic()
                        self.goodbye_bytes += len(converted)

                    await sender.send(converted)

            # ---- End of Gemini's turn ----
            if server_content.turn_complete:
                tail = out_resampler.flush()
                if tail:
                    if self.hangup_pending:
                        self.goodbye_bytes += len(tail)
                    await sender.send(tail)
                await sender.flush()

                self.caller_turn_text = ""

                if self._greeting_active:
                    self._greeting_active = False
                    self.log.info("Greeting finished; caller audio is now live.")

                # Remember this turn's audio so a later end_call can time the goodbye.
                if tail:
                    self.cur_audio_bytes += len(tail)
                if self.cur_audio_bytes > 0:
                    self.last_audio_bytes = self.cur_audio_bytes
                    self.last_audio_started = self.cur_audio_started
                    self.last_turn_complete_at = time.monotonic()
                self.cur_audio_bytes = 0
                self.cur_audio_started = None
                reply_latency_logged = False

                self.log.info("Response completed (%d audio chunks).", audio_chunks)
                audio_chunks = 0

                # Only finish once actual goodbye audio has been sent.
                if self.hangup_pending and self.goodbye_bytes > 0:
                    await self._finish_goodbye()
                    return
