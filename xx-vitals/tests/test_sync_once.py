"""--sync-once headless entry: dispatch and exit codes.

No display, no session bus, no real keyring: libsecret, HTTP and the database
are all injected fakes. Exit codes: 0 synced, 1 transient failure,
2 not configured, 3 re-login required.
"""

import os

os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent-test-bus")

from xx_vitals import main as main_module
from xx_vitals import prefs as prefs_module
from xx_vitals.store import entries
from xx_vitals.store.db import Database
from xx_vitals.sync import auth as auth_module
from xx_vitals.sync.api import AuthExpired, ChangesPage, FakeSyncApi, Ok, VolumeFull


def _patch_env(monkeypatch, cfg, secrets, db, api):
    monkeypatch.setattr(main_module, "ensure_app_dirs", lambda: {})
    monkeypatch.setattr(main_module, "load_prefs", lambda: cfg)
    monkeypatch.setattr(auth_module, "LibsecretBackend", lambda: secrets)
    monkeypatch.setattr("xx_vitals.sync.http_api.HttpSyncApi", lambda *a, **k: api)
    monkeypatch.setattr("xx_vitals.store.db.Database", lambda *a, **k: db)


def test_sync_once_flag_dispatches(monkeypatch):
    calls: list = []
    monkeypatch.setattr(main_module, "sync_once", lambda: calls.append(1) or 7)
    assert main_module.main(["--sync-once"]) == 7
    assert calls == [1]


def test_sync_once_unconfigured_returns_2(tmp_path, monkeypatch, capsys):
    db = Database(tmp_path / "vitals.db")
    db.init()
    _patch_env(monkeypatch, prefs_module.AppConfig(),
               auth_module.MemorySecretBackend(), db, FakeSyncApi())
    assert main_module.sync_once() == 2
    assert "not configured" in capsys.readouterr().out


def test_sync_once_clean_pass_returns_0(tmp_path, monkeypatch, capsys):
    db = Database(tmp_path / "vitals.db")
    db.init()
    api = FakeSyncApi()
    api.pull_result = Ok(ChangesPage([], [], [], max_batch_id=0))
    cfg = prefs_module.AppConfig(server_url="http://127.0.0.1:9")
    secrets = auth_module.MemorySecretBackend()
    secrets.store_token("http://127.0.0.1:9", "tok")
    _patch_env(monkeypatch, cfg, secrets, db, api)
    assert main_module.sync_once() == 0
    assert "Sync done: 0 queued" in capsys.readouterr().out


def test_sync_once_401_returns_3_with_relogin_copy(tmp_path, monkeypatch, capsys):
    db = Database(tmp_path / "vitals.db")
    db.init()
    api = FakeSyncApi()
    api.pull_result = AuthExpired()
    cfg = prefs_module.AppConfig(server_url="http://127.0.0.1:9")
    secrets = auth_module.MemorySecretBackend()
    secrets.store_token("http://127.0.0.1:9", "tok")
    _patch_env(monkeypatch, cfg, secrets, db, api)
    assert main_module.sync_once() == 3
    assert "sign in again" in capsys.readouterr().out


def test_sync_once_507_keeps_queue_and_says_storage_full(
        tmp_path, monkeypatch, capsys):
    db = Database(tmp_path / "vitals.db")
    db.init()
    db.pending_insert(entries.PendingWrite(
        kind="weight",
        payload_json=entries.weight_payload("manual", 1000, 70.0),
        created_at=1000))
    api = FakeSyncApi()
    api.post_results.append(VolumeFull())
    api.pull_result = Ok(ChangesPage([], [], [], max_batch_id=0))
    cfg = prefs_module.AppConfig(server_url="http://127.0.0.1:9")
    secrets = auth_module.MemorySecretBackend()
    secrets.store_token("http://127.0.0.1:9", "tok")
    _patch_env(monkeypatch, cfg, secrets, db, api)
    assert main_module.sync_once() == 0
    out = capsys.readouterr().out
    assert "storage full" in out and "queue kept" in out
    assert db.pending_count() == 1
