"""Profile sync UI helpers: login copy, setup gating, state lines, host save.

Headless: no display, no keyring (MemorySecretBackend only), HOME pointed at
a temp dir so host persistence never touches the real config.
"""

import base64
import json
import os
from pathlib import Path

os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent-test-bus")

from xx_vitals import prefs as prefs_module
from xx_vitals.store import entries
from xx_vitals.store.db import Activity, DailyMetric, Database, SyncState
from xx_vitals.sync import auth as auth_module
from xx_vitals.ui import profile as profile_page


def _b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _token(user_id="operator-7"):
    claims = json.dumps({"user_id": user_id, "iat": 1, "exp": 1900000000})
    return f"v1.{_b64('k')}.{_b64(claims)}.{_b64('sig')}"


def _success(token):
    return auth_module.LoginSuccess(token=token, expires_at_ms=None)


def test_login_copy_names_identity_or_plain_signed_in():
    assert profile_page.format_login_message(
        _success(_token())) == "Signed in · operator-7"
    assert profile_page.format_login_message(
        _success("opaque-token")) == "Signed in"


def test_login_failure_copy_is_the_server_message():
    failure = auth_module.LoginFailure(401, "Wrong username or password")
    assert profile_page.format_login_message(failure) == "Wrong username or password"


def test_setup_copy_gates_sync_now():
    creds = auth_module.SyncCredentials(
        nas_host="http://127.0.0.1:8080", xxchat_host=None,
        token="tok", relogin_required=False)
    assert profile_page.sync_setup_copy(creds) is None
    assert profile_page.sync_setup_copy(auth_module.SyncCredentials(
        None, None, None, False)) == "Set the NAS host and sign in to sync."
    assert profile_page.sync_setup_copy(None) == "Set the NAS host and sign in to sync."


def test_save_nas_host_persists_valid_url(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = prefs_module.AppConfig()
    assert profile_page.save_nas_host(cfg, " http://192.168.1.50:8080/ ") is None
    assert cfg.server_url == "http://192.168.1.50:8080"
    assert prefs_module.load().server_url == "http://192.168.1.50:8080"


def test_save_nas_host_rejects_garbage_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = prefs_module.AppConfig()
    assert profile_page.save_nas_host(cfg, "nas.local:8080") is not None
    assert cfg.server_url == ""
    assert not (tmp_path / ".config" / "xx-vitals" / "config.json").exists()


def test_save_xxchat_host_validates_like_nas(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = prefs_module.AppConfig()
    assert profile_page.save_xxchat_host(cfg, "https://chat.example.lan/") is None
    assert cfg.xxchat_url == "https://chat.example.lan"
    assert profile_page.save_xxchat_host(cfg, "notaurl") is not None


def _seeded_db(tmp_path) -> Database:
    db = Database(tmp_path / "vitals.db")
    db.init()
    ms = 1785574800000
    db.upsert_weight(entries.WeightSample(
        source_id="manual", measured_at=ms, weight_kg=80.0))
    db.upsert_daily(DailyMetric(
        source_id="manual", date="2026-08-01", steps=9000, derived_mask=0))
    db.insert_activity(Activity(
        source_id="manual", type="walk", started_at=ms - 3600000,
        ended_at=ms))
    db.pending_insert(entries.PendingWrite(
        kind="weight", payload_json=entries.weight_payload("manual", ms, 80.0),
        created_at=ms))
    db.pending_insert(entries.PendingWrite(
        kind="daily", payload_json=entries.daily_payload(DailyMetric(
            source_id="manual", date="2026-08-01", steps=9000,
            derived_mask=0)), created_at=ms))
    db.sync_upsert(SyncState(
        id=1, last_pull_at=ms, last_push_at=ms - 1000,
        last_pull_iso="2026-08-01T09:00:00+00:00"))
    return db


def test_refresh_sync_state_renders_seeded_rows(tmp_path):
    db = _seeded_db(tmp_path)
    cfg = prefs_module.AppConfig(server_url="http://127.0.0.1:8080")
    secrets = auth_module.MemorySecretBackend()
    secrets.store_token("http://127.0.0.1:8080", _token())
    state = profile_page.refresh_sync_state(db, cfg, secrets)
    assert state["host"] == "http://127.0.0.1:8080"
    assert state["login"] == "signed in · operator-7"
    assert state["last_pull"].startswith("2026-")
    assert state["last_push"].startswith("2026-")
    assert state["pending"] == "2 queued"
    assert state["can_sync"] is True


def test_refresh_sync_state_honest_gaps_and_relogin(tmp_path):
    db = Database(tmp_path / "vitals.db")
    db.init()
    cfg = prefs_module.AppConfig()
    secrets = auth_module.MemorySecretBackend()
    state = profile_page.refresh_sync_state(db, cfg, secrets)
    assert state["host"] == "— not configured —"
    assert state["login"] == "not signed in"
    assert state["last_pull"] == "—"
    assert state["pending"] == "0 queued"
    assert state["can_sync"] is False


def test_libsecret_sync_calls_live_only_in_auth_backend():
    src = Path(__file__).parent.parent / "src" / "xx_vitals"
    hits: dict[str, list[str]] = {}
    for path in sorted(src.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found = [name for name in (
            "password_store_sync", "password_lookup_sync", "password_clear_sync",
        ) if name in text]
        if found:
            hits[str(path.relative_to(src))] = found
    assert hits == {"sync/auth.py": [
        "password_store_sync", "password_lookup_sync", "password_clear_sync"]}


def test_ui_never_references_secret_service():
    src = Path(__file__).parent.parent / "src" / "xx_vitals"
    for name in ("ui/profile.py", "ui/shell.py", "main.py"):
        text = (src / name).read_text(encoding="utf-8")
        assert "password_store_sync" not in text, name
        assert "password_lookup_sync" not in text, name
        assert "Secret" not in text, name


def test_suite_never_touches_a_real_keyring():
    tests = Path(__file__).parent
    needles = (
        "LibsecretBackend" + "(",
        "Secret" + ".password",
        "Secret" + ".Schema",
        "Secret" + ".COLLECTION",
    )
    users = [
        path.name for path in sorted(tests.glob("test_*.py"))
        if any(needle in path.read_text(encoding="utf-8") for needle in needles)
    ]
    assert users == []
