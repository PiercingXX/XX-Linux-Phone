"""Setup-flow tests: first-run account add, sign-out, keyring lock.

Covers the milestone 4 UI lane's headless side: the reported
postmarketOS bug (a "later lane" placeholder reached the user instead
of a setup screen), the BYO-client-ID sign-in completion, Google
revocation, local sign-out, the locked-keyring retry message, the
0700 first-run directories, and the loopback listener stopping after
the first valid callback.
"""

import base64
import json
import os
import stat
import urllib.parse
import urllib.request

import pytest

from xx_email import config, db
from xx_email.auth import AuthFlowError, LoopbackFlow
from xx_email.domain import OutboxFiles

CLIENT_ID = "1234567890-abcdef.apps.googleusercontent.com"
ACCOUNT = "user@example.com"


def make_id_token(payload):
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(
        b"=").decode("ascii")
    return f"header.{raw}.signature"


def token_transport(email=ACCOUNT, refresh="rt-1"):
    def _transport(body):
        return {"access_token": "tok-1", "refresh_token": refresh,
                "expires_in": 3600,
                "id_token": make_id_token({"email": email})}
    return _transport


@pytest.fixture
def conn():
    handle = db.connect(":memory:")
    db.migrate(handle)
    yield handle
    handle.close()


@pytest.fixture
def cfg_path(tmp_path):
    return tmp_path / "config.json"


def test_no_development_notes_in_shipped_sources():
    from pathlib import Path
    roots = [Path(__file__).resolve().parent.parent / "src",
             Path(__file__).resolve().parent.parent / "docs"]
    banned = ("later lane", "milestone 9", "milestone 7",
              "arriving in a later", "arrive in milestone",
              "your mail stays empty", "sign-in arrives")
    hits = []
    for root in roots:
        for path in sorted(root.rglob("*.py" if root.name == "src"
                                      else "*.md")):
            lowered = path.read_text(encoding="utf-8").lower()
            for phrase in banned:
                if phrase in lowered:
                    hits.append((str(path), phrase))
    assert hits == []


def test_oauth_guide_describes_desktop_client():
    from pathlib import Path
    guide = Path(__file__).resolve().parent.parent / "docs" \
        / "oauth-setup.md"
    assert guide.is_file(), "setup screen links to docs/oauth-setup.md"
    text = guide.read_text(encoding="utf-8")
    assert "Desktop" in text
    assert "127.0.0.1" in text
    assert "Application type: **Android**" not in text


def test_setup_page_carries_guide_and_keyring_text():
    from pathlib import Path
    page = Path(__file__).resolve().parent.parent / "src" / "xx_email" \
        / "ui" / "setup_page.py"
    assert page.is_file()
    text = page.read_text(encoding="utf-8")
    assert "Sign in with Google" in text
    assert "Unlock your keyring to continue" in text
    assert "docs/oauth-setup.md" in text
    assert "Desktop" in text


def test_browser_opens_through_uri_launcher():
    from pathlib import Path
    main = Path(__file__).resolve().parent.parent / "src" / "xx_email" \
        / "main.py"
    text = main.read_text(encoding="utf-8")
    assert "UriLauncher" in text
    assert "LoopbackFlow" in text
    assert "wait_for_code" in text


def test_finish_sign_in_stores_grant_registers_and_configures(
        conn, cfg_path):
    from xx_email import setup
    from xx_email.token_store import MemoryTokenBackend, TokenStore
    store = TokenStore(MemoryTokenBackend())
    email = setup.finish_sign_in(
        CLIENT_ID, "code-1", "http://127.0.0.1:9/oauth2redirect",
        "verifier-1", token_transport=token_transport(),
        store=store, conn=conn, config_path=cfg_path)
    assert email == ACCOUNT
    record = store.load(ACCOUNT)
    assert record is not None
    assert record.refresh_token == "rt-1"
    assert record.client_id == CLIENT_ID
    assert db.get_account(conn, ACCOUNT) is not None
    saved = config.load(cfg_path)
    assert saved.client_id == CLIENT_ID
    assert saved.last_account == ACCOUNT


def test_finish_sign_in_rejects_bad_client_id(conn, cfg_path):
    from xx_email import setup
    from xx_email.token_store import MemoryTokenBackend, TokenStore
    with pytest.raises(AuthFlowError):
        setup.finish_sign_in(
            "bogus", "code-1", "http://127.0.0.1:9/x", "v",
            token_transport=token_transport(),
            store=TokenStore(MemoryTokenBackend()), conn=conn,
            config_path=cfg_path)


