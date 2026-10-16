"""Journal grouping. Port of ui/journal/JournalModel.kt (pure part).

Groups activity rows into local calendar days, newest first, with per-day
totals and a saturated mini-ring fraction. Gap days render as gaps: see
no_data_gap. No I/O, no GI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from xx_vitals.core import metrics
from xx_vitals.core.bucketing import local_day

PHONE_STEPS_TYPE = "Phone steps"


@dataclass(frozen=True)
class DayGroup:
    date: date
    activities: tuple
    total_steps: int
    total_effort: int
    ring_fraction: float


def _instant_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def ring_fraction(effort: int, target: int) -> float:
    return metrics.ring_fraction(float(effort), float(target))


def _has_activity_data(daily) -> bool:
    return (
        daily.steps is not None
        or daily.distance_m is not None
        or daily.active_min is not None
        or daily.effort_points is not None
        or daily.calories_kcal is not None
    )


def _phone_steps_row(daily, zone: ZoneInfo):
    from xx_vitals.store.db import Activity

    start = _parse_daily_noon(daily.date, zone, hour=8)
    active_ms = max(1, daily.active_min or 0) * 60_000
    return Activity(
        id=0,
        source_id=daily.source_id,
        type=PHONE_STEPS_TYPE,
        started_at=int(start.timestamp() * 1000),
        ended_at=int(start.timestamp() * 1000) + active_ms,
        distance_m=daily.distance_m,
        calories_kcal=daily.calories_kcal,
        effort_points=daily.effort_points,
        active_min=daily.active_min,
        hr_avg=daily.hr_avg,
        hr_max=daily.hr_max,
        steps=daily.steps,
        track_geojson=None,
        batch_id=daily.batch_id,
    )


def _parse_daily_noon(day_iso: str, zone: ZoneInfo, hour: int = 8) -> datetime:
    year, month, day = (int(p) for p in day_iso[:10].split("-"))
    return datetime(year, month, day, hour, 0, tzinfo=zone)


def group_by_day(activities, zone: ZoneInfo, effort_target: int, dailies=()) -> list[DayGroup]:
    by_date: dict[date, DayGroup] = {}
    buckets: dict[date, list] = {}
    for activity in activities:
        day = local_day(_instant_ms(activity.started_at), zone)
        buckets.setdefault(day, []).append(activity)
    for day, rows in buckets.items():
        ordered = sorted(rows, key=lambda a: a.started_at)
        steps = sum(a.steps or 0 for a in ordered)
        effort = sum(a.effort_points or 0 for a in ordered)
        by_date[day] = DayGroup(
            date=day,
            activities=tuple(ordered),
            total_steps=steps,
            total_effort=effort,
            ring_fraction=ring_fraction(effort, effort_target),
        )
    for daily in dailies:
        try:
            parts = daily.date[:10].split("-")
            day = date(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError, AttributeError):
            continue
        if not _has_activity_data(daily):
            continue
        existing = by_date.get(day)
        if existing is None:
            effort = daily.effort_points or 0
            by_date[day] = DayGroup(
                date=day,
                activities=(_phone_steps_row(daily, zone),),
                total_steps=daily.steps or 0,
                total_effort=effort,
                ring_fraction=ring_fraction(effort, effort_target),
            )
        else:
            steps = daily.steps if daily.steps is not None else existing.total_steps
            effort = daily.effort_points if daily.effort_points is not None else existing.total_effort
            by_date[day] = DayGroup(
                date=existing.date,
                activities=existing.activities,
                total_steps=steps,
                total_effort=effort,
                ring_fraction=ring_fraction(effort, effort_target),
            )
    return sorted(by_date.values(), key=lambda g: g.date, reverse=True)


def distance_part(metres: float, units: str) -> str:
    if units == "metric":
        value = metres / 1000.0
        label = "km"
    else:
        value = metres / 1609.344
        label = "mi"
    figure = f"{value:.1f}" if value < 10.0 else str(int(value))
    return f"{figure} {label}"


def no_data_gap(last_data_day: date, today: date) -> int:
    if last_data_day > today:
        return 0
    return (today - last_data_day).days
