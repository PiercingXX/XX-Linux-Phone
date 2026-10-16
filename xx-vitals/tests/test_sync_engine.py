"""Port of SyncEnginePushTest + SyncEnginePullTest + end-to-end via fakes.

No network: SyncApi is the FakeSyncApi in-memory transport. Covers queue
order, 2xx-only dequeue, PATCH/DELETE id lookup, 507 pause, dead-letter,
401 abort, backoff, pull cursor (raw server ISO), and watermark advance.
"""

import pytest

from xx_vitals.store import entries
from xx_vitals.store.db import Activity, DailyMetric, Database, PendingWrite, SyncState, WeightSample
from xx_vitals.sync import engine as engine_mod
from xx_vitals.sync.api import (
    AuthExpired,
    BatchStamp,
    ChangesPage,
    FakeSyncApi,
    Ok,
    Rejected,
    Transient,
    VolumeFull,
    to_iso,
)
from xx_vitals.sync.engine import (
    SyncAuthException,
    SyncEngine,
    SyncPermanentException,
    SyncRetryableException,
)


def _payload(measured_at, weight_kg):
    return '{"source_id":"manual","measured_at":%d,"weight_kg":%s}' % (measured_at, weight_kg)


def _write(id_, measured_at, weight_kg, kind="weight"):
    return PendingWrite(id=id_, kind=kind, payload_json=_payload(measured_at, weight_kg),
                        created_at=measured_at)


class _Pending:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.deleted = []
        self.failures = []

    def pending_insert(self, write):
        new_id = max([r.id for r in self.rows] + [0]) + 1
        self.rows.append(PendingWrite(id=new_id, kind=write.kind,
                                      payload_json=write.payload_json,
                                      created_at=write.created_at,
                                      attempts=write.attempts,
                                      last_error=write.last_error))
        return new_id

    def pending_next(self, limit, dead_letter_attempts):
        ordered = sorted(self.rows, key=lambda r: (r.created_at, r.id))
        return [r for r in ordered if r.attempts < dead_letter_attempts][:limit]

    def pending_delete(self, id_):
        self.rows = [r for r in self.rows if r.id != id_]
        self.deleted.append(id_)

    def pending_record_failure(self, id_, attempts, last_error):
        self.failures.append((id_, attempts, last_error))
        self.rows = [r if r.id != id_ else PendingWrite(
            id=r.id, kind=r.kind, payload_json=r.payload_json, created_at=r.created_at,
            attempts=attempts, last_error=last_error) for r in self.rows]

    def pending_count(self):
        return len(self.rows)

    def pending_next_weight_ops(self):
        return []

    def pending_next_daily_ops(self):
        return []


class _Sync:
    def __init__(self, state=None):
        self.state = state
        self.upserted = []

    def sync_get(self):
        return self.state

    def sync_upsert(self, state):
        self.state = state
        self.upserted.append(state)


class _Weights:
    def __init__(self):
        self.samples = []

    def upsert_weight(self, sample):
        self.samples.append(sample)


class _Activities:
    def __init__(self):
        self.inserted = []

    def insert_all(self, activities):
        self.inserted.append(list(activities))


class _Dailies:
    def __init__(self):
        self.upserted = []

    def upsert_all(self, metrics):
        self.upserted.extend(metrics)

    def upsert_daily(self, metric):
        self.upserted.append(metric)


def _engine(dao, api, state=None, now=lambda: 42, delays=None):
    sync = state if isinstance(state, _Sync) else _Sync(state)
    return SyncEngine(
        pending_dao=dao, sync_dao=sync, weight_dao=_Weights(),
        activity_dao=_Activities(), daily_dao=_Dailies(), api=api,
        now=now, delay=(lambda ms: delays.append(ms)) if delays is not None else (lambda ms: None),
    ), sync


def test_drains_in_order_deleting_each_on_2xx():
    dao = _Pending([_write(1, 100, 70.0), _write(2, 200, 71.0), _write(3, 300, 72.0)])
    api = FakeSyncApi()
    eng, _ = _engine(dao, api)
    eng.push()
    assert api.posted_iso == [to_iso(100), to_iso(200), to_iso(300)]
    assert api.posted_kg == [70.0, 71.0, 72.0]
    assert dao.rows == []
    assert dao.deleted == [1, 2, 3]


