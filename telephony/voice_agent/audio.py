"""Audio helpers: PCM16 conversion, level meter, and a stateful streaming resampler."""

from __future__ import annotations

import numpy as np
import soxr

GEMINI_INPUT_RATE = 16000   # Gemini Live expects 16 kHz PCM16 input
GEMINI_OUTPUT_RATE = 24000  # Gemini Live returns 24 kHz PCM16 output


def to_pcm16(samples: np.ndarray) -> bytes:
    """float32 samples in [-1, 1] -> little-endian PCM16 bytes."""
    if samples.size == 0:
        return b""
    return np.clip(np.round(samples * 32768.0), -32768, 32767).astype("<i2").tobytes()


def pcm16_rms(pcm: bytes) -> float:
    """RMS level of PCM16 audio in int16 units (silence ~0, speech hundreds+)."""
    usable = len(pcm) - (len(pcm) % 2)
    if usable == 0:
        return 0.0
    samples = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples)))


class StreamResampler:
    """Mono PCM16 little-endian streaming resampler with odd-byte carry-over.

    Stateful, so consecutive chunks join without clicks.
    """

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
        return to_pcm16(self._stream.resample_chunk(samples))

    def flush(self) -> bytes:
        """Drain the resampler's tail and start a fresh stream."""
        if self.passthrough:
            return b""
        tail = self._stream.resample_chunk(np.zeros(0, dtype=np.float32), last=True)
        self.reset()
        return to_pcm16(tail)

    def reset(self) -> None:
        self._carry = b""
        self._stream = self._new_stream()