def test_finish_sign_in_needs_email_and_refresh(conn, cfg_path):
    from xx_email import setup
    from xx_email.token_store import MemoryTokenBackend, TokenStore

    def no_email(body):
        return {"access_token": "t", "refresh_token": "r",
                "id_token": make_id_token({"sub": "123"})}

    with pytest.raises(AuthFlowError):
        setup.finish_sign_in(
            CLIENT_ID, "c", "http://127.0.0.1:9/x", "v",
            token_transport=no_email,
            store=TokenStore(MemoryTokenBackend()), conn=conn,
            config_path=cfg_path)

    def no_refresh(body):
        return {"access_token": "t",
                "id_token": make_id_token({"email": ACCOUNT})}

    with pytest.raises(AuthFlowError):
        setup.finish_sign_in(
            CLIENT_ID, "c", "http://127.0.0.1:9/x", "v",
            token_transport=no_refresh,
            store=TokenStore(MemoryTokenBackend()), conn=conn,
            config_path=cfg_path)


def test_finish_sign_in_preserves_existing_checkpoint(conn, cfg_path):
    from xx_email import setup
    from xx_email.token_store import MemoryTokenBackend, TokenStore
    db.upsert_account(conn, db.Account(
        email=ACCOUNT, display_name=ACCOUNT, history_id="888",
        last_sync_at=123))
    store = TokenStore(MemoryTokenBackend())
    setup.finish_sign_in(
        CLIENT_ID, "code-1", "http://127.0.0.1:9/oauth2redirect",
        "verifier-1", token_transport=token_transport(),
        store=store, conn=conn, config_path=cfg_path)
    assert db.get_account(conn, ACCOUNT).history_id == "888"


def test_locked_keyring_blocks_sign_in_with_retry_message(conn, cfg_path):
    from xx_email import setup
    from xx_email.token_store import MemoryTokenBackend, TokenStore
    backend = MemoryTokenBackend()
    backend.lock()
    store = TokenStore(backend)
    assert setup.describe_setup_state([], True) == "locked"
    assert setup.KEYRING_LOCKED_MESSAGE == \
        "Unlock your keyring to continue"
    with pytest.raises(setup.KeyringLocked,
                       match="Unlock your keyring to continue"):
        setup.finish_sign_in(
            CLIENT_ID, "c", "http://127.0.0.1:9/x", "v",
            token_transport=token_transport(), store=store,
            conn=conn, config_path=cfg_path)
    backend.unlock()
    assert setup.describe_setup_state([], False) == "setup"
    assert setup.describe_setup_state([ACCOUNT], False) == "ready"
    assert setup.describe_setup_state([ACCOUNT], True) == "locked"


def test_revoke_posts_token_and_reports_status():
    from xx_email import setup
    seen = {}

    def ok(url, body, headers):
        seen["url"] = url
        seen["params"] = urllib.parse.parse_qs(body.decode("ascii"))
        return 200

    assert setup.revoke_grant("rt-1", opener=ok) is True
    assert seen["url"] == setup.REVOKE_ENDPOINT
    assert seen["params"] == {"token": ["rt-1"]}

    assert setup.revoke_grant("rt-1", opener=lambda u, b, h: 400) \
        is False
    assert setup.revoke_grant("", opener=ok) is False

    def down(url, body, headers):
        raise OSError("offline")

    assert setup.revoke_grant("rt-1", opener=down) is False


def test_sign_out_revokes_removes_rows_and_payloads(tmp_path, conn):
    from xx_email import setup
    from xx_email.token_store import (
        MemoryTokenBackend, TokenRecord, TokenStore)
    store = TokenStore(MemoryTokenBackend())
    store.save(ACCOUNT, TokenRecord(refresh_token="rt-1",
                                    client_id=CLIENT_ID))
    db.upsert_account(conn, db.Account(email=ACCOUNT,
                                       display_name=ACCOUNT))
    db.upsert_threads(conn, [db.Thread(
        account_email=ACCOUNT, id="t1", labels_csv="INBOX")])
    db.upsert_messages(conn, [db.Message(
        account_email=ACCOUNT, id="m1", thread_id="t1",
        subject="s", from_address="a@x", to_csv="b@x",
        snippet="sn", labels_csv="INBOX")])
    data_dir = tmp_path / "data"
    payload = b"From: a@x\r\n\r\nhello"
    rel = OutboxFiles.write_new(data_dir, 7, payload)
    db.insert_outbox(conn, db.OutboxEntry(
        account_email=ACCOUNT, kind="SEND", subject="hi",
        path=rel, size=len(payload), target_at=1, state="QUEUED",
        created_at=1))
    assert OutboxFiles.resolve(data_dir, rel) is not None

    revoked = []

    def fake_revoke(client_id, refresh_token):
        revoked.append((client_id, refresh_token))
        return True

    outcome = setup.sign_out(conn, store, ACCOUNT, data_dir=data_dir,
                             revoke=fake_revoke)
    assert outcome["revoked"] is True
    assert outcome["warnings"] == []
    assert revoked == [(CLIENT_ID, "rt-1")]
    assert store.load(ACCOUNT) is None
    assert db.get_account(conn, ACCOUNT) is None
    assert db.threads_with_label(conn, ACCOUNT, "INBOX") == []
    assert db.search_local(conn, ACCOUNT, "hello") == []
    assert OutboxFiles.resolve(data_dir, rel) is None