def test_update_patches_via_lookup_not_post():
    dao = _Pending([_write(1, 500, 80.5, kind="weight.update")])
    api = FakeSyncApi()
    api.find_result = Ok(77)
    eng, _ = _engine(dao, api)
    eng.push()
    assert api.find_calls == 1
    assert api.posted_iso == []
    assert api.patched == [(77, to_iso(500), 80.5)]
    assert dao.rows == []


def test_delete_deletes_via_lookup():
    dao = _Pending([_write(1, 300, 70.0, kind="weight.delete")])
    api = FakeSyncApi()
    api.find_result = Ok(9)
    eng, _ = _engine(dao, api)
    eng.push()
    assert api.deleted_ids == [9]
    assert api.posted_iso == []
    assert dao.rows == []


def test_delete_server_row_gone_succeeds_without_delete():
    dao = _Pending([_write(1, 300, 70.0, kind="weight.delete")])
    api = FakeSyncApi()
    api.find_result = Ok(None)
    eng, _ = _engine(dao, api)
    eng.push()
    assert api.deleted_ids == []
    assert dao.rows == []


def test_update_server_row_missing_dead_lettered():
    dao = _Pending([_write(1, 300, 70.0, kind="weight.update")])
    api = FakeSyncApi()
    api.find_result = Ok(None)
    eng, _ = _engine(dao, api)
    eng.push()
    assert api.patched == []
    assert [r.id for r in dao.rows] == [1]
    assert "stale" in dao.failures[0][2]


def test_rejected_4xx_dead_letters_valid_rows_drain():
    dao = _Pending([_write(1, 100, 70.0), _write(2, 200, 71.0)])
    api = FakeSyncApi()
    api.post_results.append(Rejected(422, "bad value"))
    eng, _ = _engine(dao, api)
    eng.push()
    assert dao.deleted == [2]
    assert [r.id for r in dao.rows] == [1]
    assert "HTTP 422" in dao.failures[0][2]
    eng2, _ = _engine(dao, api)
    eng2.push()
    assert len(api.posted_iso) == 2


def test_unparseable_dead_lettered_never_pushed():
    bad = PendingWrite(id=9, kind="weight",
                       payload_json='{"source_id":"manual","measured_at":}', created_at=50)
    dao = _Pending([bad])
    api = FakeSyncApi()
    eng, _ = _engine(dao, api)
    eng.push()
    assert api.posted_iso == []
    assert [r.id for r in dao.rows] == [9]
    assert "unparseable" in dao.failures[0][2]
    eng2, _ = _engine(dao, api)
    eng2.push()
    assert len(dao.failures) == 1 and api.posted_iso == []


def test_unknown_kind_dead_lettered():
    unknown = PendingWrite(id=5, kind="future-kind",
                           payload_json=_payload(10, 60.0), created_at=10)
    dao = _Pending([unknown, _write(6, 200, 71.0)])
    api = FakeSyncApi()
    eng, _ = _engine(dao, api)
    eng.push()
    assert api.posted_iso == [to_iso(200)]
    assert dao.deleted == [6]
    assert [r.id for r in dao.rows] == [5]
    assert any(f[0] == 5 and "unknown kind" in f[2] for f in dao.failures)


def test_auth_aborts_rows_stay_queued():
    dao = _Pending([_write(1, 100, 70.0), _write(2, 200, 71.0)])
    api = FakeSyncApi()
    api.post_results.append(AuthExpired())
    eng, _ = _engine(dao, api)
    with pytest.raises(SyncAuthException):
        eng.push()
    assert dao.deleted == []
    assert [r.id for r in dao.rows] == [1, 2]


def test_pause_on_507():
    dao = _Pending([_write(1, 100, 70.0), _write(2, 200, 71.0), _write(3, 300, 72.0)])
    api = FakeSyncApi()
    api.post_results.extend([Ok(None), VolumeFull()])
    delays = []
    eng, _ = _engine(dao, api, delays=delays)
    eng.push()
    assert dao.deleted == [1]
    assert [r.id for r in dao.rows] == [2, 3]
    assert len(api.posted_iso) == 2
    assert delays


