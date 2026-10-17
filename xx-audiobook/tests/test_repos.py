"""Library cache and settings sync against the fake server."""

import json

from fake_server import DETAIL_1
from xx_audiobook import api as api_module
from xx_audiobook import prefs as prefs_module
from xx_audiobook import repos as repos_module
from xx_audiobook import store as store_module
from xx_audiobook.api import ApiError, AudiobookApi
from xx_audiobook.repos import (
    LibraryRepository,
    SettingsSync,
)


def _library(server, now=None):
    db = store_module.Database.in_memory()
    client = AudiobookApi(server.api_base, "tok")
    repo = LibraryRepository(client, db,
                             now_ms=lambda: (now or [1000000])[0])
    return db, repo


def test_library_refresh_caches_and_serves_offline(fake_server):
    db, repo = _library(fake_server)
    try:
        ok, more, error = repo.refresh(store_module.LibraryQuery(limit=50))
        assert (ok, more, error) == (True, False, None)
        rows = repo.observe(store_module.LibraryQuery())
        assert [r.title for r in rows] == ["Project Hail Mary",
                                           "The Long Way"]
        long_way = next(r for r in rows if r.id == "it-1")
        assert long_way.authors == "Becky Chambers"
        assert long_way.series == "Wayfarers #1"
        assert long_way.position_s == 1200.0
        assert [r.id for r in repo.observe_continue()] == ["it-1"]

        detail, error = repo.refresh_detail("it-1")
        assert error is None
        assert len(detail.chapters) == 2

        offline = LibraryRepository(
            AudiobookApi(fake_server.api_base, "tok",
                         transport=_dead_transport),
            db, now_ms=lambda: 1000000)
        ok, _more, error = offline.refresh(store_module.LibraryQuery())
        assert ok is False
        assert isinstance(error, ApiError.Unreachable)
        assert len(offline.observe(
            store_module.LibraryQuery())) == 2
        cached, error = offline.detail("it-1")
        assert error is None
        assert cached.title == "The Long Way"
        assert offline.cached_detail("it-1").title == "The Long Way"
        assert offline.cached_detail("ghost") is None
    finally:
        db.close()


def _dead_transport(method, url, headers, body, timeout):
    raise ApiError.Unreachable("off")


def test_library_detail_missing_everywhere_reports_error(fake_server):
    db, repo = _library(fake_server)
    try:
        detail, error = repo.detail("ghost")
        assert detail is None
        assert error is not None
    finally:
        db.close()


def test_library_refresh_continue_and_ids(fake_server):
    db, repo = _library(fake_server)
    try:
        ok, error = repo.refresh_continue()
        assert (ok, error) == (True, None)
        assert [r.id for r in repo.observe_continue()] == ["it-1"]
        assert [r.id for r in repo.observe_ids(["it-1"])] == ["it-1"]
    finally:
        db.close()


class _Prefs:
    def __init__(self) -> None:
        self.state = prefs_module.Prefs()

    def current(self):
        return self.state

    def mark_settings_edited(self, at_s):
        self.state.settings_updated_at = at_s
        self.state.settings_dirty = True

    def mark_settings_synced(self, at_s):
        if self.state.settings_updated_at == at_s:
            self.state.settings_dirty = False

    def apply_remote_settings(self, skip_back, skip_forward, speed,
                              auto_rewind, at_s):
        self.state.skip_back_seconds = skip_back
        self.state.skip_seconds = skip_forward
        self.state.default_speed = speed
        self.state.auto_rewind_seconds = auto_rewind
        self.state.settings_updated_at = at_s
        self.state.settings_dirty = False


def _sync(server, prefs=None, now=None):
    prefs = prefs if prefs is not None else _Prefs()
    client = AudiobookApi(server.api_base, "tok")
    moment = now if now is not None else [1790003700.0]
    return SettingsSync(client, prefs,
                        now_s=lambda: moment[0]), prefs, moment


def test_settings_push_sends_only_owned_keys(fake_server):
    sync, prefs, _moment = _sync(fake_server)
    prefs.state.skip_seconds = 45
    assert sync.local_edited() is True
    assert prefs.state.settings_dirty is False
    _method, _path, _headers, body, _query = fake_server.last_call(
        "PUT", "/audiobook/api/me/settings")
    assert json.loads(body) == {
        "skip_back_s": 30, "skip_forward_s": 45, "default_speed": 1.0,
        "auto_rewind_s": 2, "updated_at": 1790003700.0}


def test_settings_push_stays_dirty_offline(fake_server):
    db_port = fake_server.port
    fake_server.stop()
    prefs = _Prefs()
    client = AudiobookApi(f"http://127.0.0.1:{db_port}/audiobook/", "tok")
    sync = SettingsSync(client, prefs, now_s=lambda: 1790003700.0)
    prefs.state.skip_seconds = 45
    assert sync.local_edited() is False
    assert prefs.state.settings_dirty is True
    assert sync.push() is False


def test_settings_sync_pulls_newer_server_row(fake_server):
    sync, prefs, _moment = _sync(fake_server)
    assert sync.sync() is True
    assert prefs.state.skip_seconds == 30
    assert prefs.state.settings_dirty is False
    assert prefs.state.settings_updated_at == 1790000000.0


def test_settings_sync_keeps_local_when_server_never_saved(fake_server):
    fake_server.settings["updated_at"] = None
    sync, prefs, _moment = _sync(fake_server)
    prefs.state.skip_seconds = 45
    assert sync.sync() is True
    assert prefs.state.skip_seconds == 45


def test_settings_loser_applies_server_row(fake_server):
    sync, prefs, _moment = _sync(fake_server)
    prefs.state.skip_seconds = 45
    prefs.state.settings_dirty = True
    prefs.state.settings_updated_at = 1790000000.0
    fake_server.settings.update(skip_forward_s=10, skip_back_s=10,
                                updated_at=1790003800.0)
    assert sync.push() is True
    assert prefs.state.skip_seconds == 10
    assert prefs.state.settings_dirty is False


def test_prefs_store_marks_and_applies():
    store = prefs_module.PrefsStore()
    store.mark_settings_edited(100.0)
    assert store.current().settings_dirty is True
    store.mark_settings_synced(99.0)
    assert store.current().settings_dirty is True
    store.mark_settings_synced(100.0)
    assert store.current().settings_dirty is False
    store.apply_remote_settings(10, 15, 1.5, 5, 200.0)
    current = store.current()
    assert (current.skip_back_seconds, current.skip_seconds,
            current.default_speed, current.auto_rewind_seconds) == (
        10, 15, 1.5, 5)
    assert current.settings_updated_at == 200.0
    store.set_owner_user_id("u-9")
    assert store.current().owner_user_id == "u-9"


def test_rows_from_any_route_merge_progress_lww(fake_server):
    db, repo = _library(fake_server)
    try:
        detail = api_module.ItemDetail.from_dict(dict(DETAIL_1))
        summary = detail_to_summary(detail)
        db.progress().upsert(store_module.Progress(
            "it-1", 9999.0, 7200.0, False, 9999999999999))
        repo.cache_summaries([summary])
        assert db.progress().get("it-1").position_s == 9999.0
    finally:
        db.close()


def detail_to_summary(detail):
    wire = repos_module.detail_to_wire(detail)
    return api_module.ItemSummary.from_dict(wire)
