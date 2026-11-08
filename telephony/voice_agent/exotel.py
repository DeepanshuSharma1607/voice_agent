"""Exotel Voicebot protocol: parse incoming events, send audio packets back."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

EXOTEL_OUTPUT_PACKET_SIZE = 3200   # Exotel minimum packet size; multiple of 320 bytes
SUPPORTED_SAMPLE_RATES = (8000, 16000, 24000)
DEFAULT_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class StartInfo:
    stream_sid: str
    call_sid: str | None
    sample_rate: int
    media_format: dict[str, Any]


def parse_message(raw: str) -> dict[str, Any] | None:
    """Parse one WebSocket text frame; None if it is not a JSON object."""
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return message if isinstance(message, dict) else None


def parse_start(message: dict[str, Any]) -> StartInfo:
    """Extract call details from Exotel's 'start' event. Raises on bad data."""
    start = message.get("start", {}) or {}
    stream_sid = start.get("stream_sid") or message.get("stream_sid")
    media_format = start.get("media_format", {}) or {}
    sample_rate = int(media_format.get("sample_rate", DEFAULT_SAMPLE_RATE))

    if sample_rate not in SUPPORTED_SAMPLE_RATES:
        raise ValueError(f"Unsupported Exotel sample rate: {sample_rate}")
    if not stream_sid:
        raise RuntimeError("Exotel did not provide a stream SID.")

    return StartInfo(
        stream_sid=stream_sid,
        call_sid=start.get("call_sid"),
        sample_rate=sample_rate,
        media_format=media_format,
    )


def decode_media(message: dict[str, Any]) -> bytes:
    """Return the raw PCM bytes of a 'media' event (b'' if missing/invalid)."""
    payload = (message.get("media") or {}).get("payload")
    if not payload:
        return b""
    try:
        return base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        return b""


class ExotelAudioSender:
    """Buffers PCM and sends it to Exotel in correctly sized base64 packets."""

    def __init__(self, websocket: WebSocket, stream_sid: str) -> None:
        self._websocket = websocket
        self._stream_sid = stream_sid
        self._buffer = bytearray()

    async def _send_packet(self, packet: bytes) -> None:
        await self._websocket.send_json(
            {
                "event": "media",
                "stream_sid": self._stream_sid,
                "media": {"payload": base64.b64encode(packet).decode("ascii")},
            }
        )

    async def send(self, pcm: bytes) -> None:
        """Queue PCM and send every full packet."""
        self._buffer.extend(pcm)
        while len(self._buffer) >= EXOTEL_OUTPUT_PACKET_SIZE:
            packet = bytes(self._buffer[:EXOTEL_OUTPUT_PACKET_SIZE])
            del self._buffer[:EXOTEL_OUTPUT_PACKET_SIZE]
            await self._send_packet(packet)

    async def flush(self) -> None:
        """Send the last partial packet, padded with silence to the minimum size."""
        if not self._buffer:
            return
        remaining = bytes(self._buffer)
        self._buffer.clear()
        if len(remaining) < EXOTEL_OUTPUT_PACKET_SIZE:
            remaining += b"\x00" * (EXOTEL_OUTPUT_PACKET_SIZE - len(remaining))
        await self._send_packet(remaining)

    async def clear(self) -> None:
        """Barge-in: drop queued audio and tell Exotel to stop playing."""
        self._buffer.clear()
        await self._websocket.send_json(
            {"event": "clear", "stream_sid": self._stream_sid}
        )