def test_transient_backs_off_then_deletes():
    dao = _Pending([_write(1, 100, 70.0)])
    api = FakeSyncApi()
    api.post_results.extend([Transient("reset")] * 3)
    delays = []
    eng, _ = _engine(dao, api, delays=delays)
    eng.push()
    assert delays == [1000, 2000, 4000]
    assert dao.deleted == [1]
    assert len(api.posted_iso) == 4


def test_persistent_transient_throws_retryable():
    dao = _Pending([_write(1, 100, 70.0)])
    api = FakeSyncApi()
    api.post_results.extend([Transient("offline")] * 20)
    delays = []
    eng, _ = _engine(dao, api, delays=delays)
    with pytest.raises(SyncRetryableException):
        eng.push()
    assert max(delays) == 60_000
    assert [r.id for r in dao.rows] == [1]


def test_first_push_preserves_pull_cursor():
    dao = _Pending([_write(1, 100, 70.0)])
    sync = _Sync(SyncState(id=1, last_pull_at=500, last_push_at=None, last_pull_iso=None))
    api = FakeSyncApi()
    eng = SyncEngine(pending_dao=dao, sync_dao=sync, weight_dao=_Weights(),
                     activity_dao=_Activities(), daily_dao=_Dailies(), api=api,
                     now=lambda: 42, delay=lambda ms: None)
    eng.push()
    assert sync.state.last_push_at == 42
    assert sync.state.last_pull_at == 500


def test_daily_and_activity_kinds_drain():
    metric = DailyMetric(source_id="phone", date="2026-09-12", steps=1000,
                         distance_m=700.0, active_min=10, effort_points=10,
                         calories_kcal=None, derived_mask=7)
    dao = _Pending([PendingWrite(id=11, kind=entries.KIND_DAILY,
                                 payload_json=entries.daily_payload(metric), created_at=1)])
    api = FakeSyncApi()
    eng, _ = _engine(dao, api)
    eng.push()
    assert dao.deleted == [11]
    dao2 = _Pending([PendingWrite(
        id=12, kind=entries.KIND_ACTIVITY,
        payload_json=entries.activity_payload(
            source_id="manual", type_="walk", started_at=1000, ended_at=2000,
            distance_m=None, calories_kcal=None, effort_points=20,
            active_min=20, steps=None), created_at=1)])
    eng2, _ = _engine(dao2, FakeSyncApi())
    eng2.push()
    assert dao2.deleted == [12]


def test_empty_queue_no_api_call():
    dao = _Pending()
    sync = _Sync()
    api = FakeSyncApi()
    eng = SyncEngine(pending_dao=dao, sync_dao=sync, weight_dao=_Weights(),
                     activity_dao=_Activities(), daily_dao=_Dailies(), api=api)
    eng.push()
    assert api.posted_iso == []
    assert sync.state is None


def test_dead_letter_never_retried_and_never_stars():
    dead = PendingWrite(id=3, kind="weight", payload_json=_payload(400, 75.0),
                        created_at=400, attempts=engine_mod.DEAD_LETTER_ATTEMPTS,
                        last_error="permanent: HTTP 422")
    dao = _Pending([dead])
    api = FakeSyncApi()
    eng, _ = _engine(dao, api)
    eng.push()
    assert api.posted_iso == []
    assert dao.failures == []
    page = [_Pending([PendingWrite(
        id=i, kind="weight", payload_json=_payload(i * 100, 70.0), created_at=i * 100,
        attempts=engine_mod.DEAD_LETTER_ATTEMPTS, last_error="permanent") for i in range(1, 51)])]
    good = _write(51, 9_999_900, 80.0)
    dao2 = _Pending(page[0].rows + [good])
    api2 = FakeSyncApi()
    eng2, _ = _engine(dao2, api2)
    eng2.push()
    assert api2.posted_iso == [to_iso(9_999_900)]
    assert len(dao2.rows) == 50


def _pull_engine(state, api, weights=None, activities=None, dailies=None):
    weights = weights if weights is not None else _Weights()
    activities = activities if activities is not None else _Activities()
    dailies = dailies if dailies is not None else _Dailies()
    sync = state if isinstance(state, _Sync) else _Sync(state)
    eng = SyncEngine(pending_dao=_Pending(), sync_dao=sync, weight_dao=weights,
                     activity_dao=activities, daily_dao=dailies, api=api,
                     now=lambda: 42, delay=lambda ms: None)
    return eng, sync, weights


