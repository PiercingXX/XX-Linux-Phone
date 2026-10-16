"""Home tab presentation logic. Port of ui/home/HomeModel.kt (pure part).

Pure functions of the rows already in the local database: no I/O, no GI.
Gap days (no recorded values) render as gaps, never zero rings; values
computed from steps carry the `~` derived mark.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

DERIVED_PREFIX = "~"

COLUMN_DISTANCE = 0
COLUMN_ACTIVE_MIN = 1
COLUMN_EFFORT = 2
COLUMN_CALORIES = 3

BIT_DISTANCE = 1 << COLUMN_DISTANCE
BIT_ACTIVE_MIN = 1 << COLUMN_ACTIVE_MIN
BIT_EFFORT = 1 << COLUMN_EFFORT
BIT_CALORIES = 1 << COLUMN_CALORIES

METRES_PER_KM = 1000.0
METRES_PER_MILE = 1609.344

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_WEEKDAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"]


def has_data(metric) -> bool:
    return any(
        value is not None
        for value in (
            metric.steps,
            metric.distance_m,
            metric.active_min,
            metric.effort_points,
            metric.calories_kcal,
            metric.hr_avg,
            metric.hr_min,
            metric.hr_max,
            metric.weight_avg_kg,
            metric.weight_min_kg,
            metric.weight_max_kg,
        )
    )


def derived_prefix(column: int, mask: int) -> str:
    return DERIVED_PREFIX if mask & (1 << column) != 0 else ""


def format_calories(kcal: float | None) -> str:
    if kcal is None:
        return "—"
    import math

    return str(int(math.floor(kcal + 0.5)))


def format_distance_km(metres: float | None) -> str:
    if metres is None:
        return "—"
    km = metres / METRES_PER_KM
    return f"{km:.1f}" if km < 10.0 else str(int(km))


def format_distance_miles(metres: float | None) -> str:
    if metres is None:
        return "—"
    miles = metres / METRES_PER_MILE
    return f"{miles:.1f}" if miles < 10.0 else str(int(miles))


def format_distance(metres: float | None, units: str) -> str:
    if units == "metric":
        return format_distance_km(metres)
    return format_distance_miles(metres)


def distance_label(units: str) -> str:
    return "km" if units == "metric" else "mi"


def format_active_min(minutes: int | None) -> str:
    return str(minutes) if minutes is not None else "—"


def weekly_progress_line(current: int, goal: int) -> str:
    return f"{current} of {goal}"


def week_effort_sum(days) -> int:
    return sum(day.effort_points or 0 for day in days)


def current_iso_week_effort(days, today: date) -> int:
    week_start = today - timedelta(days=today.weekday())
    total = 0
    for day in days:
        try:
            parsed = date.fromisoformat(day.date[:10])
        except (ValueError, AttributeError):
            continue
        if week_start <= parsed <= today:
            total += day.effort_points or 0
    return total


@dataclass(frozen=True)
class DaySlot:
    date: date
    metric: object | None


def last_seven_days(days, today: date) -> list[DaySlot]:
    return _padded_days(days, today - timedelta(days=6), today)


def iso_week_days(days, today: date) -> list[DaySlot]:
    start = today - timedelta(days=today.weekday())
    return _padded_days(days, start, start + timedelta(days=6))


def iso_week_range_label(today: date) -> str:
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    return f"{_month_day(start)} – {_month_day(end)}"


def weekday_letter(day: date) -> str:
    return _WEEKDAY_LETTERS[day.weekday()]


def _padded_days(days, start: date, end: date) -> list[DaySlot]:
    by_date: dict[date, object] = {}
    for row in days:
        try:
            parsed = date.fromisoformat(row.date[:10])
        except (ValueError, AttributeError):
            continue
        by_date[parsed] = row
    slots: list[DaySlot] = []
    cursor = start
    while cursor <= end:
        slots.append(DaySlot(date=cursor, metric=by_date.get(cursor)))
        cursor += timedelta(days=1)
    return slots


def _month_day(day: date) -> str:
    return f"{_MONTHS[day.month - 1]} {day.day}"
