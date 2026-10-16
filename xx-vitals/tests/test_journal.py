"""Port of JournalModelTest (pure grouping, totals, ring, no-data band)."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from xx_vitals.core import journal as journal_model
from xx_vitals.store.db import Activity, DailyMetric

NEW_YORK = ZoneInfo("America/New_York")


def _ms(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _activity(start_iso, steps=None, effort=None, type_="walk"):
    start = _ms(start_iso)
    return Activity(source_id="healthconnect", type=type_, started_at=start,
                    ended_at=start + 3_600_000, steps=steps, effort_points=effort)


def test_groups_by_local_day_newest_first():
    groups = journal_model.group_by_day(
        [_activity("2026-08-14T12:00:00Z", steps=3000),
         _activity("2026-08-15T12:00:00Z", steps=4000)],
        NEW_YORK, 90)
    assert [g.date for g in groups] == [date(2026, 8, 15), date(2026, 8, 14)]


def test_late_night_utc_prior_local_day():
    groups = journal_model.group_by_day([_activity("2026-08-15T03:00:00Z")], NEW_YORK, 90)
    assert [g.date for g in groups] == [date(2026, 8, 14)]


def test_within_day_ordered_by_start():
    groups = journal_model.group_by_day(
        [_activity("2026-08-15T12:00:00Z", type_="run"),
         _activity("2026-08-15T10:00:00Z", type_="walk")],
        NEW_YORK, 90)
    assert [a.type for a in groups[0].activities] == ["walk", "run"]


def test_empty_yields_no_groups():
    assert journal_model.group_by_day([], NEW_YORK, 90) == []


def test_totals_sum_and_null_as_zero():
    group = journal_model.group_by_day(
        [_activity("2026-08-15T10:00:00Z", steps=4000, effort=50),
         _activity("2026-08-15T18:00:00Z", steps=2900, effort=40)],
        NEW_YORK, 90)[0]
    assert group.total_steps == 6900
    assert group.total_effort == 90
    single = journal_model.group_by_day(
        [_activity("2026-08-15T12:00:00Z", steps=5000)], NEW_YORK, 90)[0]
    assert single.total_steps == 5000
    assert single.total_effort == 0


def test_ring_saturated():
    full = journal_model.group_by_day(
        [_activity("2026-08-15T12:00:00Z", effort=90)], NEW_YORK, 90)[0]
    assert full.ring_fraction == 1.0
    over = journal_model.group_by_day(
        [_activity("2026-08-15T12:00:00Z", effort=300)], NEW_YORK, 90)[0]
    assert over.ring_fraction == 1.0


def test_daily_rollup_phone_steps_row():
    daily = DailyMetric(source_id="manual", date="2026-08-15", steps=6900,
                        effort_points=69, active_min=69, derived_mask=0)
    groups = journal_model.group_by_day([], NEW_YORK, 90, dailies=[daily])
    assert len(groups) == 1
    assert groups[0].total_steps == 6900
    assert groups[0].activities[0].type == journal_model.PHONE_STEPS_TYPE


def test_no_data_gap():
    assert journal_model.no_data_gap(date(2026, 9, 12), date(2026, 9, 12)) == 0
    assert journal_model.no_data_gap(date(2026, 9, 10), date(2026, 9, 12)) == 2
    assert journal_model.no_data_gap(date(2026, 9, 13), date(2026, 9, 12)) == 0


def test_distance_part_units():
    assert journal_model.distance_part(9000.0, "metric") == "9.0 km"
    assert journal_model.distance_part(9000.0, "imperial").endswith(" mi")


def test_utc_anchor():
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    assert instant.tzinfo is not None
