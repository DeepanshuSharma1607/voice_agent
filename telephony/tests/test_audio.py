import numpy as np

from voice_agent.audio import StreamResampler, pcm16_rms


def _tone(rate: int, seconds: float) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * 440 * t) * 8000).astype("<i2").tobytes()


def test_passthrough_keeps_bytes_and_carries_odd_byte():
    r = StreamResampler(16000, 16000)
    assert r.process(b"\x01\x00\x02") == b"\x01\x00"
    assert r.process(b"\x00") == b"\x02\x00"
    assert r.flush() == b""


def test_empty_input_returns_empty():
    assert StreamResampler(8000, 16000).process(b"") == b""


def test_upsample_length_is_about_double():
    r = StreamResampler(8000, 16000)
    data = _tone(8000, 1.0)  # 16000 bytes in
    out = b"".join(r.process(data[i : i + 640]) for i in range(0, len(data), 640))
    out += r.flush()
    assert abs(len(out) - 2 * len(data)) <= 0.03 * 2 * len(data)


def test_downsample_length_is_about_two_thirds():
    r = StreamResampler(24000, 16000)
    data = _tone(24000, 1.0)  # 48000 bytes in
    out = b"".join(r.process(data[i : i + 960]) for i in range(0, len(data), 960))
    out += r.flush()
    assert abs(len(out) - 32000) <= 0.03 * 32000


def test_odd_chunk_sizes_do_not_crash():
    r = StreamResampler(8000, 16000)
    data = _tone(8000, 0.2)
    out = b"".join(r.process(data[i : i + 321]) for i in range(0, len(data), 321))
    assert len(out) % 2 == 0


def test_rms():
    assert pcm16_rms(b"") == 0.0
    assert pcm16_rms(b"\x00\x00" * 100) == 0.0
    assert pcm16_rms(_tone(8000, 0.1)) > 1000
