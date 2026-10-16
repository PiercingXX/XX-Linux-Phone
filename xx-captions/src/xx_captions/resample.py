"""Capture-rate audio to engine-rate audio (pure, no hardware).

Capture may deliver 44.1/48 kHz stereo s16 while the engines consume
16 kHz mono float32. This module bridges the two with a numpy fast
path and a pure-Python fallback so phones without numpy still work.
"""
from __future__ import annotations

from collections.abc import Sequence

from xx_captions.audio import ENGINE_RATE_HZ

try:
    import numpy as _np
except ImportError:
    _np = None  # type: ignore[assignment]

def _check_rate(rate: int) -> int:
    if not isinstance(rate, int) or isinstance(rate, bool) or rate <= 0:
        raise ValueError(f"sample rate must be a positive int, got {rate!r}")
    return rate


def _check_channels(channels: int) -> int:
    if channels not in (1, 2):
        raise ValueError(f"channels must be 1 or 2, got {channels!r}")
    return channels


def downmix_to_mono(frames: Sequence[float], channels: int) -> list[float]:
    """Average interleaved stereo frames to mono; mono passes through."""
    _check_channels(channels)
    data = list(frames)
    if channels == 1:
        return [float(v) for v in data]
    if len(data) % 2:
        raise ValueError("stereo frame count must be even")
    return [(float(data[i]) + float(data[i + 1])) / 2.0
            for i in range(0, len(data), 2)]


def resample_mono_16k(mono: Sequence[float], in_rate: int) -> list[float]:
    """Linear-interpolate mono audio at in_rate to 16 kHz float."""
    _check_rate(in_rate)
    data = [float(v) for v in mono]
    if in_rate == ENGINE_RATE_HZ or not data:
        return data
    out_len = int(round(len(data) * ENGINE_RATE_HZ / in_rate))
    if out_len < 1:
        return []
    if _np is not None:
        src = _np.asarray(data, dtype=_np.float64)
        pos = _np.arange(out_len) * (len(data) - 1) / max(out_len - 1, 1)
        return _np.interp(pos, _np.arange(len(data)), src).tolist()
    scale = (len(data) - 1) / max(out_len - 1, 1)
    out: list[float] = []
    for i in range(out_len):
        pos = i * scale
        lo = int(pos)
        frac = pos - lo
        hi = lo + 1 if lo + 1 < len(data) else lo
        out.append(data[lo] * (1.0 - frac) + data[hi] * frac)
    return out


def pcm16_to_mono_float32_16k(raw: bytes, in_rate: int,
                              channels: int = 1) -> list[float]:
    """s16 capture bytes at in_rate to 16 kHz mono float32 in [-1, 1]."""
    _check_rate(in_rate)
    _check_channels(channels)
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("raw audio must be bytes")
    if len(raw) % 2:
        raise ValueError("s16 audio must have an even byte count")
    frames = len(raw) // 2
    if frames % channels:
        raise ValueError("frame count must be a multiple of channels")
    if _np is not None:
        pcm = _np.frombuffer(bytearray(raw), dtype=_np.int16).astype(_np.float64)
        mono = pcm if channels == 1 else (pcm[0::2] + pcm[1::2]) / 2.0
        return resample_mono_16k((mono / 32768.0).tolist(), in_rate)
    import struct

    pcm = struct.unpack(f"<{frames}h", raw)
    scaled = [v / 32768.0 for v in pcm]
    return resample_mono_16k(downmix_to_mono(scaled, channels), in_rate)


__all__ = ["downmix_to_mono", "pcm16_to_mono_float32_16k", "resample_mono_16k"]
