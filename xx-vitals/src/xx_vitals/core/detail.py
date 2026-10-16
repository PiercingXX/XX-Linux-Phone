"""Metric detail aggregation. Port of ui/browse/MetricDetailModel.kt and
ui/browse/WeightDetailModel.kt (pure parts).

One metric at a time: daily rows aggregate into day/week/month/year buckets
(sums for accumulating metrics, averages for body weight), a bucket with no
readings stays null so the chart draws a gap, and the axis never collapses
to zero width. Weight detail projects weigh-in rows into per-day averages
because nothing on the phone writes weight into the daily table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from zoneinfo import ZoneInfo


class Zoom(Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


@dataclass(frozen=True)
class Bucket:
    label: str
    start_epoch_millis: int
    value: float | None
    samples: tuple = ()


@dataclass(frozen=True)
class Series:
    buckets: tuple
    axis_min: float
    axis_max: float


@dataclass(frozen=True)
class Sample:
    date: date
    value: float | None


def aggregate(dailies, zoom: Zoom, value, accumulates: bool = True) -> Series:
    combine = _sum if accumulates else _average
    if zoom is Zoom.DAY:
        buckets = [
            Bucket(
                label=_short_date(daily.date),
                start_epoch_millis=_utc_midnight(daily.date),
                value=value(daily),
                samples=(daily,),
            )
            for daily in sorted(dailies, key=lambda row: row.date)
        ]
    elif zoom is Zoom.WEEK:
        buckets = _grouped(dailies, value, combine, _week_key, _week_label)
    elif zoom is Zoom.MONTH:
        buckets = _grouped(dailies, value, combine, _month_key, _month_label)
    else:
        buckets = _grouped(dailies, value, combine, _year_key, _year_label)
    values = [bucket.value for bucket in buckets if bucket.value is not None]
    return Series(buckets=tuple(buckets), axis_min=_axis_min(values), axis_max=_axis_max(values))


def samples(dailies, value) -> list[Sample]:
    return [
        Sample(date=date.fromisoformat(row.date[:10]), value=value(row))
        for row in sorted(dailies, key=lambda row: row.date)
    ]


def _grouped(dailies, value, combine, key, label) -> list[Bucket]:
    by_key: dict[str, list] = {}
    for row in dailies:
        by_key.setdefault(key(row.date), []).append(row)
    ordered = sorted(by_key.items(), key=lambda item: min(row.date for row in item[1]))
    buckets: list[Bucket] = []
    for bucket_key, rows in ordered:
        readings = [reading for reading in (value(row) for row in rows) if reading is not None]
        oldest = min(row.date for row in rows)
        buckets.append(
            Bucket(
                label=label(bucket_key),
                start_epoch_millis=_utc_midnight(oldest),
                value=combine(readings) if readings else None,
                samples=tuple(sorted(rows, key=lambda row: row.date)),
            )
        )
    return buckets


def _sum(readings: list[float]) -> float:
    return sum(readings)


def _average(readings: list[float]) -> float:
    return sum(readings) / len(readings)


def _axis_min(values: list[float]) -> float:
    if not values:
        return 0.0
    low, high = min(values), max(values)
    return low - 1.0 if low == high else low


def _axis_max(values: list[float]) -> float:
    if not values:
        return 1.0
    low, high = min(values), max(values)
    return high + 1.0 if low == high else high


def _week_key(day_iso: str) -> str:
    parsed = date.fromisoformat(day_iso[:10])
    return f"{parsed.year}-W{parsed.isocalendar().week}"


def _week_label(key: str) -> str:
    return f"W{key.split('W', 1)[1]}"


def _month_key(day_iso: str) -> str:
    return day_iso[:7]


def _month_label(key: str) -> str:
    year, month = int(key[:4]), int(key[5:7])
    return f"{_MONTHS[month - 1]} {year}"


def _year_key(day_iso: str) -> str:
    return day_iso[:4]


def _year_label(key: str) -> str:
    return key


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _short_date(day_iso: str) -> str:
    parsed = date.fromisoformat(day_iso[:10])
    return f"{_MONTHS[parsed.month - 1]} {parsed.day}"


def _utc_midnight(day_iso: str) -> int:
    parsed = date.fromisoformat(day_iso[:10])
    return int(datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc).timestamp() * 1000)


PROJECTION_SOURCE_ID = "weight_sample_projection"


def daily_weight_projection(samples_, zone: ZoneInfo) -> list:
    from xx_vitals.store.db import DailyMetric

    by_day: dict[date, list] = {}
    for sample in samples_:
        local = datetime.fromtimestamp(sample.measured_at / 1000, tz=timezone.utc).astimezone(zone).date()
        by_day.setdefault(local, []).append(sample)
    rows = [
        DailyMetric(
            source_id=PROJECTION_SOURCE_ID,
            date=day.isoformat(),
            weight_avg_kg=sum(sample.weight_kg for sample in day_samples) / len(day_samples),
        )
        for day, day_samples in by_day.items()
    ]
    return sorted(rows, key=lambda row: row.date)
