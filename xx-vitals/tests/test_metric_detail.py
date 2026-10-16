"""Port of MetricDetailModelTest + WeightDetailModelTest: zoom aggregation,
accumulating vs averaging, gap honesty, axis safety, weight projection."""

from datetime import date
from zoneinfo import ZoneInfo

from xx_vitals.core import detail
from xx_vitals.core.detail import Zoom
from xx_vitals.store.db import DailyMetric, WeightSample


def _daily(day_iso, steps=None, effort=None, distance=None, weight=None):
    return DailyMetric(
        source_id="manual", date=day_iso, steps=steps,
        distance_m=distance, effort_points=effort, weight_avg_kg=weight,
    )


def _steps(row):
    return float(row.steps) if row.steps is not None else None


def test_day_keeps_one_bucket_per_day_chronological():
    series = detail.aggregate(
        [_daily("2026-08-14", steps=3000), _daily("2026-08-15", steps=4000)],
        Zoom.DAY, _steps,
    )
    assert [bucket.label for bucket in series.buckets] == ["Aug 14", "Aug 15"]
    assert [bucket.value for bucket in series.buckets] == [3000.0, 4000.0]
    assert len(series.buckets[0].samples) == 1
    assert series.buckets[0].samples[0].date == "2026-08-14"


def test_day_passes_null_as_gap_not_zero():
    series = detail.aggregate([_daily("2026-08-15")], Zoom.DAY, _steps)
    assert series.buckets[0].value is None


def test_week_sums_days_into_iso_weeks():
    series = detail.aggregate(
        [
            _daily("2026-08-10", steps=1000),
            _daily("2026-08-11", steps=2000),
            _daily("2026-08-17", steps=5000),
        ],
        Zoom.WEEK, _steps,
    )
    assert len(series.buckets) == 2
    assert series.buckets[0].label == "W33"
    assert series.buckets[0].value == 3000.0
    assert series.buckets[1].label == "W34"
    assert series.buckets[1].value == 5000.0
    assert [row.date for row in series.buckets[0].samples] == ["2026-08-10", "2026-08-11"]


def test_month_and_year_sum():
    month = detail.aggregate(
        [
            _daily("2026-07-31", steps=1000),
            _daily("2026-08-01", steps=2000),
            _daily("2026-08-15", steps=3000),
        ],
        Zoom.MONTH, _steps,
    )
    assert [bucket.label for bucket in month.buckets] == ["Jul 2026", "Aug 2026"]
    assert [bucket.value for bucket in month.buckets] == [1000.0, 5000.0]
    year = detail.aggregate(
        [
            _daily("2025-12-31", steps=1000),
            _daily("2026-01-01", steps=2000),
            _daily("2026-06-15", steps=3000),
        ],
        Zoom.YEAR, _steps,
    )
    assert [bucket.label for bucket in year.buckets] == ["2025", "2026"]
    assert [bucket.value for bucket in year.buckets] == [1000.0, 5000.0]


def test_all_null_bucket_days_do_not_fabricate_zero():
    series = detail.aggregate(
        [
            _daily("2026-08-10"),
            _daily("2026-08-11"),
            _daily("2026-08-12", steps=1000),
        ],
        Zoom.WEEK, _steps,
    )
    assert len(series.buckets) == 1
    assert series.buckets[0].value == 1000.0


def test_week_averages_non_accumulating_metric():
    series = detail.aggregate(
        [
            _daily("2026-08-10", weight=80.0),
            _daily("2026-08-12", weight=82.0),
            _daily("2026-08-14", weight=84.0),
        ],
        Zoom.WEEK, lambda row: row.weight_avg_kg, accumulates=False,
    )
    assert len(series.buckets) == 1
    assert abs(series.buckets[0].value - 82.0) < 1e-9


def test_month_and_year_average_non_accumulating():
    month = detail.aggregate(
        [
            _daily("2026-07-31", weight=90.0),
            _daily("2026-08-01", weight=80.0),
            _daily("2026-08-15", weight=84.0),
        ],
        Zoom.MONTH, lambda row: row.weight_avg_kg, accumulates=False,
    )
    assert [bucket.value for bucket in month.buckets] == [90.0, 82.0]
    year = detail.aggregate(
        [
            _daily("2025-12-31", weight=100.0),
            _daily("2026-01-01", weight=90.0),
            _daily("2026-06-15", weight=94.0),
        ],
        Zoom.YEAR, lambda row: row.weight_avg_kg, accumulates=False,
    )
    assert [bucket.value for bucket in year.buckets] == [100.0, 92.0]


def test_axis_safety_empty_all_null_and_flat():
    empty = detail.aggregate([], Zoom.DAY, _steps)
    assert (empty.axis_min, empty.axis_max) == (0.0, 1.0)
    gaps = detail.aggregate([_daily("2026-08-15")], Zoom.DAY, _steps)
    assert (gaps.axis_min, gaps.axis_max) == (0.0, 1.0)
    flat = detail.aggregate(
        [_daily("2026-08-14", steps=3000), _daily("2026-08-15", steps=3000)],
        Zoom.DAY, _steps,
    )
    assert flat.axis_max - flat.axis_min == 2.0
    assert flat.axis_min <= 3000.0 <= flat.axis_max


def test_samples_list_is_chronological_with_gaps():
    rows = detail.samples(
        [_daily("2026-08-15", steps=4000), _daily("2026-08-14")], _steps,
    )
    assert [(sample.date, sample.value) for sample in rows] == [
        (date(2026, 8, 14), None),
        (date(2026, 8, 15), 4000.0),
    ]


def _sample_at(year, month, day, hour, kg):
    from datetime import datetime, timezone

    ms = int(datetime(year, month, day, hour, 0, tzinfo=timezone.utc).timestamp() * 1000)
    return WeightSample(source_id="manual", measured_at=ms, weight_kg=kg)


def test_weight_projection_averages_each_day():
    zone = ZoneInfo("UTC")
    rows = detail.daily_weight_projection(
        [
            _sample_at(2026, 8, 10, 8, 80.0),
            _sample_at(2026, 8, 10, 20, 82.0),
            _sample_at(2026, 8, 11, 8, 84.0),
        ],
        zone,
    )
    assert [row.date for row in rows] == ["2026-08-10", "2026-08-11"]
    assert rows[0].weight_avg_kg == 81.0
    assert rows[0].source_id == detail.PROJECTION_SOURCE_ID
    assert detail.daily_weight_projection([], zone) == []
