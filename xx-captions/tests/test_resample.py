from __future__ import annotations

import math
import struct

import pytest

from xx_captions import resample
from xx_captions.audio import ENGINE_RATE_HZ


def _sine(rate: int, seconds: float, freq: float = 440.0,
          channels: int = 1) -> bytes:
    frames = int(rate * seconds)
    out = bytearray()
    for i in range(frames):
        value = int(30000 * math.sin(2 * math.pi * freq * i / rate))
        for _ in range(channels):
            out += struct.pack("<h", value)
    return bytes(out)


def test_engine_rate_is_16k() -> None:
    assert ENGINE_RATE_HZ == 16000


def test_16k_mono_passthrough_is_exact() -> None:
    raw = _sine(16000, 0.5)
    got = resample.pcm16_to_mono_float32_16k(raw, 16000, 1)
    assert len(got) == 8000
    assert got[0] == pytest.approx(0.0, abs=1 / 32768)


def test_48k_stereo_downmixes_to_16k_mono() -> None:
    raw = _sine(48000, 1.0, channels=2)
    got = resample.pcm16_to_mono_float32_16k(raw, 48000, 2)
    assert len(got) == 16000
    assert all(-1.0 <= v <= 1.0 for v in got)


def test_44100_mono_length() -> None:
    raw = _sine(44100, 1.0)
    assert len(resample.pcm16_to_mono_float32_16k(raw, 44100, 1)) == 16000


def test_8k_upsamples_to_16k() -> None:
    raw = _sine(8000, 1.0)
    assert len(resample.pcm16_to_mono_float32_16k(raw, 8000, 1)) == 16000


def test_sine_survives_48k_roundtrip() -> None:
    raw = _sine(48000, 1.0, freq=440.0, channels=2)
    got = resample.pcm16_to_mono_float32_16k(raw, 48000, 2)
    peak = max(abs(v) for v in got)
    assert peak == pytest.approx(30000 / 32768, rel=0.05)
    zero_crossings = sum(1 for a, b in zip(got, got[1:])
                         if a <= 0 < b)
    assert zero_crossings == pytest.approx(440, rel=0.05)


def test_downmix_averages_channels() -> None:
    assert resample.downmix_to_mono([1.0, -1.0, 0.5, 0.5], 2) == [0.0, 0.5]
    assert resample.downmix_to_mono([0.25, 0.5], 1) == [0.25, 0.5]


def test_empty_inputs_stay_empty() -> None:
    assert resample.pcm16_to_mono_float32_16k(b"", 44100, 2) == []
    assert resample.resample_mono_16k([], 48000) == []
    assert resample.downmix_to_mono([], 2) == []


def test_bad_inputs_raise() -> None:
    with pytest.raises(ValueError):
        resample.pcm16_to_mono_float32_16k(b"\x00", 16000, 1)
    with pytest.raises(ValueError):
        resample.pcm16_to_mono_float32_16k(b"\x00\x00", 0, 1)
    with pytest.raises(ValueError):
        resample.pcm16_to_mono_float32_16k(b"\x00\x00", 16000, 3)
    with pytest.raises(ValueError):
        resample.pcm16_to_mono_float32_16k(b"\x00\x00\x00\x00", 16000, 3)
    with pytest.raises(ValueError):
        resample.downmix_to_mono([1.0], 2)
    with pytest.raises(ValueError):
        resample.resample_mono_16k([0.0], -8000)


def test_pure_python_fallback_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _sine(44100, 0.2, channels=2)
    with_numpy = resample.pcm16_to_mono_float32_16k(raw, 44100, 2)
    monkeypatch.setattr(resample, "_np", None)
    pure = resample.pcm16_to_mono_float32_16k(raw, 44100, 2)
    assert len(pure) == len(with_numpy)
    for a, b in zip(pure[100:-100:37], with_numpy[100:-100:37]):
        assert a == pytest.approx(b, abs=0.02)
