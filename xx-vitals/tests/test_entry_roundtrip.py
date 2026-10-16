"""Milestone 2 round trip: manual weight + activity entry validate, write
Room first, enqueue wire-compatible payloads, and drain oldest-first."""

from xx_vitals.store import entries
from xx_vitals.store.db import Database
from xx_vitals.ui import entry as entry_ui


def _db():
    db = Database(":memory:")
    db.init()
    return db


def test_weight_submit_writes_room_and_enqueues_wire_payload():
    db = _db()
    row_id, error = entry_ui.submit_weight(db, "imperial", "184", 1752000000000)
    assert error is None
    sample = db.find_weight_by_natural_key("manual", 1752000000000)
    assert sample is not None
    queued = db.pending_next_weight_ops()
    assert len(queued) == 1
    assert queued[0].kind == "weight"
    assert queued[0].payload_json.startswith('{"source_id":"manual","measured_at":1752000000000,')
    assert row_id is not None


def test_weight_submit_rejects_garbage_without_writing():
    db = _db()
    row_id, error = entry_ui.submit_weight(db, "metric", "abc", 42)
    assert row_id is None and error is not None
    assert db.count_weights() == 0
    assert db.pending_count() == 0


def test_weight_kinds_stay_wire_compatible():
    assert entries.KIND_WEIGHT == "weight"
    assert entries.KIND_WEIGHT_UPDATE == "weight.update"
    assert entries.KIND_WEIGHT_DELETE == "weight.delete"
    assert entries.KIND_ACTIVITY == "activity"
    assert entries.KIND_DAILY == "daily"


def test_activity_submit_derives_and_enqueues():
    db = _db()
    row_id, error = entry_ui.submit_activity(
        db, "walk", "30", "3000", now_ms=1752000000000,
        height_metres=1.78, weight_kg=74.2,
    )
    assert error is None
    assert db.count_activities() == 1
    assert row_id is not None
    rows = db.pending_next(10, 2**31 - 1)
    assert len(rows) == 1
    assert rows[0].kind == "activity"
    parsed = entries.parse_activity(rows[0].payload_json)
    assert parsed is not None and parsed.type == "walk" and parsed.steps == 3000


def test_activity_submit_requires_type_and_duration():
    db = _db()
    assert entry_ui.submit_activity(db, "", "30", "", 1, 1.78, None)[0] is None
    assert entry_ui.submit_activity(db, "walk", "0", "", 1, 1.78, None)[0] is None
    assert entry_ui.submit_activity(db, "walk", "30", "-5", 1, 1.78, None)[0] is None
    assert db.count_activities() == 0
    assert db.pending_count() == 0


def test_queue_drains_oldest_first():
    db = _db()
    entry_ui.submit_weight(db, "metric", "74.20", 2000)
    entry_ui.submit_weight(db, "metric", "75.20", 1000)
    rows = db.pending_next(10, 2**31 - 1)
    assert [row.created_at for row in rows] == [1000, 2000]


def test_weight_edit_while_queued_keeps_single_insert():
    db = _db()
    from xx_vitals.store.db import WeightSample

    repo = entries.WeightRepository(db)
    repo.add_weight(
        WeightSample(source_id="manual", measured_at=7000, weight_kg=80.0),
        entries.weight_payload("manual", 7000, 80.0),
    )
    repo.edit(WeightSample(source_id="manual", measured_at=7000, weight_kg=81.0))
    queued = db.pending_next_weight_ops()
    assert len(queued) == 1 and queued[0].kind == "weight"
    assert db.find_weight_by_natural_key("manual", 7000).weight_kg == 81.0
