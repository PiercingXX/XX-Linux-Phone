"""Port of HomeModelTest: no-data predicate, derived marks, stat formatting,
weekly target line, ISO-week effort, padded windows."""

from datetime import date

from xx_vitals.core import home
from xx_vitals.store.db import DailyMetric


def _metric(**overrides):
    base = {
        "source_id": "manual", "date": "2026-08-15",
        "steps": None, "distance_m": None, "active_min": None,
        "effort_points": None, "calories_kcal": None,
        "hr_avg": None, "hr_min": None, "hr_max": None,
        "weight_avg_kg": None,
    }
    base.update(overrides)
    return DailyMetric(**base)


def _day(day_iso, effort):
    return DailyMetric(source_id="manual", date=day_iso, effort_points=effort)


def test_has_data_all_null_is_false():
    assert home.has_data(_metric()) is False


def test_has_data_zero_steps_is_true():
    assert home.has_data(_metric(steps=0)) is True
    assert home.has_data(_metric(steps=8421)) is True


def test_has_data_any_metric_present():
    assert home.has_data(_metric(distance_m=6400.0)) is True
    assert home.has_data(_metric(active_min=38)) is True
    assert home.has_data(_metric(effort_points=41)) is True
    assert home.has_data(_metric(calories_kcal=312.0)) is True
    assert home.has_data(_metric(hr_avg=96)) is True
    assert home.has_data(_metric(hr_min=62)) is True
    assert home.has_data(_metric(hr_max=118)) is True
    assert home.has_data(_metric(weight_avg_kg=74.2)) is True


def test_derived_prefix_bit_set_returns_tilde():
    mask = home.BIT_DISTANCE | home.BIT_CALORIES
    assert home.derived_prefix(home.COLUMN_DISTANCE, mask) == "~"
    assert home.derived_prefix(home.COLUMN_CALORIES, mask) == "~"


def test_derived_prefix_bit_clear_returns_empty():
    assert home.derived_prefix(home.COLUMN_CALORIES, home.BIT_DISTANCE) == ""
    assert home.derived_prefix(home.COLUMN_DISTANCE, 0) == ""


def test_format_calories():
    assert home.format_calories(None) == "—"
    assert home.format_calories(312.0) == "312"
    assert home.format_calories(312.4) == "312"
    assert home.format_calories(312.6) == "313"
    assert home.format_calories(0.0) == "0"


def test_format_distance_km():
    assert home.format_distance_km(None) == "—"
    assert home.format_distance_km(6400.0) == "6.4"
    assert home.format_distance_km(900.0) == "0.9"
    assert home.format_distance_km(10000.0) == "10"
    assert home.format_distance_km(42000.0) == "42"


def test_format_distance_miles():
    assert home.format_distance_miles(None) == "—"
    assert home.format_distance_miles(6400.0) == "4.0"
    assert home.format_distance_miles(900.0) == "0.6"
    assert home.format_distance_miles(16093.44) == "10"
    assert home.format_distance_miles(42000.0) == "26"


def test_format_distance_follows_units_toggle():
    assert home.format_distance(6400.0, "metric") == "6.4"
    assert home.format_distance(6400.0, "imperial") == "4.0"
    assert home.format_distance(None, "imperial") == "—"
    assert home.distance_label("metric") == "km"
    assert home.distance_label("imperial") == "mi"


def test_format_active_min():
    assert home.format_active_min(None) == "—"
    assert home.format_active_min(38) == "38"
    assert home.format_active_min(0) == "0"


def test_weekly_progress_line_never_clamped():
    assert home.weekly_progress_line(90, 150) == "90 of 150"
    assert home.weekly_progress_line(160, 150) == "160 of 150"
    assert home.weekly_progress_line(0, 150) == "0 of 150"


def test_week_effort_sum_skips_gaps():
    days = [_metric(effort_points=10), _metric(effort_points=12), _metric(), _metric(effort_points=3)]
    assert home.week_effort_sum(days) == 25
    assert home.week_effort_sum([_metric(), _metric(), _metric(effort_points=7)]) == 7
    assert home.week_effort_sum([]) == 0


def test_current_iso_week_effort_counts_only_this_week():
    days = [
        _day("2026-08-15", 10),
        _day("2026-08-17", 4),
        _day("2026-08-20", 5),
        _day("2026-08-23", 3),
        _day("2026-08-24", 9),
    ]
    assert home.current_iso_week_effort(days, date(2026, 8, 23)) == 12


def test_current_iso_week_effort_gaps_and_garbage_contribute_nothing():
    days = [_day("2026-08-17", None), _day("2026-08-18", 7), _day("2026-08-19", None)]
    assert home.current_iso_week_effort(days, date(2026, 8, 23)) == 7
    days = [_day("not-a-date", 50), _day("2026-08-20", 2)]
    assert home.current_iso_week_effort(days, date(2026, 8, 23)) == 2
    days = [_day("2026-08-16", 10), _day("2026-08-17", 6)]
    assert home.current_iso_week_effort(days, date(2026, 8, 17)) == 6


def test_last_seven_days_always_seven_slots():
    today = date(2026, 9, 12)
    slots = home.last_seven_days([_day("2026-09-09", 12)], today)
    assert len(slots) == 7
    assert slots[0].date == date(2026, 9, 6)
    assert slots[-1].date == today
    assert [slot for slot in slots if slot.date == date(2026, 9, 9)][0].metric.effort_points == 12
    assert all(slot.metric is None for slot in slots if slot.date != date(2026, 9, 9))


def test_iso_week_days_monday_through_sunday():
    today = date(2026, 9, 12)
    slots = home.iso_week_days([_day("2026-09-09", 8)], today)
    assert len(slots) == 7
    assert slots[0].date == date(2026, 9, 7)
    assert slots[-1].date == date(2026, 9, 13)
    assert slots[2].metric.effort_points == 8


def test_iso_week_range_label_and_weekday_letter():
    assert home.iso_week_range_label(date(2026, 9, 12)) == "Sep 7 – Sep 13"
    assert home.weekday_letter(date(2026, 9, 7)) == "M"
    assert home.weekday_letter(date(2026, 9, 12)) == "S"
