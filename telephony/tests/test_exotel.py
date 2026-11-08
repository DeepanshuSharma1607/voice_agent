import asyncio
import base64

import pytest

from voice_agent import exotel


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


def test_parse_message():
    assert exotel.parse_message('{"event": "media"}') == {"event": "media"}
    assert exotel.parse_message("not json") is None
    assert exotel.parse_message("[1, 2]") is None


def test_parse_start_ok():
    msg = {
        "event": "start",
        "start": {"stream_sid": "S1", "call_sid": "C1", "media_format": {"sample_rate": 8000}},
    }
    info = exotel.parse_start(msg)
    assert (info.stream_sid, info.call_sid, info.sample_rate) == ("S1", "C1", 8000)


def test_parse_start_defaults_and_errors():
    assert exotel.parse_start({"start": {"stream_sid": "S"}}).sample_rate == 16000
    with pytest.raises(ValueError):
        exotel.parse_start({"start": {"stream_sid": "S", "media_format": {"sample_rate": 44100}}})
    with pytest.raises(RuntimeError):
        exotel.parse_start({"start": {}})


def test_decode_media():
    payload = base64.b64encode(b"\x01\x02").decode()
    assert exotel.decode_media({"media": {"payload": payload}}) == b"\x01\x02"
    assert exotel.decode_media({"media": {}}) == b""
    assert exotel.decode_media({}) == b""


def test_sender_packets_flush_and_clear():
    async def scenario():
        ws = FakeWebSocket()
        sender = exotel.ExotelAudioSender(ws, "S1")
        size = exotel.EXOTEL_OUTPUT_PACKET_SIZE

        await sender.send(b"\x01" * (size + 100))
        assert len(ws.sent) == 1  # one full packet, 100 bytes still buffered

        await sender.flush()
        assert len(ws.sent) == 2
        last = base64.b64decode(ws.sent[1]["media"]["payload"])
        assert len(last) == size and last[:100] == b"\x01" * 100 and last[100:] == b"\x00" * (size - 100)

        await sender.send(b"\x01" * 50)
        await sender.clear()
        assert ws.sent[-1] == {"event": "clear", "stream_sid": "S1"}
        await sender.flush()  # nothing left to flush
        assert ws.sent[-1]["event"] == "clear"

    asyncio.run(scenario())
