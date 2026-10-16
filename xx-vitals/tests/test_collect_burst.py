"""Burst stride filter: still stays zero, walking counts, floor relearns."""

import math
import random

from xx_vitals.collect import burst


def _still(count=500, seed=7, hiss=0.02):
    rng = random.Random(seed)
    return [(0.0, 0.0, 9.81 + hiss * rng.gauss(0, 1)) for _ in range(count)]


def _walk(count=500, seed=7, freq_hz=2.0, amplitude=1.2, hiss=0.05):
    rng = random.Random(seed)
    out = []
    for index in range(count):
        sway = amplitude * math.sin(2 * math.pi * freq_hz * index / 50.0)
        out.append((0.0, 0.0, 9.81 + sway + hiss * rng.gauss(0, 1)))
    return out


def test_still_burst_counts_zero():
    samples = _still()
    floor = burst.learn_noise_floor(samples)
    assert burst.count_burst_steps(samples, 50.0, floor) == 0


def test_walking_burst_counts_strides():
    samples = _walk()
    floor = burst.learn_noise_floor(_still())
    counted = burst.count_burst_steps(samples, 50.0, floor)
    assert 15 <= counted <= 25


def test_floor_learned_while_walking_still_detects():
    walking = _walk()
    floor = burst.learn_noise_floor(walking)
    assert burst.MIN_NOISE_FLOOR <= floor <= burst.MAX_NOISE_FLOOR
    assert 15 <= burst.count_burst_steps(walking, 50.0, floor) <= 25
    assert burst.count_burst_steps(_still(), 50.0, floor) == 0


def test_floor_clamps_to_bounds():
    assert burst.learn_noise_floor([]) == burst.DEFAULT_NOISE_FLOOR
    flat = [(0.0, 0.0, 9.81)] * 500
    floor = burst.learn_noise_floor(flat)
    assert floor == burst.MIN_NOISE_FLOOR
    wild = [(float(i % 7), float(-i % 5), float(i % 11)) for i in range(500)]
    assert burst.MIN_NOISE_FLOOR <= burst.learn_noise_floor(wild) <= burst.MAX_NOISE_FLOOR


def test_short_and_empty_bursts_count_zero():
    floor = burst.learn_noise_floor(_still())
    assert burst.count_burst_steps([], 50.0, floor) == 0
    assert burst.count_burst_steps(_walk()[:20], 50.0, floor) == 0
    assert burst.count_burst_steps(_walk(), 0.0, floor) == 0


def test_synthetic_walking_trace_counts_within_five_percent():
    for seed in (7, 11, 42):
        floor = burst.learn_noise_floor(_still(seed=seed))
        for freq_hz, expected in ((1.0, 10), (1.5, 15), (2.0, 20), (2.5, 25)):
            counted = burst.count_burst_steps(
                _walk(seed=seed, freq_hz=freq_hz), 50.0, floor
            )
            assert abs(counted - expected) / expected <= 0.05, (
                seed, freq_hz, counted, expected
            )


def test_refractory_merges_plateau_peaks():
    floor = burst.MIN_NOISE_FLOOR
    plateau = [(0.0, 0.0, 9.81 + 1.0)] * 500
    assert burst.count_burst_steps(plateau, 50.0, floor) == 0
