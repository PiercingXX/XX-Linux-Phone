"""Step sampling pure logic: rebase + derived metric mapping.

Port of data/StepReader.kt (rebase) and data/StepMetricMapper.kt. No sensor
or platform I/O; the collector service injects readings.
"""

from __future__ import annotations

from dataclasses import dataclass

from xx_vitals.core import metrics

REBOOT_CLOCK_TOLERANCE_MS = 5 * 60 * 1000
NEAR_ZERO_STEPS = 50.0
NEAR_ZERO_BASELINE = 1000.0
STUCK_ZERO_ELAPSED_MS = 6 * 60 * 60 * 1000


@dataclass(frozen=True)
class StepReading:
    counter_value: float
    elapsed_realtime_ms: int
    wall_clock_ms: int


@dataclass(frozen=True)
class StepDelta:
    steps: int
    baseline: StepReading | None
    rebooted: bool


@dataclass(frozen=True)
class StepMetrics:
    distance_m: float | None
    calories_kcal: float | None
    active_min: int
    effort_points: int


def _boot_clock_ms(reading: StepReading) -> int:
    return reading.wall_clock_ms - reading.elapsed_realtime_ms


def counter_looks_reset(previous: float, current: float) -> bool:
    if current < previous:
        return True
    return previous >= NEAR_ZERO_BASELINE and current <= NEAR_ZERO_STEPS


def looks_blocked(counter_value: float, elapsed_realtime_ms: int) -> bool:
    return counter_value == 0.0 and elapsed_realtime_ms > STUCK_ZERO_ELAPSED_MS


def rebase(last: StepReading | None, now: StepReading) -> StepDelta:
    if last is None:
        return StepDelta(steps=0, baseline=now, rebooted=False)
    elapsed_went_backwards = now.elapsed_realtime_ms < last.elapsed_realtime_ms
    boot_jump = abs(_boot_clock_ms(now) - _boot_clock_ms(last)) > REBOOT_CLOCK_TOLERANCE_MS
    counter_reset = counter_looks_reset(last.counter_value, now.counter_value)
    rebooted = elapsed_went_backwards or (boot_jump and counter_reset)
    counter_went_backwards = now.counter_value < last.counter_value
    if rebooted:
        return StepDelta(steps=int(now.counter_value), baseline=now, rebooted=True)
    if counter_went_backwards:
        return StepDelta(steps=0, baseline=now, rebooted=True)
    return StepDelta(
        steps=int(now.counter_value - last.counter_value), baseline=now, rebooted=False
    )


def map_steps(
    steps: int, per_minute_steps: list[float], height_cm: float, weight_kg: float
) -> StepMetrics:
    total = max(0.0, float(steps))
    if per_minute_steps:
        active = metrics.active_minutes(per_minute_steps).value
        effort = sum(int(metrics.effort_from_cadence(v).value) for v in per_minute_steps)
    else:
        active = metrics.active_minutes_from_total(total).value
        effort = metrics.effort_from_total(total).value
    return StepMetrics(
        distance_m=metrics.distance(total, height_cm).value,
        calories_kcal=metrics.calories(total, weight_kg, height_cm).value,
        active_min=active,
        effort_points=effort,
    )


def distance_m(steps: int, height_cm: float) -> float:
    return metrics.distance(max(0.0, float(steps)), height_cm).value


def calories_kcal(steps: int, weight_kg: float, height_cm: float) -> float:
    return metrics.calories(max(0.0, float(steps)), weight_kg, height_cm).value