def _sample(source_id, measured_at, weight_kg):
    return WeightSample(source_id=source_id, measured_at=measured_at,
                        weight_kg=weight_kg)


def test_first_pull_epoch_merges_and_advances_to_server_stamp():
    weights = _Weights()
    api = FakeSyncApi()
    api.pull_result = Ok(ChangesPage(
        weight=[_sample("manual", 1000, 70.0), _sample("manual", 2000, 71.0)],
        activities=[], dailies=[], max_batch_id=12))
    api.batch_stamps = [BatchStamp(11, to_iso(5_000)), BatchStamp(12, to_iso(9_000))]
    eng, sync, _ = _pull_engine(None, api, weights=weights)
    eng.pull()
    assert api.since_calls == ["1970-01-01T00:00:00+00:00"]
    assert [s.measured_at for s in weights.samples] == [1000, 2000]
    assert sync.state.last_pull_iso == to_iso(9_000)


def test_subsequent_pull_sends_stored_iso_empty_advances_nothing():
    iso = "2026-08-01T08:00:00+00:00"
    api = FakeSyncApi()
    api.pull_result = Ok(ChangesPage([], [], [], max_batch_id=0))
    sync = _Sync(SyncState(1, 1, None, iso))
    eng, sync, _ = _pull_engine(sync, api)
    eng.pull()
    assert api.since_calls == [iso]
    assert sync.state.last_pull_iso == iso


def test_pull_preserves_microsecond_cursor():
    iso = "2026-08-01T08:00:00.123456+00:00"
    api = FakeSyncApi()
    api.pull_result = Ok(ChangesPage([], [], [], max_batch_id=4))
    api.batch_stamps = [BatchStamp(4, iso)]
    eng, sync, _ = _pull_engine(None, api)
    eng.pull()
    assert sync.state.last_pull_iso == iso
    second = FakeSyncApi()
    second.pull_result = Ok(ChangesPage([], [], [], max_batch_id=0))
    eng2, _, _ = _pull_engine(sync, second)
    eng2.pull()
    assert second.since_calls == [iso]


def test_pull_preserves_push_stamp():
    api = FakeSyncApi()
    api.pull_result = Ok(ChangesPage([], [], [], max_batch_id=3))
    api.batch_stamps = [BatchStamp(3, to_iso(777))]
    sync = _Sync(SyncState(1, last_pull_at=10, last_push_at=8, last_pull_iso="old"))
    eng, sync, _ = _pull_engine(sync, api)
    eng.pull()
    assert sync.state.last_push_at == 8
    assert sync.state.last_pull_iso == to_iso(777)


def test_empty_delta_writes_no_state():
    api = FakeSyncApi()
    api.pull_result = Ok(ChangesPage([], [], [], max_batch_id=0))
    eng, sync, _ = _pull_engine(None, api)
    eng.pull()
    assert sync.upserted == []
    assert sync.state is None


def test_watermark_failure_keeps_cursor_but_merges():
    weights = _Weights()
    api = FakeSyncApi()
    api.pull_result = Ok(ChangesPage(
        weight=[_sample("manual", 1000, 70.0)], activities=[], dailies=[], max_batch_id=4))
    api.batch_result = Transient("flaky")
    sync = _Sync(SyncState(1, 100, 50, None))
    eng, sync, _ = _pull_engine(sync, api, weights=weights)
    eng.pull()
    assert len(weights.samples) == 1
    assert sync.state.last_pull_at == 100


def test_pull_errors_map():
    for result, exc in [(Transient("x"), SyncRetryableException),
                        (Rejected(400, "bad"), SyncPermanentException),
                        (AuthExpired(), SyncAuthException)]:
        api = FakeSyncApi()
        api.pull_result = result
        eng, _, _ = _pull_engine(None, api)
        with pytest.raises(exc):
            eng.pull()


def test_end_to_end_offline_queue_then_push_then_pull(tmp_path):
    db = Database(tmp_path / "vitals.db")
    db.init()
    repo = entries.WeightRepository(db)
    sample = WeightSample(source_id="manual", measured_at=1_000, weight_kg=70.0)
    repo.add_weight(sample, entries.weight_payload("manual", 1_000, 70.0))
    assert db.pending_count() == 1
    api = FakeSyncApi()
    api.batch_stamps = []
    api.pull_result = Ok(ChangesPage([], [], [], max_batch_id=0))
    eng = SyncEngine(pending_dao=db, sync_dao=db, weight_dao=db,
                     activity_dao=db, daily_dao=db, api=api, now=lambda: 42,
                     delay=lambda ms: None)
    eng.push()
    assert db.pending_count() == 0
    eng.pull()
    assert db.pending_count() == 0


