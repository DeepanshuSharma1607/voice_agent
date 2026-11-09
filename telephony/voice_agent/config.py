"""Settings: .env discovery, validation, and tunables (all overridable via .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from .prompts import DEFAULT_GREETING_TRIGGER

REQUIRED_KEYS = ("GEMINI_API_KEY", "GEMINI_LIVE_MODEL", "EXOTEL_WS_URL")


@dataclass(frozen=True)
class Settings:
    # Required
    gemini_api_key: str
    gemini_model: str
    exotel_ws_url: str

    # Optional
    env_path: Path | None = None
    exotel_ws_token: str | None = None   # if set, /exotel/media requires ?token=<value>
    voice_name: str = "Puck"
    greeting_trigger: str = DEFAULT_GREETING_TRIGGER
    vad_silence_ms: int = 600            # end-of-speech silence; 0 = use Gemini default
    greeting_protect_seconds: float = 4.0  # ignore caller audio while greeting plays; 0 = off

    # Timing
    goodbye_timeout_seconds: float = 10.0
    max_playback_wait_seconds: float = 15.0
    playback_margin_seconds: float = 0.5
    idle_warn_seconds: float = 10.0
    recent_turn_seconds: float = 5.0
    start_event_timeout_seconds: float = 30.0

    # Diagnostics
    raw_responses_to_log: int = 5
    log_level: str = "INFO"
    transcript_dir: str = "call_transcripts"


def find_env_file(start: Path | None = None) -> Path | None:
    """Search the start path's folders, then every parent folder, for a .env file."""
    origin = (start or Path(__file__)).resolve()
    for folder in origin.parents:
        candidate = folder / ".env"
        if candidate.is_file():
            return candidate
    return None


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _env_number(name: str, default: float, cast):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env_path = find_env_file()
    if env_path is not None:
        load_dotenv(dotenv_path=env_path, override=True)

    values = {key: os.getenv(key, "").strip() for key in REQUIRED_KEYS}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required .env keys: {', '.join(missing)}")

    return Settings(
        gemini_api_key=values["GEMINI_API_KEY"],
        gemini_model=values["GEMINI_LIVE_MODEL"],
        exotel_ws_url=values["EXOTEL_WS_URL"],
        env_path=env_path,
        exotel_ws_token=os.getenv("EXOTEL_WS_TOKEN", "").strip() or None,
        voice_name=_env_str("VOICE_NAME", "Puck"),
        greeting_trigger=_env_str("GREETING_TRIGGER", DEFAULT_GREETING_TRIGGER),
        vad_silence_ms=_env_number("VAD_SILENCE_MS", 600, int),
        greeting_protect_seconds=_env_number("GREETING_PROTECT_SECONDS", 4.0, float),
        goodbye_timeout_seconds=_env_number("GOODBYE_TIMEOUT_SECONDS", 10.0, float),
        max_playback_wait_seconds=_env_number("MAX_PLAYBACK_WAIT_SECONDS", 15.0, float),
        playback_margin_seconds=_env_number("PLAYBACK_MARGIN_SECONDS", 0.5, float),
        idle_warn_seconds=_env_number("IDLE_WARN_SECONDS", 10.0, float),
        recent_turn_seconds=_env_number("RECENT_TURN_SECONDS", 5.0, float),
        start_event_timeout_seconds=_env_number("START_EVENT_TIMEOUT_SECONDS", 30.0, float),
        raw_responses_to_log=_env_number("RAW_RESPONSES_TO_LOG", 5, int),
        log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        transcript_dir=_env_str("CALL_TRANSCRIPT_DIR", "call_transcripts"),
    )
