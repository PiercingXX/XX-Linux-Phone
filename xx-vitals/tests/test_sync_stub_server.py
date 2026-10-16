"""Sync over real HTTP on 127.0.0.1: ports of SyncEnginePushTest,
SyncEnginePullTest, SyncEndToEndTest, HttpSyncApiTest and CleartextPolicyTest.

The REAL HttpSyncApi drives the REAL SyncEngine over actual sockets against
a stub NAS (stdlib http.server on an ephemeral loopback port). Bearer [REDACTED]
ride every /v1 request; the stub answers the exact NAS route shapes. No test
touches a real keyring or session bus: the token rides in a lambda and
secrets stay in memory.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent-test-bus")

from xx_vitals import prefs as prefs_module
from xx_vitals.store import entries
from xx_vitals.store.db import Database
from xx_vitals.sync import auth as auth_module
from xx_vitals.sync import http as http_guard
from xx_vitals.sync.api import Ok, Transient, from_iso
from xx_vitals.sync.engine import (
    BACKOFF_MILLIS,
    PAGE_SIZE,
    SyncAuthException,
    SyncEngine,
    SyncPermanentException,
    SyncRetryableException,
)
from xx_vitals.sync.http_api import HttpSyncApi


def _b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


MINTED_TOKEN = (
    "v1." + _b64("kid") + "."
    + _b64(json.dumps({"user_id": "operator-7", "iat": 1, "exp": 1900000000}))
    + "." + _b64("sig")
)
RECEIVED_AT_ISO = "2026-08-01T09:00:00+00:00"
RECEIVED_AT_MS = from_iso(RECEIVED_AT_ISO)


class StubNAS:
    """Scriptable stub for xx-chat login + the vitals NAS routes."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.login_token = MINTED_TOKEN
        self.login_status = 200
        self.weight_rows: list[dict] = []
        self.changes_body: dict = {"daily": [], "activities": [], "weight": []}
        self.batches_body: dict = {"batches": []}
        self.post_status: list[tuple[int, str]] = []
        self.patch_status: list[tuple[int, str]] = []
        self.delete_status: list[tuple[int, str]] = []
        self.changes_status = 200
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self.server is not None
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    @property
    def host_port(self) -> str:
        assert self.server is not None
        host, port = self.server.server_address
        return f"{host}:{port}"

    def start(self) -> "StubNAS":
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass

            def _read_body(self) -> bytes:
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _send(self, code: int, body: str) -> None:
                raw = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _record(self, body: bytes) -> None:
                stub.requests.append({
                    "method": self.command,
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "body": body,
                })

            def _pop(self, queue: list) -> tuple[int, str]:
                if queue:
                    return queue.pop(0)
                return 200, "{}"

            def do_POST(self) -> None:  # noqa: N802
                body = self._read_body()
                self._record(body)
                parsed = urlparse(self.path)
                if parsed.path == "/api/v1/fabric/login":
                    if stub.login_status in (401, 403):
                        self._send(stub.login_status, '{"detail":"bad"}')
                    else:
                        self._send(200, json.dumps({"token": stub.login_token}))
                elif parsed.path == "/v1/weight":
                    code, text = self._pop(stub.post_status)
                    self._send(code, text)
                else:
                    self._send(404, "{}")

            def do_PATCH(self) -> None:  # noqa: N802
                body = self._read_body()
                self._record(body)
                code, text = self._pop(stub.patch_status)
                self._send(code, text or '{"updated":true}')

            def do_DELETE(self) -> None:  # noqa: N802
                self._record(b"")
                code, text = self._pop(stub.delete_status)
                self._send(code, text or '{"deleted":true}')

            def do_GET(self) -> None:  # noqa: N802
                self._record(b"")
                parsed = urlparse(self.path)
                if parsed.path == "/v1/weight":
                    self._send(200, json.dumps({"rows": stub.weight_rows}))
                elif parsed.path == "/v1/changes":
                    if stub.changes_status != 200:
                        self._send(stub.changes_status, "{}")
                    elif isinstance(stub.changes_body, str):
                        self._send(200, stub.changes_body)
                    else:
                        self._send(200, json.dumps(stub.changes_body))
                elif parsed.path == "/v1/batches":
                    self._send(200, json.dumps(stub.batches_body))
                elif parsed.path == "/v1/daily":
                    self._send(200, json.dumps({"rows": []}))
                elif parsed.path == "/v1/activities":
                    self._send(200, json.dumps({"rows": []}))
                else:
                    self._send(404, "{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=10)

    def paths(self, method: str | None = None) -> list[str]:
        return [r["path"] for r in self.requests
                if method is None or r["method"] == method]


@pytest.fixture()
def stub():
    nas = StubNAS().start()
    yield nas
    nas.stop()


def _api(stub: StubNAS, token: str | None = MINTED_TOKEN, allowed=None,
         auth_calls: list | None = None) -> HttpSyncApi:
    return HttpSyncApi(
        base_url=stub.base_url,
        token_provider=lambda: token,
        allowed_cleartext_hosts=lambda: (
            {stub.host_port} if allowed is None else set(allowed)),
        on_auth_expired=(lambda: auth_calls.append(1))
        if auth_calls is not None else None,
    )


def _engine(db: Database, api: HttpSyncApi) -> SyncEngine:
    return SyncEngine(db, db, db, db, db, api, now=lambda: 42,
                      delay=lambda ms: None)


def _allow_stub(monkeypatch, stub: StubNAS) -> None:
    cfg = prefs_module.AppConfig(
        allowed_cleartext_hosts=[stub.host_port, stub.base_url])
    monkeypatch.setattr(prefs_module, "load", lambda path=None: cfg)


def test_login_then_push_then_pull_end_to_end(stub, tmp_path, monkeypatch):
    _allow_stub(monkeypatch, stub)
    result = auth_module.fabric_login(
        stub.base_url, "piercing", "secret", timeout=10.0)
    assert isinstance(result, auth_module.LoginSuccess)
    assert result.token == MINTED_TOKEN
    assert auth_module.token_user_id(result.token) == "operator-7"
    login = stub.requests[0]
    assert login["method"] == "POST"
    assert login["path"] == "/api/v1/fabric/login"
    assert json.loads(login["body"].decode("utf-8")) == {
        "username": "piercing", "password": "secret"}

    stub.weight_rows = [{
        "id": 56, "source_id": 3, "measured_at": RECEIVED_AT_ISO,
        "weight_kg": 80.0,
    }]
    stub.changes_body = {
        "daily": [], "activities": [],
        "weight": [{"id": 99, "source_id": 4,
                    "measured_at": "2026-07-30T06:30:00+00:00",
                    "weight_kg": 79.5, "batch_id": 77}],
    }
    stub.batches_body = {"batches": [
        {"id": 56, "kind": "manual", "started_at": RECEIVED_AT_ISO,
         "finished_at": None, "rows_read": 1, "rows_inserted": 1,
         "rows_skipped": 0, "status": "ok", "error": None},
        {"id": 77, "kind": "takeout",
         "started_at": "2026-07-30T06:31:00+00:00",
         "finished_at": None, "rows_read": 5, "rows_inserted": 5,
         "rows_skipped": 0, "status": "ok", "error": None},
    ]}

    db = Database(tmp_path / "vitals.db")
    db.init()
    repo = entries.WeightRepository(db)
    repo.add_weight(
        entries.WeightSample(
            source_id="manual", measured_at=RECEIVED_AT_MS, weight_kg=80.0),
        entries.weight_payload("manual", RECEIVED_AT_MS, 80.0))
    db.pending_insert(entries.PendingWrite(
        kind=entries.KIND_WEIGHT_UPDATE,
        payload_json=entries.weight_payload("manual", RECEIVED_AT_MS, 81.25),
        created_at=2))
    db.pending_insert(entries.PendingWrite(
        kind=entries.KIND_WEIGHT_DELETE,
        payload_json=entries.weight_payload("manual", 1754000000000, 78.0),
        created_at=3))
    assert db.pending_count() == 3

    auth_calls: list = []
    eng = _engine(db, _api(stub, auth_calls=auth_calls))
    assert eng.push() is False
    assert db.pending_count() == 0

    post = next(r for r in stub.requests
                if r["method"] == "POST" and r["path"] == "/v1/weight")
    assert post["auth"] == f"Bearer {MINTED_TOKEN}"
    assert '"weight_kg": 80.0' in post["body"].decode("utf-8")
    lookup = next(r for r in stub.requests
                  if r["method"] == "GET" and r["path"].startswith("/v1/weight"))
    assert lookup["auth"] == f"Bearer {MINTED_TOKEN}"
    assert "from=2026-07-31" in lookup["path"] and "to=2026-08-02" in lookup["path"]
    patch = next(r for r in stub.requests if r["method"] == "PATCH")
    assert patch["path"] == "/v1/weight/56"
    assert '"weight_kg": 81.25' in patch["body"].decode("utf-8")
    assert [r for r in stub.requests if r["method"] == "DELETE"] == []

    eng.pull()
    changes = next(r for r in stub.requests
                   if r["path"].startswith("/v1/changes"))
    assert changes["auth"] == f"Bearer {MINTED_TOKEN}"
    assert "since=1970-01-01T00%3A00%3A00%2B00%3A00" in changes["path"]
    pulled = db.find_weight_by_natural_key(
        "manual", from_iso("2026-07-30T06:30:00+00:00"))
    assert pulled is not None and pulled.weight_kg == 79.5
    state = db.sync_get()
    assert state is not None
    assert state.last_pull_iso == "2026-07-30T06:31:00+00:00"

    eng.pull()
    seconds = [r for r in stub.requests if r["path"].startswith("/v1/changes")]
    assert "since=2026-07-30T06%3A31%3A00%2B00%3A00" in seconds[-1]["path"]
    assert auth_calls == []


def test_wrong_password_surfaces_relogin_never_touches_nas(
        stub, monkeypatch):
    _allow_stub(monkeypatch, stub)
    stub.login_status = 401
    result = auth_module.fabric_login(
        stub.base_url, "piercing", "wrong", timeout=10.0)
    assert isinstance(result, auth_module.LoginFailure)
    assert result.code == 401
    assert result.message == "Wrong username or password"
    assert stub.paths() == ["/api/v1/fabric/login"]
    assert all(r["method"] == "POST" for r in stub.requests)


def _seeded_db(tmp_path, count: int = 2):
    db = Database(tmp_path / "vitals.db")
    db.init()
    for i in range(count):
        at = 1_000 * (i + 1)
        db.pending_insert(entries.PendingWrite(
            kind=entries.KIND_WEIGHT,
            payload_json=entries.weight_payload("manual", at, 70.0 + i),
            created_at=at))
    return db


def test_push_401_aborts_and_flags_relogin(stub, tmp_path):
    db = _seeded_db(tmp_path)
    stub.post_status.append((401, '{"detail":"bad token"}'))
    auth_calls: list = []
    eng = _engine(db, _api(stub, auth_calls=auth_calls))
    with pytest.raises(SyncAuthException):
        eng.push()
    assert auth_calls == [1]
    assert db.pending_count() == 2
    assert stub.paths("POST") == ["/v1/weight"]


def test_push_507_pauses_with_queue_kept(stub, tmp_path):
    db = _seeded_db(tmp_path, 3)
    stub.post_status.extend([(200, '{"received_at":"x"}'), (507, "")])
    eng = _engine(db, _api(stub))
    assert eng.push() is True
    assert db.pending_count() == 2
    assert stub.paths("POST") == ["/v1/weight", "/v1/weight"]


def test_push_422_dead_letters_rest_drains(stub, tmp_path):
    db = _seeded_db(tmp_path, 2)
    stub.post_status.extend([(422, '{"detail":"bad value"}'), (200, "{}")])
    eng = _engine(db, _api(stub))
    assert eng.push() is False
    assert db.pending_count() == 1
    row = db.pending_next(10, 2**31)[0]
    assert "HTTP 422" in (row.last_error or "")
    before = len(stub.paths("POST"))
    assert _engine(db, _api(stub)).push() is False
    assert len(stub.paths("POST")) == before


def test_pull_errors_map_over_http(stub, tmp_path):
    db = Database(tmp_path / "vitals.db")
    db.init()
    stub.changes_status = 500
    with pytest.raises(SyncRetryableException):
        _engine(db, _api(stub)).pull()
    stub.changes_status = 400
    with pytest.raises(SyncPermanentException):
        _engine(db, _api(stub)).pull()
    stub.changes_status = 401
    with pytest.raises(SyncAuthException):
        _engine(db, _api(stub)).pull()


def test_pull_malformed_delta_is_transient(stub, tmp_path):
    db = Database(tmp_path / "vitals.db")
    db.init()
    stub.changes_body = "<html>not json</html>"
    with pytest.raises(SyncRetryableException):
        _engine(db, _api(stub)).pull()


def test_pull_cursor_keeps_server_microseconds(stub, tmp_path):
    iso = "2026-08-01T08:00:00.123456+00:00"
    stub.changes_body = {"daily": [], "activities": [], "weight": []}
    stub.batches_body = {"batches": []}
    db = Database(tmp_path / "vitals.db")
    db.init()
    eng = _engine(db, _api(stub))
    eng.pull()
    assert db.sync_get() is None
    stub.changes_body = {"daily": [], "activities": [],
                         "weight": [{"id": 1, "source_id": 4,
                                     "measured_at": "2026-08-01T08:00:00+00:00",
                                     "weight_kg": 70.0, "batch_id": 4}]}
    stub.batches_body = {"batches": [{"id": 4, "started_at": iso}]}
    eng.pull()
    assert db.sync_get().last_pull_iso == iso
    eng.pull()
    last = [r for r in stub.requests if r["path"].startswith("/v1/changes")][-1]
    assert "since=2026-08-01T08%3A00%3A00.123456%2B00%3A00" in last["path"]


def test_find_weight_id_ambiguous_rows_are_no_match(stub):
    stub.weight_rows = [
        {"id": 56, "measured_at": "2026-08-01T08:00:00+00:00"},
        {"id": 57, "measured_at": "2026-08-01T08:00:00+00:00"},
    ]
    api = _api(stub)
    found = api.find_weight_id(from_iso("2026-08-01T08:00:00+00:00"))
    assert isinstance(found, Ok) and found.value is None


def test_missing_bearer_still_sends_request_without_header(stub):
    api = _api(stub, token=None)
    assert isinstance(api.fetch_batch_stamps(), Ok)
    assert stub.requests[-1]["auth"] is None


def test_cleartext_to_unconfigured_host_never_reaches_wire(stub):
    before = len(stub.requests)
    api = _api(stub, allowed={"http://some-other-nas.lan:9999"})
    result = api.fetch_daily("2026-08-01", "2026-08-02")
    assert isinstance(result, Transient)
    assert "cleartext" in (result.reason or "")
    assert len(stub.requests) == before


def test_cleartext_policy_configured_host_with_port_allowed():
    assert http_guard.is_cleartext_allowed(
        "192.168.1.50", 8080, {"http://192.168.1.50:8080"})


def test_cleartext_policy_bare_hostname_allows_any_port():
    assert http_guard.is_cleartext_allowed("nas.local", 9999, {"nas.local"})


def test_cleartext_policy_different_port_refused():
    assert not http_guard.is_cleartext_allowed(
        "192.168.1.50", 9090, {"http://192.168.1.50:8080"})


def test_cleartext_policy_unconfigured_lan_host_refused():
    assert not http_guard.is_cleartext_allowed(
        "evil-device.lan", 80, {"nas.local:8080"})


def test_cleartext_policy_empty_configuration_refuses():
    assert not http_guard.is_cleartext_allowed("nas.local", 8080, set())


def test_cleartext_policy_host_matching_case_insensitive():
    assert http_guard.is_cleartext_allowed("NAS.LOCAL", 8080, {"nas.local"})


def test_cleartext_policy_loopback_needs_configuration():
    assert http_guard.is_cleartext_allowed(
        "127.0.0.1", 8080, {"127.0.0.1:8080"})
    assert not http_guard.is_cleartext_allowed("127.0.0.1", 8080, set())
    assert not http_guard.is_cleartext_allowed(
        "127.0.0.1", 9090, {"127.0.0.1:8080"})


def test_engine_constants_match_android_reference():
    from pathlib import Path

    candidates = [
        Path(__file__).parent.parent / "ref" / "xx-vitals",
        # The real checkout keeps Android sources in ../../android/.
        Path(__file__).parent.parent.parent.parent / "android" / "xx-vitals",
    ]
    sub = "app/src/main/kotlin/com/piercingxx/vitals/sync/SyncEngine.kt"
    root = next((p for p in candidates if (p / sub).is_file()), None)
    if root is None:
        pytest.skip("no Android reference tree available")
    text = (root / "app/src/main/kotlin/com/piercingxx/vitals/sync/SyncEngine.kt"
            ).read_text(encoding="utf-8")
    assert "PAGE_SIZE = 50" in text
    assert PAGE_SIZE == 50
    assert "1000L, 2000L, 4000L, 8000L, 16000L, 32000L, 60000L" in text
    assert BACKOFF_MILLIS == [1000, 2000, 4000, 8000, 16000, 32000, 60000]
    assert "DEAD_LETTER_ATTEMPTS" in text
    assert "stale edit" in text.lower()
