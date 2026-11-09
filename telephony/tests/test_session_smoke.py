"""End-to-end smoke test of CallSession with a fake Gemini (no network)."""

from types import SimpleNamespace

import numpy as np
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from voice_agent import gemini
from voice_agent.config import Settings
from voice_agent.session import CallSession


def _audio_response():
    pcm = (np.sin(np.linspace(0, 200, 4800)) * 6000).astype("<i2").tobytes()  # 0.2 s @ 24 kHz
    part = SimpleNamespace(inline_data=SimpleNamespace(data=pcm), text=None)
    return _server_content(
        model_turn=SimpleNamespace(parts=[part]), output_text="Hello there, how can I help?"
    )


def _server_content(model_turn=None, turn_complete=False, output_text=None):
    return SimpleNamespace(
        server_content=SimpleNamespace(
            input_transcription=None,
            output_transcription=(SimpleNamespace(text=output_text) if output_text else None),
            interrupted=False,
            model_turn=model_turn,
            turn_complete=turn_complete,
        )
    )


class FakeSession:
    def __init__(self):
        self.sent_text = []
        self.tool_responses = []
        self._round = 0

    async def send_realtime_input(self, **kwargs):
        if "text" in kwargs:
            self.sent_text.append(kwargs["text"])

    async def send_tool_response(self, **kwargs):
        self.tool_responses.append(kwargs)

    async def receive(self):
        import asyncio

        self._round += 1
        if self._round == 1:  # the greeting turn
            yield _audio_response()
            yield _server_content(turn_complete=True)
        elif self._round == 2:  # then the model ends the call
            yield SimpleNamespace(
                server_content=None,
                tool_call=SimpleNamespace(function_calls=[SimpleNamespace(name="end_call", id="1")]),
            )
        else:
            await asyncio.sleep(0.05)


def test_greeting_then_end_call_hangs_up(monkeypatch, tmp_path):
    fake = FakeSession()

    async def fake_connect(stack, client, settings):
        return fake

    monkeypatch.setattr(gemini, "connect", fake_connect)

    settings = Settings(
        gemini_api_key="k",
        gemini_model="m",
        exotel_ws_url="wss://x",
        playback_margin_seconds=0.05,
        goodbye_timeout_seconds=5.0,
        transcript_dir=str(tmp_path),
    )
    app = FastAPI()

    @app.websocket("/exotel/media")
    async def route(websocket: WebSocket):
        await websocket.accept()
        await CallSession(websocket, settings, client=None).run()

    events = []
    with TestClient(app).websocket_connect("/exotel/media") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(
            {
                "event": "start",
                "start": {"stream_sid": "S1", "call_sid": "CALL123", "media_format": {"sample_rate": 8000}},
            }
        )
        try:
            while True:
                events.append(ws.receive_json())
        except Exception:
            pass  # server closed the socket = hang-up

    assert fake.sent_text == [settings.greeting_trigger]   # greeting was sent from code
    assert any(e["event"] == "media" for e in events)      # greeting audio reached Exotel
    assert fake.tool_responses                              # end_call was answered
    transcript = tmp_path / "call_no_CALL123.txt"
    assert transcript.exists()                             # each started call is persisted
    content = transcript.read_text(encoding="utf-8")
    assert "Call No: CALL123" in content
    assert "Agent:\nHello there, how can I help?" in content
