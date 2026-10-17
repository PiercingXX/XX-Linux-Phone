"""Session sync: 15 s cadence, immediate writes, reopen rule, LWW."""

import json

from fake_server import PLAY_1

from xx_audiobook import api as api_module
from xx_audiobook import store as store_module
from xx_audiobook.api import AudiobookApi
from xx_audiobook.repos import ProgressRepository
from xx_audiobook.sync import (
    BACKOFF_INTERVAL_MS,
    INTERVAL_MS,
    ProgressSync,
    Reason,
    SyncScheduler,
    Tick,
    is_gone,
)


class FakeEnv:
    def __init__(self) -> None:
        self.item = "it-1"
        self.playing = False
        self.position = 0
        self.chapter = 0
        self.screen = True
        self.save = False

    def item_id(self):
        return self.item

    def is_playing(self):
        return self.playing

    def position_ms(self):
        return self.position

    def chapter_index(self):
        return self.chapter

    def screen_on(self):
        return self.screen

    def power_save(self):
        return self.save


def _scheduler(env=None, now=None):
    env = env or FakeEnv()
    clock = now if now is not None else [1000000]
    return SyncScheduler(env, lambda: clock[0]), env, clock


def test_interval_write_every_15s_while_playing():
    scheduler, env, clock = _scheduler()
    env.playing = True
    scheduler.start()
    clock[0] += 5000
    assert scheduler.tick() is None
    clock[0] += 10000
    tick = scheduler.tick()
    assert tick is not None
    assert tick.reason == Reason.INTERVAL
    assert tick.item_id == "it-1"
    assert scheduler.tick() is None


def test_backoff_to_60s_when_screen_off_or_power_save():
    scheduler, env, clock = _scheduler()
    assert scheduler.interval_ms() == INTERVAL_MS
    env.screen = False
    assert scheduler.interval_ms() == BACKOFF_INTERVAL_MS
    env.screen = True
    env.save = True
    assert scheduler.interval_ms() == BACKOFF_INTERVAL_MS
    env.save = False
    env.playing = True
    scheduler.start()
    clock[0] += 30000
    env.screen = False
    assert scheduler.tick() is None
    clock[0] += 30000
    assert scheduler.tick().reason == Reason.INTERVAL


def test_immediate_writes_on_pause_seek_chapter_stop():
    scheduler, env, clock = _scheduler()
    env.playing = True
    scheduler.start()
    clock[0] += 1000
    assert scheduler.tick() is None
    env.position = 5000
    assert scheduler.on_pause().reason == Reason.PAUSE
    assert scheduler.on_seek().reason == Reason.SEEK
    env.chapter = 1
    clock[0] += 1000
    assert scheduler.tick().reason == Reason.CHAPTER
    clock[0] += 1000
    assert scheduler.tick() is None
    assert scheduler.on_stop().reason == Reason.STOP


def test_listened_is_monotonic_with_5s_max_gap():
    scheduler, env, clock = _scheduler()
    env.playing = True
    scheduler.start()
    clock[0] += 2000
    scheduler.tick()
    assert scheduler.listened_now() == 2000
    clock[0] += 60000
    scheduler.tick()
    assert scheduler.listened_now() == 7000
    env.playing = False
    clock[0] += 60000
    scheduler.tick()
    assert scheduler.listened_now() == 7000


def test_reset_on_book_change_and_stop_without_item():
    scheduler, env, clock = _scheduler()
    env.playing = True
    scheduler.start()
    clock[0] += 4000
    scheduler.tick()
    env.item = "it-2"
    clock[0] += 1000
    scheduler.tick()
    assert scheduler.listened_now() == 0
    clock[0] += 1000
    scheduler.tick()
    assert scheduler.listened_now() == 1000
    env.item = None
    assert scheduler.tick() is None
    assert scheduler.on_pause() is None


def test_is_gone_rule():
    assert is_gone(api_module.Http(404, None, "gone")) is True
    assert is_gone(api_module.Http(
        409, "session_closed", "closed")) is True
    assert is_gone(api_module.Http(409, "other", "x")) is False
    assert is_gone(api_module.Http(500, None, "boom")) is False


def _sync(server, **kwargs):
    db = store_module.Database.in_memory()
    client = AudiobookApi(server.api_base, "tok")
    repo = ProgressRepository(client, db,
                              now_ms=lambda: 1790000000000)
    played = []
    flushed = []
    remapped = []
    sync = ProgressSync(
        client, repo,
        set_last_played=lambda item, pos: played.append((item, pos)),
        request_flush=lambda: flushed.append(True),
        on_session_changed=lambda i, o, n: remapped.append((i, o, n)),
        **kwargs)
    return db, repo, sync, played, flushed, remapped


def _tick(reason=Reason.INTERVAL, item="it-1", position_ms=120000,
          listened_ms=15000):
    return Tick(reason, item, position_ms, listened_ms)


