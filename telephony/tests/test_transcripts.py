from datetime import datetime, timezone

from voice_agent.transcripts import save_call_transcript


def test_saves_one_text_file_per_call(tmp_path):
    now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    path = save_call_transcript(
        call_sid="CA123456",
        stream_sid="MZ987654",
        started_at=now,
        ended_at=now,
        entries=[("Caller", "Hello there."), ("Agent", "Hi! How can I help?")],
        directory=tmp_path,
    )

    assert path.name == "call_no_CA123456.txt"
    content = path.read_text(encoding="utf-8")
    assert "Call No: CA123456" in content
    assert "Stream SID: MZ987654" in content
    assert "Caller:\nHello there." in content
    assert "Agent:\nHi! How can I help?" in content


def test_uses_stream_sid_when_call_sid_is_missing(tmp_path):
    path = save_call_transcript(
        call_sid=None,
        stream_sid="stream/with unsafe chars",
        started_at=None,
        ended_at=None,
        entries=[],
        directory=tmp_path,
    )

    assert path.name == "call_no_stream_with_unsafe_chars.txt"
    assert "No transcription text was received" in path.read_text(encoding="utf-8")
