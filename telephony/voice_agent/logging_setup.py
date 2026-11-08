"""Logging: one format everywhere, with the call SID on every call-related line."""

from __future__ import annotations

import logging


class _CallFormatter(logging.Formatter):
    """Adds a [call] field; shows '-' for lines not tied to a call."""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "call"):
            record.call = "-"
        return super().format(record)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        _CallFormatter(
            fmt="%(asctime)s %(levelname)-7s [%(call)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    package_logger = logging.getLogger("voice_agent")
    package_logger.handlers.clear()
    package_logger.addHandler(handler)
    package_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    package_logger.propagate = False


def call_logger(name: str, call_id: str = "-") -> logging.LoggerAdapter:
    """Logger whose lines carry the given call id (mutable via adapter.extra['call'])."""
    return logging.LoggerAdapter(logging.getLogger(name), {"call": call_id})
