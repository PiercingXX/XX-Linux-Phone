"""Accelerometer burst step detection: pure stride filter, no I/O.

A collector tick samples the phone accelerometer for ~10 s at ~50 Hz
(PORT_SPEC.md section 3, lane 4) and counts walking strides here. The
filter is peak detection on the acceleration magnitude with a per-device
noise floor learned on the first run: a local maximum counts only when it
clears the floor and sits far enough from the previous stride. Still
periods and sensor hiss stay at zero; nothing is ever synthesized.

Units are whatever the sensor reports (normally m/s^2); only differences
from the burst mean matter, so gravity never needs a constant.
"""

from __future__ import annotations

import math

BURST_SECONDS = 10.0
SAMPLE_HZ = 50.0
MIN_STEP_INTERVAL_S = 0.30
MIN_NOISE_FLOOR = 0.25
MAX_NOISE_FLOOR = 1.50
DEFAULT_NOISE_FLOOR = 0.55
SMOOTH_WINDOW = 3


def magnitudes(samples: list[tuple[float, float, float]]) -> list[float]:
    """Acceleration magnitudes for one burst."""
    return [math.sqrt(x * x + y * y + z * z) for x, y, z in samples]


def learn_noise_floor(samples: list[tuple[float, float, float]]) -> float:
    """Learn the per-device still-noise floor from one burst.

    The floor is estimated from sample-to-sample differences, not from
    the spread around the mean: the first run may happen while walking,
    and the stride itself must never become the "noise" that blinds the
    filter. Differencing keeps the slow stride while exposing the fast
    hiss, so a walking first burst still learns a still-like floor.
    Clamped to [MIN_NOISE_FLOOR, MAX_NOISE_FLOOR].
    """
    mags = magnitudes(samples)
    if len(mags) < 8:
        return DEFAULT_NOISE_FLOOR
    diffs = [mags[index + 1] - mags[index] for index in range(len(mags) - 1)]
    mean = sum(diffs) / len(diffs)
    variance = sum((value - mean) ** 2 for value in diffs) / len(diffs)
    floor = 1.5 * math.sqrt(variance) / math.sqrt(2.0)
    return min(max(floor, MIN_NOISE_FLOOR), MAX_NOISE_FLOOR)


def _smooth(deviation: list[float]) -> list[float]:
    width = SMOOTH_WINDOW
    if len(deviation) < width:
        return list(deviation)
    half = width // 2
    out: list[float] = []
    for index in range(len(deviation)):
        lo = max(0, index - half)
        hi = min(len(deviation), index + half + 1)
        out.append(sum(deviation[lo:hi]) / (hi - lo))
    return out


def count_burst_steps(
    samples: list[tuple[float, float, float]],
    sample_hz: float = SAMPLE_HZ,
    noise_floor: float = DEFAULT_NOISE_FLOOR,
) -> int:
    """Count strides in one accelerometer burst.

    Peak detection on the mean-removed magnitude: a smoothed local maximum
    above the noise floor counts when it follows the refractory gap
    (MIN_STEP_INTERVAL_S, about 200 steps/min ceiling) and the signal
    dips back below half the floor before the next peak, so a broad
    plateau never counts twice. Returns 0 for still, short, or empty
    bursts — never a guess.
    """
    if sample_hz <= 0 or len(samples) < int(sample_hz):
        return 0
    floor = max(float(noise_floor), MIN_NOISE_FLOOR)
    mags = magnitudes(samples)
    mean = sum(mags) / len(mags)
    shape = _smooth([value - mean for value in mags])
    min_gap = max(1, int(MIN_STEP_INTERVAL_S * sample_hz))
    steps = 0
    last_peak = -min_gap - 1
    dipped = True
    for index in range(1, len(shape) - 1):
        if shape[index] < floor * 0.5:
            dipped = True
        if not dipped:
            continue
        if index - last_peak <= min_gap:
            continue
        if shape[index] > floor and shape[index] >= shape[index - 1] and shape[index] > shape[index + 1]:
            steps += 1
            last_peak = index
            dipped = False
    return steps