def test_write_posts_session_sync_and_acks_queue(fake_server):
    db, repo, sync, played, flushed, _remapped = _sync(fake_server)
    try:
        sync.write(_tick(), "ps-1", 7200000, 1.0)
        assert played == [("it-1", 120.0)]
        assert flushed == []
        assert repo.pending_count() == 0
        _method, _path, _headers, body, _query = fake_server.last_call(
            "POST", "/audiobook/api/sessions/ps-1/sync")
        sent = json.loads(body)
        assert sent["position_s"] == 120.0
        assert sent["listened_s"] == 15.0
        assert sent["speed"] == 1.0
        assert sent["updated_at"] == 1790000000.0
    finally:
        db.close()


def test_stop_closes_and_pause_seek_stop_log_history(fake_server):
    db, repo, sync, _played, _flushed, _remapped = _sync(fake_server)
    try:
        sync.write(_tick(Reason.PAUSE), "ps-1", 7200000, 1.0)
        sync.write(_tick(Reason.SEEK), "ps-1", 7200000, 1.0)
        sync.write(_tick(Reason.INTERVAL), "ps-1", 7200000, 1.0)
        kinds = [event.event for event in db.history().recent(10)]
        assert "pause" in kinds and "seek" in kinds
        assert "interval" not in kinds
        sync.write(_tick(Reason.STOP), "ps-1", 7200000, 1.0)
        assert fake_server.last_call(
            "POST", "/audiobook/api/sessions/ps-1/close") is not None
        assert sync.is_closed("ps-1") is True
    finally:
        db.close()


def test_gone_session_reopens_and_resends_with_zero_listened(fake_server):
    db, repo, sync, _played, flushed, remapped = _sync(fake_server)
    try:
        play_doc = dict(PLAY_1, session_id="ps-2")
        fake_server.queue("POST", "/audiobook/api/sessions/ps-1/sync",
                          404, {"detail": "gone"})
        fake_server.queue("POST", "/audiobook/api/items/it-1/play",
                          200, play_doc)
        sync.write(_tick(), "ps-1", 7200000, 1.0)
        assert remapped == [("it-1", "ps-1", "ps-2")]
        assert sync.live_session("ps-1") == "ps-2"
        assert flushed == []
        assert repo.pending_count() == 0
        _method, _path, _headers, body, _query = fake_server.last_call(
            "POST", "/audiobook/api/sessions/ps-2/sync")
        assert json.loads(body)["listened_s"] == 0.0
    finally:
        db.close()


def test_dropped_session_goes_by_put_and_marks_unsynced(fake_server):
    db, repo, sync, _played, flushed, remapped = _sync(fake_server)
    try:
        fake_server.queue("POST", "/audiobook/api/sessions/ps-9/sync",
                          404, {"detail": "gone"})
        fake_server.queue("POST", "/audiobook/api/items/it-1/play",
                          404, {"detail": "gone"})
        sync.write(_tick(), "ps-9", 7200000, 1.0)
        assert remapped == [("it-1", "ps-9", None)]
        assert sync.live_session("ps-9") is None
        assert flushed == [True]
        assert repo.pending_count() == 1
    finally:
        db.close()


def test_server_error_leaves_queue_and_asks_flush(fake_server):
    db, repo, sync, _played, flushed, _remapped = _sync(fake_server)
    try:
        fake_server.queue("POST", "/audiobook/api/sessions/ps-1/sync",
                          500, {"detail": "boom"})
        sync.write(_tick(), "ps-1", 7200000, 1.0)
        assert flushed == [True]
        assert repo.pending_count() == 1
    finally:
        db.close()


def test_progress_applied_false_merges_server_row(fake_server):
    db, repo, sync, _played, _flushed, _remapped = _sync(fake_server)
    try:
        db.progress().upsert(store_module.Progress(
            "it-1", 100.0, 7200.0, False, 1000))
        server_row = {"item_id": "it-1", "position_s": 4100.0,
                      "duration_s": 7200.0, "speed": 1.0,
                      "is_finished": False, "started_at": 1790000000.0,
                      "finished_at": None, "last_played_at": 1790003600.0,
                      "updated_at": 1790003600.0}
        fake_server.queue("POST", "/audiobook/api/sessions/ps-1/sync",
                          200, {"session_id": "ps-1", "item_id": "it-1",
                                "position_s": 100.0, "listened_s": 15.0,
                                "speed": 1.0, "closed_at": None,
                                "progress_applied": False,
                                "progress": server_row})
        sync.write(_tick(), "ps-1", 7200000, 1.0)
        assert db.progress().get("it-1").position_s == 4100.0
    finally:
        db.close()


def test_offline_session_records_locally_and_flushes_later(fake_server):
    db, repo, sync, _played, flushed, _remapped = _sync(fake_server)
    try:
        sync.write(_tick(), None, 7200000, 1.0)
        assert db.progress().get("it-1").position_s == 120.0
        assert flushed == [True]
        assert repo.pending_count() == 1
        assert sync.last_listened_ms(None) == 0
    finally:
        db.close()
