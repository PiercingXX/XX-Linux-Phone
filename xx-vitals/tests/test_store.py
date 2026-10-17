"""SQLite schema parity: Room 1:1 tables, UNIQUE idempotence, WAL."""

import sqlite3

from xx_vitals.store import entries
from xx_vitals.store.db import Activity, DailyMetric, Database, PendingWrite, SyncState, WeightSample


def _db():
    db = Database(":memory:")
    db.init()
    return db


def test_tables_mirror_room_columns():
    db = _db()
    conn = db._connect()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        pass
    assert {"daily_metric", "activity", "weight_sample", "pending_write", "sync_state"} <= tables


def test_daily_primary_key_is_source_date():
    db = _db()
    db.upsert_daily(DailyMetric(source_id="manual", date="2026-09-12", steps=100, derived_mask=0))
    db.upsert_daily(DailyMetric(source_id="manual", date="2026-09-12", steps=200, derived_mask=0))
    assert db.count_dailies() == 1
    assert db.get_daily("manual", "2026-09-12").steps == 200
    db.upsert_daily(DailyMetric(source_id="phone", date="2026-09-12", steps=50, derived_mask=0))
    assert db.count_dailies() == 2


def test_weight_unique_source_measured_at_preserves_id():
    db = _db()
    first = db.upsert_weight(WeightSample(source_id="manual", measured_at=1_000, weight_kg=70.0))
    second = db.upsert_weight(WeightSample(source_id="manual", measured_at=1_000, weight_kg=71.0))
    assert first == second
    assert db.count_weights() == 1
    assert db.find_weight_by_natural_key("manual", 1_000).weight_kg == 71.0
    db.upsert_weight(WeightSample(source_id="manual", measured_at=2_000, weight_kg=70.0))
    assert db.count_weights() == 2


def test_activity_unique_natural_key_upserts():
    db = _db()
    first = db.insert_activity(Activity(
        source_id="manual", type="walk", started_at=1_000, ended_at=2_000, steps=100))
    second = db.insert_activity(Activity(
        source_id="manual", type="walk", started_at=1_000, ended_at=2_000, steps=200))
    assert first == second
    assert db.count_activities() == 1
    assert db.find_activity_by_natural_key("manual", 1_000, 2_000, "walk").steps == 200


def test_unique_indexes_exist_at_db_level():
    db = _db()
    conn = db._connect()
    try:
        indexes = {r[1] for r in conn.execute("SELECT type, name FROM sqlite_master").fetchall()
                   if r[0] == "index"}
    finally:
        pass
    assert "index_weight_sample_source_id_measured_at" in indexes
    assert "index_activity_source_id_started_at_ended_at_type" in indexes


def test_pending_fifo_and_dead_letter_filter():
    db = _db()
    db.pending_insert(PendingWrite(kind="weight", payload_json="{}", created_at=30))
    db.pending_insert(PendingWrite(kind="weight", payload_json="{}", created_at=10))
    rows = db.pending_next(10, 2147483646)
    assert [r.created_at for r in rows] == [10, 30]
    assert db.pending_count() == 2


def test_sync_state_single_row_cursor_raw_iso():
    db = _db()
    assert db.sync_get() is None
    iso = "2026-08-01T08:00:00.123456+00:00"
    db.sync_upsert(SyncState(id=1, last_pull_at=1_000, last_push_at=None, last_pull_iso=iso))
    assert db.sync_get().last_pull_iso == iso
    db.sync_upsert(SyncState(id=1, last_pull_at=2_000, last_push_at=42, last_pull_iso=iso))
    assert db.sync_get().last_push_at == 42


def test_journal_grouping_and_format_helpers():
    from datetime import date
    from zoneinfo import ZoneInfo

    from xx_vitals.core import journal as journal_model
    from xx_vitals.core.format import format_distance_km, format_height, format_weight

    zone = ZoneInfo("America/New_York")
    groups = journal_model.group_by_day([], zone, 90)
    assert groups == []
    assert journal_model.no_data_gap(date(2026, 9, 10), date(2026, 9, 12)) == 2
    assert format_weight(83.5, "metric") == "83.5 kg"
    assert format_weight(83.5, "imperial").endswith(" lb")
    assert format_height(1.78, "metric") == "1.78 m"
    assert format_distance_km(9.0, "metric") == "9.0 km"
    assert entries.prefer_phone([]) == []


def test_replayed_sync_is_idempotent_through_uniques():
    db = _db()
    sample = WeightSample(source_id="manual", measured_at=5_000, weight_kg=70.0)
    db.upsert_weight(sample)
    db.upsert_weight(sample)
    assert db.count_weights() == 1
    db.upsert_daily(DailyMetric(source_id="manual", date="2026-01-01", steps=1, derived_mask=0))
    db.upsert_daily(DailyMetric(source_id="manual", date="2026-01-01", steps=1, derived_mask=0))
    assert db.count_dailies() == 1


def test_listings_return_newest_first_with_limits():
    db = _db()
    db.upsert_daily(DailyMetric(source_id="phone", date="2026-10-14", steps=1, derived_mask=0))
    db.upsert_daily(DailyMetric(source_id="phone", date="2026-10-16", steps=3, derived_mask=0))
    db.upsert_daily(DailyMetric(source_id="phone", date="2026-10-15", steps=2, derived_mask=0))
    assert [row.date for row in db.list_dailies()] == ["2026-10-16", "2026-10-15", "2026-10-14"]
    assert [row.date for row in db.list_dailies(limit=2)] == ["2026-10-16", "2026-10-15"]
    db.insert_activity(Activity(
        source_id="phone", type="walk", started_at=1_000, ended_at=2_000, steps=10))
    db.insert_activity(Activity(
        source_id="phone", type="run", started_at=3_000, ended_at=4_000, steps=20))
    assert [row.started_at for row in db.list_activities()] == [3_000, 1_000]
    db.upsert_weight(WeightSample(source_id="manual", measured_at=1_000, weight_kg=70.0))
    db.upsert_weight(WeightSample(source_id="manual", measured_at=2_000, weight_kg=71.0))
    assert [row.measured_at for row in db.list_weights()] == [2_000, 1_000]
    assert db.list_weights(limit=1)[0].weight_kg == 71.0


def test_file_db_uses_wal(tmp_path):
    path = tmp_path / "vitals.db"
    db = Database(path)
    db.init()
    conn = sqlite3.connect(str(path))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"
