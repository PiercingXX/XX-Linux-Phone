"""Port of WeightEntryModelTest, ActivityEntryModelTest, DailyEntryModelTest."""

from datetime import date
from zoneinfo import ZoneInfo

from xx_vitals.core import metrics
from xx_vitals.store import entries
from xx_vitals.store.db import DailyMetric, Database


def test_weight_units_store_kg_always():
    assert entries.to_kg(74.2, entries.WeightUnit.KG) == 74.2
    assert entries.to_kg(184.0, entries.WeightUnit.LB) == metrics.lb_to_kg(184.0)
    assert entries.from_kg(74.2, entries.WeightUnit.KG) == 74.2
    assert entries.from_kg(74.2, entries.WeightUnit.LB) == metrics.kg_to_lb(74.2)


def test_weight_round_trip_to_hundredth():
    kg = 74.2
    back = entries.to_kg(entries.from_kg(kg, entries.WeightUnit.LB), entries.WeightUnit.LB)
    assert abs(back - kg) < 0.01


def test_weight_kind_constants():
    assert entries.KIND_WEIGHT == "weight"
    assert entries.KIND_WEIGHT_UPDATE == "weight.update"
    assert entries.KIND_WEIGHT_DELETE == "weight.delete"


def test_weight_payload_mirrors_sample_columns():
    assert entries.weight_payload("manual", 1_752_000_000_000, 74.2) == (
        '{"source_id":"manual","measured_at":1752000000000,"weight_kg":74.20}'
    )


def test_weight_payload_escapes():
    assert entries.weight_payload("a\"b\\c", 1, 70.0) == (
        '{"source_id":"a\\"b\\\\c","measured_at":1,"weight_kg":70.00}'
    )


def test_weight_payload_rounds_to_two_decimals():
    assert entries.weight_payload("manual", 2, 74.235) == (
        '{"source_id":"manual","measured_at":2,"weight_kg":74.24}'
    )


def test_round_for_storage_matches_payload():
    for kg in [74.235, 70.0, 99.999, 0.005]:
        rounded = entries.round_for_storage(kg)
        payload = entries.weight_payload("manual", 1, rounded)
        carried = float(payload.split('"weight_kg":')[1].rstrip("}"))
        assert carried == rounded


def test_weight_update_delete_share_insert_shape():
    expected = '{"source_id":"manual","measured_at":7,"weight_kg":80.00}'
    assert entries.weight_payload("manual", 7, 80.0) == expected
    assert entries.weight_key_prefix("manual", 7) + '"weight_kg":80.00}' == expected


def test_weight_prefix_bounds_match():
    prefix = entries.weight_key_prefix("manual", 123)
    assert prefix == '{"source_id":"manual","measured_at":123,'
    assert not entries.weight_payload("manual", 1234, 1.0).startswith(prefix)


def test_unit_for_follows_display_units():
    assert entries.unit_for("metric") == entries.WeightUnit.KG
    assert entries.unit_for("imperial") == entries.WeightUnit.LB


def test_can_amend_window():
    measured = 1_000_000
    assert entries.can_amend(measured, measured)
    assert entries.can_amend(measured, measured + entries.AMEND_WINDOW_MS)
    assert not entries.can_amend(measured, measured + entries.AMEND_WINDOW_MS + 1)
    assert entries.can_amend(2_000, 1_000)


def test_format_entry_date_uses_sample_zone():
    from datetime import datetime, timezone

    at = int(datetime(2026, 9, 9, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert entries.format_entry_date(at, ZoneInfo("America/New_York")) == "Sep 9, 2026"


def test_measured_at_for_today_and_past():
    zone = ZoneInfo("America/New_York")
    from datetime import datetime

    now = int(datetime(2026, 9, 12, 18, 30, tzinfo=zone).timestamp() * 1000)
    assert entries.measured_at_for(date(2026, 9, 12), zone, now) == now
    expected = int(datetime(2026, 9, 9, 12, 0, tzinfo=zone).timestamp() * 1000)
    assert entries.measured_at_for(date(2026, 9, 9), zone, now) == expected


def test_activity_duration_and_steps_parsing():
    assert entries.parse_duration_minutes("0") is None
    assert entries.parse_duration_minutes("1441") is None
    assert entries.parse_duration_minutes("30") == 30
    assert entries.parse_optional_steps("") is None
    assert entries.parse_optional_steps("0") == 0
    assert entries.parse_optional_steps("-1") is None


def test_activity_derived_totals_fallback():
    fields = entries.derive_activity(steps=1000, duration_min=20, height_metres=1.70, weight_kg=None)
    assert fields.active_min == 20
    assert fields.effort_points == 10
    assert fields.distance_m is not None
    assert fields.calories_kcal is None


def test_activity_payload_round_trip():
    payload = entries.activity_payload(
        source_id="manual", type_="walk", started_at=1000, ended_at=2000,
        distance_m=12.5, calories_kcal=None, effort_points=8, active_min=15, steps=800,
    )
    parsed = entries.parse_activity(payload)
    assert parsed is not None
    assert parsed.type == "walk"
    assert parsed.started_at == 1000
    assert parsed.steps == 800
    assert parsed.calories_kcal is None


def test_activity_glyphs():
    assert entries.glyph_for("walk") == "\u25c8"
    assert entries.glyph_for("Phone steps") == "\u25c6"
    assert entries.glyph_for("unknown") == "\u00b7"


def _metric(day: str, steps: int) -> DailyMetric:
    return DailyMetric(
        source_id="phone", date=day, steps=steps, distance_m=100.0,
        active_min=10, effort_points=10, calories_kcal=50.0, derived_mask=15,
    )


def test_daily_payload_round_trip():
    parsed = entries.parse_daily(entries.daily_payload(_metric("2026-09-12", 9000)))
    assert parsed is not None
    assert parsed.date == "2026-09-12"
    assert parsed.steps == 9000
    assert abs(parsed.distance_m - 100.0) < 0.0001
    assert parsed.derived_mask == 15


def test_daily_enqueue_replaces_same_date():
    db = Database(":memory:")
    db.init()
    entries.enqueue_daily(db, _metric("2026-09-12", 100), now_ms=1)
    entries.enqueue_daily(db, _metric("2026-09-12", 200), now_ms=2)
    entries.enqueue_daily(db, _metric("2026-09-13", 50), now_ms=3)
    assert db.pending_count() == 2
    today = [r for r in db.pending_next_daily_ops()
             if entries.daily_date_of(r.payload_json) == "2026-09-12"]
    assert len(today) == 1
    assert entries.parse_daily(today[0].payload_json).steps == 200


def test_prefer_phone_keeps_one_row_per_date():
    phone = _metric("2026-09-12", 100)
    pulled = _metric("2026-09-12", 999)
    pulled.source_id = "manual"
    other = _metric("2026-09-11", 10)
    other.source_id = "manual"
    collapsed = entries.prefer_phone([pulled, phone, other])
    assert len(collapsed) == 2
    assert [r for r in collapsed if r.date == "2026-09-12"][0].steps == 100
    assert [r for r in collapsed if r.date == "2026-09-11"][0].source_id == "manual"
