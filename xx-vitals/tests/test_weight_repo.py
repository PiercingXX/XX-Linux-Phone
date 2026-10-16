"""Port of WeightRepositoryTest: offline-first queue honesty."""

from xx_vitals.store import entries
from xx_vitals.store.db import Database, WeightSample
from xx_vitals.store.entries import WeightRepository


def _repo():
    db = Database(":memory:")
    db.init()
    return WeightRepository(db), db


def _sample(measured_at, kg, source_id="manual"):
    return WeightSample(source_id=source_id, measured_at=measured_at, weight_kg=kg)


def _payload(at, kg):
    return entries.weight_payload("manual", at, kg)


def test_add_writes_room_and_queues_insert():
    repo, db = _repo()
    repo.add_weight(_sample(1_000, 80.0), _payload(1_000, 80.0))
    assert [r.weight_kg for r in [db.find_weight_by_natural_key("manual", 1_000)]] == [80.0]
    queued = db.pending_next_weight_ops()
    assert len(queued) == 1
    assert queued[0].kind == entries.KIND_WEIGHT
    assert queued[0].payload_json == _payload(1_000, 80.0)


def test_edit_replaces_outstanding_insert():
    repo, db = _repo()
    repo.add_weight(_sample(1_000, 80.0), _payload(1_000, 80.0))
    row = db.find_weight_by_natural_key("manual", 1_000)
    repo.edit(WeightSample(source_id=row.source_id, measured_at=row.measured_at,
                           weight_kg=81.5, id=row.id))
    assert db.find_weight_by_natural_key("manual", 1_000).weight_kg == 81.5
    queued = db.pending_next_weight_ops()
    assert len(queued) == 1
    assert queued[0].kind == entries.KIND_WEIGHT
    assert queued[0].payload_json == _payload(1_000, 81.5)


def test_edit_after_drain_enqueues_update():
    repo, db = _repo()
    repo.add_weight(_sample(1_000, 80.0), _payload(1_000, 80.0))
    for row in list(db.pending_next_weight_ops()):
        db.pending_delete(row.id)
    row = db.find_weight_by_natural_key("manual", 1_000)
    repo.edit(WeightSample(source_id=row.source_id, measured_at=row.measured_at,
                           weight_kg=79.0, id=row.id))
    queued = db.pending_next_weight_ops()
    assert len(queued) == 1
    assert queued[0].kind == entries.KIND_WEIGHT_UPDATE
    assert queued[0].payload_json == _payload(1_000, 79.0)


def test_edit_repeatedly_keeps_one_row():
    repo, db = _repo()
    repo.add_weight(_sample(2_000, 90.0), _payload(2_000, 90.0))
    row = db.find_weight_by_natural_key("manual", 2_000)
    repo.edit(WeightSample(source_id=row.source_id, measured_at=2_000, weight_kg=91.0, id=row.id))
    row = db.find_weight_by_natural_key("manual", 2_000)
    repo.edit(WeightSample(source_id=row.source_id, measured_at=2_000, weight_kg=92.0, id=row.id))
    queued = db.pending_next_weight_ops()
    assert len(queued) == 1
    assert '"weight_kg":92.00' in queued[0].payload_json


def test_edit_touches_only_own_rows():
    repo, db = _repo()
    repo.add_weight(_sample(3_000, 70.0), _payload(3_000, 70.0))
    repo.add_weight(_sample(4_000, 75.0), _payload(4_000, 75.0))
    first = db.find_weight_by_natural_key("manual", 3_000)
    before = [r for r in db.pending_next_weight_ops() if ":4000," in r.payload_json][0]
    repo.edit(WeightSample(source_id=first.source_id, measured_at=3_000, weight_kg=71.0, id=first.id))
    queued = db.pending_next_weight_ops()
    assert len(queued) == 2
    assert before in queued
    assert any('"measured_at":3000,' in r.payload_json and "71.00" in r.payload_json for r in queued)


def test_edit_pulled_sample_enqueues_update():
    repo, db = _repo()
    pulled = WeightSample(source_id="nas", measured_at=5_000, weight_kg=60.0, id=7)
    db.upsert_weight(pulled)
    row = db.find_weight_by_natural_key("nas", 5_000)
    repo.edit(WeightSample(source_id="nas", measured_at=5_000, weight_kg=61.0, id=row.id))
    queued = db.pending_next_weight_ops()
    assert len(queued) == 1
    assert queued[0].kind == entries.KIND_WEIGHT_UPDATE
    assert '"source_id":"nas"' in queued[0].payload_json


def test_delete_removes_outstanding_insert():
    repo, db = _repo()
    repo.add_weight(_sample(6_000, 82.0), _payload(6_000, 82.0))
    row = db.find_weight_by_natural_key("manual", 6_000)
    repo.delete(row.id)
    assert db.find_weight_by_id(row.id) is None
    assert db.pending_next_weight_ops() == []


def test_delete_after_drain_enqueues_delete():
    repo, db = _repo()
    repo.add_weight(_sample(7_000, 83.0), _payload(7_000, 83.0))
    row = db.find_weight_by_natural_key("manual", 7_000)
    for queued in list(db.pending_next_weight_ops()):
        db.pending_delete(queued.id)
    repo.delete(row.id)
    assert db.find_weight_by_id(row.id) is None
    queued = db.pending_next_weight_ops()
    assert len(queued) == 1
    assert queued[0].kind == entries.KIND_WEIGHT_DELETE
    assert queued[0].payload_json == _payload(7_000, 83.0)


def test_delete_swallows_outstanding_update():
    repo, db = _repo()
    repo.add_weight(_sample(8_000, 84.0), _payload(8_000, 84.0))
    row = db.find_weight_by_natural_key("manual", 8_000)
    for queued in list(db.pending_next_weight_ops()):
        db.pending_delete(queued.id)
    row = db.find_weight_by_natural_key("manual", 8_000)
    repo.edit(WeightSample(source_id=row.source_id, measured_at=8_000, weight_kg=85.0, id=row.id))
    assert sum(1 for r in db.pending_next_weight_ops()
               if r.kind == entries.KIND_WEIGHT_UPDATE) == 1
    row = db.find_weight_by_natural_key("manual", 8_000)
    repo.delete(row.id)
    queued = db.pending_next_weight_ops()
    assert len(queued) == 1
    assert queued[0].kind == entries.KIND_WEIGHT_DELETE


def test_delete_unknown_id_is_noop():
    repo, db = _repo()
    repo.delete(999)
    assert db.pending_next_weight_ops() == []


def test_delete_keeps_other_sample_rows():
    repo, db = _repo()
    repo.add_weight(_sample(9_000, 64.0), _payload(9_000, 64.0))
    repo.add_weight(_sample(9_500, 66.0), _payload(9_500, 66.0))
    first = db.find_weight_by_natural_key("manual", 9_000)
    repo.delete(first.id)
    assert db.find_weight_by_id(first.id) is None
    remaining = db.pending_next_weight_ops()
    assert len(remaining) == 1
    assert remaining[0].kind == entries.KIND_WEIGHT
    assert '"measured_at":9500,' in remaining[0].payload_json
