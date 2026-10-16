"""Pure core ports: Derive, Derived, MetricMath, GoalModel, UnitConvert.

Ports `:core` (Derive.kt, Derived.kt, MetricMath.kt, GoalModel.kt,
UnitConvert.kt). No I/O, no GI, no database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MODERATE_CADENCE = 100.0
VIGOROUS_CADENCE = 130.0
STRIDE_FRACTION_OF_HEIGHT = 0.415
CM_PER_METRE = 100.0
WALKING_MET = 3.5
WALKING_SPEED_MS = 1.4
SECONDS_PER_HOUR = 3600.0
ACTIVE_MINUTE_FLOOR = 60.0

KG_PER_LB = 0.45359237
METRES_PER_MILE = 1609.344
METRES_PER_KM = 1000.0

DEFAULT_STEPS = 10_000
DEFAULT_EFFORT = 90
DEFAULT_WEEKLY_EFFORT = 150


class Derived:
    """Marks a value as derived rather than measured; str() carries `~`."""

    __slots__ = ("value",)

    def __init__(self, value) -> None:
        self.value = value

    def __str__(self) -> str:
        return f"~{self.value}"

    def __repr__(self) -> str:
        return f"Derived({self.value!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Derived) and other.value == self.value


def effort_from_cadence(steps_per_minute: float) -> Derived:
    if steps_per_minute < MODERATE_CADENCE:
        return Derived(0.0)
    if steps_per_minute < VIGOROUS_CADENCE:
        return Derived(1.0)
    return Derived(2.0)


def distance(steps: float, height_cm: float) -> Derived:
    stride_cm = STRIDE_FRACTION_OF_HEIGHT * height_cm
    return Derived(steps * stride_cm / CM_PER_METRE)


def calories(steps: float, weight_kg: float, height_cm: float) -> Derived:
    metres = distance(steps, height_cm).value
    hours = metres / (WALKING_SPEED_MS * SECONDS_PER_HOUR)
    return Derived(WALKING_MET * weight_kg * hours)


def active_minutes(per_minute_steps: list[float]) -> Derived:
    return Derived(sum(1 for s in per_minute_steps if s >= ACTIVE_MINUTE_FLOOR))


def active_minutes_from_total(steps: float) -> Derived:
    s = max(0.0, steps)
    return Derived(int(math.floor(s / MODERATE_CADENCE)))


def effort_from_total(steps: float) -> Derived:
    return Derived(active_minutes_from_total(steps).value)


def ring_fraction(actual: float, target: float) -> float:
    if target <= 0.0:
        return 1.0 if actual > 0.0 else 0.0
    return min(max(actual / target, 0.0), 1.0)


def goal_met(actual: float, target: float) -> bool:
    return actual >= target


def streak_count(days_met: list[bool]) -> int:
    streak = 0
    for met in reversed(days_met):
        if not met:
            break
        streak += 1
    return streak


def weekly_target_progress(actual: float, target: float) -> float:
    if target <= 0.0:
        return 1.0 if actual > 0.0 else 0.0
    return actual / target


def weight_trend_30d(weights_kg: list[float]) -> float:
    if len(weights_kg) < 2:
        return 0.0
    return weights_kg[-1] - weights_kg[0]


def kg_to_lb(kg: float) -> float:
    return kg / KG_PER_LB


def lb_to_kg(lb: float) -> float:
    return lb * KG_PER_LB


def metres_to_miles(metres: float) -> float:
    return metres / METRES_PER_MILE


def miles_to_metres(miles: float) -> float:
    return miles * METRES_PER_MILE


def metres_to_km(metres: float) -> float:
    return metres / METRES_PER_KM


def km_to_metres(km: float) -> float:
    return km * METRES_PER_KM


@dataclass(frozen=True)
class Goals:
    steps: int = DEFAULT_STEPS
    effort: int = DEFAULT_EFFORT
    weekly_effort: int = DEFAULT_WEEKLY_EFFORT


DEFAULT_GOALS = Goals()


def override_goals(
    steps: int | None = None,
    effort: int | None = None,
    weekly_effort: int | None = None,
) -> Goals:
    if steps is not None and steps <= 0:
        raise ValueError(f"steps goal must be positive, was {steps}")
    if effort is not None and effort <= 0:
        raise ValueError(f"effort goal must be positive, was {effort}")
    if weekly_effort is not None and weekly_effort <= 0:
        raise ValueError(f"weeklyEffort goal must be positive, was {weekly_effort}")
    return Goals(
        steps=steps if steps is not None else DEFAULT_STEPS,
        effort=effort if effort is not None else DEFAULT_EFFORT,
        weekly_effort=weekly_effort if weekly_effort is not None else DEFAULT_WEEKLY_EFFORT,
    )
