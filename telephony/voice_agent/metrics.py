"""Latency tracking: how long the caller waits before the agent starts speaking."""

from __future__ import annotations

import time


class LatencyTracker:
    """Approximate response latency.

    Reference points: the moment the greeting trigger is sent, or the latest
    caller transcript fragment. ``first_audio()`` returns the milliseconds since
    that reference, once per reply, then clears it.
    """

    def __init__(self) -> None:
        self._reference: float | None = None

    def mark_greeting_sent(self) -> None:
        self._reference = time.monotonic()

    def mark_caller_activity(self) -> None:
        self._reference = time.monotonic()

    def first_audio(self) -> int | None:
        if self._reference is None:
            return None
        elapsed_ms = int((time.monotonic() - self._reference) * 1000)
        self._reference = None
        return elapsed_ms
