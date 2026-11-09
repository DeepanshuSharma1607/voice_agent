"""Persist one human-readable transcript file for each Exotel call."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _safe_call_no(value: str | None) -> str:
    """Make an Exotel call/stream identifier safe to use in a filename."""
    raw = (value or "unknown").strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")
    return (safe or "unknown")[:120]


def _resolve_directory(directory: str | Path) -> Path:
    """Resolve relative transcript paths from the project root, not the shell CWD."""
    path = Path(directory).expanduser()
    if not path.is_absolute():
        project_root = Path(__file__).resolve().parents[1]
        path = project_root / path
    return path


def save_call_transcript(
    *,
    call_sid: str | None,
    stream_sid: str,
    started_at: datetime | None,
    ended_at: datetime | None,
    entries: Iterable[tuple[str, str]],
    directory: str | Path = "call_transcripts",
) -> Path:
    """Write a call transcript and return its path.

    Exotel's call SID is the preferred call number. The stream SID is used when
    Exotel does not provide a call SID. Files are written atomically so an
    interrupted write does not leave a partially-written final transcript.
    """
    call_no = call_sid or stream_sid or "unknown"
    safe_call_no = _safe_call_no(call_no)
    output_dir = _resolve_directory(directory)
    output_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "CALL TRANSCRIPT",
        "=" * 72,
        f"Call No: {call_no}",
        f"Call SID: {call_sid or 'not provided'}",
        f"Stream SID: {stream_sid or 'not provided'}",
        f"Started (UTC): {(started_at or datetime.now(timezone.utc)).isoformat()}",
        f"Ended (UTC): {(ended_at or datetime.now(timezone.utc)).isoformat()}",
        "",
        "CONVERSATION",
        "-" * 72,
    ]

    saved_entries = 0
    for speaker, text in entries:
        content = " ".join((text or "").split())
        if not content:
            continue
        lines.extend([f"{speaker}:", content, ""])
        saved_entries += 1

    if saved_entries == 0:
        lines.append(
            "No transcription text was received for this call. "
            "Check Gemini Live input/output transcription events and API configuration."
        )

    target = output_dir / f"call_no_{safe_call_no}.txt"
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    temporary.replace(target)
    return target