def test_sign_out_without_revoke_keeps_server_grant(tmp_path, conn):
    from xx_email import setup
    from xx_email.token_store import (
        MemoryTokenBackend, TokenRecord, TokenStore)
    store = TokenStore(MemoryTokenBackend())
    store.save(ACCOUNT, TokenRecord(refresh_token="rt-1",
                                    client_id=CLIENT_ID))
    db.upsert_account(conn, db.Account(email=ACCOUNT,
                                       display_name=ACCOUNT))
    outcome = setup.sign_out(conn, store, ACCOUNT,
                             data_dir=tmp_path / "data",
                             revoke=None)
    assert outcome["revoked"] is False
    assert store.load(ACCOUNT) is None
    assert db.get_account(conn, ACCOUNT) is None


def test_sign_out_reports_revoke_failure_but_still_removes(conn):
    from xx_email import setup
    from xx_email.token_store import (
        MemoryTokenBackend, TokenRecord, TokenStore)
    store = TokenStore(MemoryTokenBackend())
    store.save(ACCOUNT, TokenRecord(refresh_token="rt-1",
                                    client_id=CLIENT_ID))
    db.upsert_account(conn, db.Account(email=ACCOUNT,
                                       display_name=ACCOUNT))

    def down(client_id, refresh_token):
        raise OSError("offline")

    outcome = setup.sign_out(conn, store, ACCOUNT,
                             data_dir="/nonexistent-data-dir-for-tests",
                             revoke=down)
    assert outcome["revoked"] is False
    assert len(outcome["warnings"]) == 1
    assert store.load(ACCOUNT) is None
    assert db.get_account(conn, ACCOUNT) is None


def test_first_run_directories_are_private(tmp_path, monkeypatch):
    from xx_email import setup
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    dirs = setup.ensure_app_dirs()
    assert set(dirs) == {"config", "data", "cache", "state"}
    for path in dirs.values():
        assert path.is_dir()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
    assert (dirs["data"] / "outbox").is_dir()
    assert stat.S_IMODE(os.stat(dirs["cache"] / "attachments").st_mode) \
        == 0o700
    loose = tmp_path / "cfg" / "xx-email"
    os.chmod(loose, 0o755)
    setup.ensure_app_dirs()
    assert stat.S_IMODE(os.stat(loose).st_mode) == 0o700


def test_setup_module_stays_headless():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "src" / "xx_email"
            / "setup.py").read_text(encoding="utf-8")
    assert "import gi" not in text
    assert "from gi" not in text


def test_listener_stops_after_first_valid_callback():
    flow = LoopbackFlow(CLIENT_ID)
    try:
        redirect = flow.redirect_uri + \
            f"?code=auth-code&state={flow.state}"
        with urllib.request.urlopen(redirect, timeout=10) as response:
            assert response.status == 200
        assert flow.wait_for_code(timeout=10) == "auth-code"
        with pytest.raises(Exception):
            urllib.request.urlopen(flow.redirect_uri + "?code=again"
                                   f"&state={flow.state}", timeout=5)
    finally:
        flow.stop()


def test_listener_stops_after_timeout():
    flow = LoopbackFlow(CLIENT_ID)
    try:
        with pytest.raises(AuthFlowError, match="Timed out"):
            flow.wait_for_code(timeout=0.2)
        with pytest.raises(Exception):
            urllib.request.urlopen(flow.redirect_uri + "?code=late"
                                   f"&state={flow.state}", timeout=5)
    finally:
        flow.stop()


def test_state_check_uses_constant_time_comparison():
    import hmac
    flow = LoopbackFlow(CLIENT_ID)
    try:
        assert flow._state_ok(flow.state) is True
        assert flow._state_ok(flow.state + "x") is False
        assert hmac.compare_digest.__name__ == "compare_digest"
    finally:
        flow.stop()