def test_first_push_without_prior_state_invents_no_cursor():
    dao = _Pending([_write(1, 100, 70.0)])
    sync = _Sync()
    api = FakeSyncApi()
    eng = SyncEngine(pending_dao=dao, sync_dao=sync, weight_dao=_Weights(),
                     activity_dao=_Activities(), daily_dao=_Dailies(), api=api,
                     now=lambda: 42, delay=lambda ms: None)
    assert eng.push() is False
    assert sync.state.last_push_at == 42
    assert sync.state.last_pull_at is None
    assert sync.state.last_pull_iso is None


def test_after_first_push_stamp_never_rewritten():
    dao = _Pending([_write(1, 100, 70.0)])
    sync = _Sync(SyncState(id=1, last_pull_at=7, last_push_at=8, last_pull_iso=None))
    api = FakeSyncApi()
    eng = SyncEngine(pending_dao=dao, sync_dao=sync, weight_dao=_Weights(),
                     activity_dao=_Activities(), daily_dao=_Dailies(), api=api,
                     now=lambda: 42, delay=lambda ms: None)
    eng.push()
    assert sync.state.last_push_at == 8
    assert sync.upserted == []


def test_push_reports_pause_on_507_with_first_backoff_delay():
    dao = _Pending([_write(1, 100, 70.0), _write(2, 200, 71.0)])
    api = FakeSyncApi()
    api.post_results.extend([Ok(None), VolumeFull()])
    delays = []
    eng, _ = _engine(dao, api, delays=delays)
    assert eng.push() is True
    assert delays == [engine_mod.BACKOFF_MILLIS[0]]
    assert dao.deleted == [1]
    assert [r.id for r in dao.rows] == [2]


def test_watermark_auth_raises_even_after_merging_rows():
    weights = _Weights()
    api = FakeSyncApi()
    api.pull_result = Ok(ChangesPage(
        weight=[_sample("manual", 1000, 70.0)], activities=[], dailies=[],
        max_batch_id=2))
    api.batch_result = AuthExpired()
    eng, _, _ = _pull_engine(None, api, weights=weights)
    with pytest.raises(SyncAuthException):
        eng.pull()
    assert len(weights.samples) == 1


def test_run_pass_pushes_only_when_queued_then_always_pulls():
    from xx_vitals.sync import worker as worker_module

    assert worker_module.should_sync(0) is False
    assert worker_module.should_sync(3) is True

    class _RecordingEngine:
        def __init__(self):
            self.pushes = 0
            self.pulls = 0

        def pending_change_count(self):
            return 0

        def push(self):
            self.pushes += 1
            return False

        def pull(self):
            self.pulls += 1

    eng = _RecordingEngine()
    assert worker_module.run_sync_pass(eng) == {"paused": False, "pending": 0}
    assert (eng.pushes, eng.pulls) == (0, 1)


def test_real_db_push_pull_round_trip_with_pull_rows(tmp_path):
    db = Database(tmp_path / "vitals.db")
    db.init()
    api = FakeSyncApi()
    api.pull_result = Ok(ChangesPage(
        weight=[WeightSample(source_id="manual", measured_at=9_000, weight_kg=72.0)],
        activities=[Activity(source_id="manual", type="walk",
                             started_at=1_000, ended_at=2_000, steps=500)],
        dailies=[DailyMetric(source_id="manual", date="2026-09-12",
                              steps=500, derived_mask=0)],
        max_batch_id=7))
    api.batch_stamps = [BatchStamp(7, to_iso(11_000))]
    eng = SyncEngine(pending_dao=db, sync_dao=db, weight_dao=db,
                     activity_dao=db, daily_dao=db, api=api, now=lambda: 42,
                     delay=lambda ms: None)
    eng.pull()
    assert db.count_weights() == 1
    assert db.count_activities() == 1
    assert db.count_dailies() == 1
    assert db.sync_get().last_pull_iso == to_iso(11_000)
