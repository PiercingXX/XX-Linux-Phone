"""Milestone 5 stub-server acceptance: real HTTP against a local stub.

Replays Gmail-shaped JSON over http.server on 127.0.0.1 (ephemeral
port) through the real urllib transport: first sync hydrates, the
second sync is delta-only (asserted on the request log), a 401
mid-sync refreshes once, a 404 on history rebuilds, 429 with
Retry-After is honored with an injected sleep, and a killed full sync
resumes from its DB checkpoint. Loopback only: no real network, and
tokens come from plain dicts/callables, never a real keyring.
"""

import json
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from xx_email import db
from xx_email.auth import ReauthNeeded
from xx_email.gmail_client import (
    GmailClient,
    GmailTransportError,
    parse_retry_after,
    urllib_transport,
)
from xx_email.sync_service import FULL_SYNC_PROGRESS_KEY, SyncEngine

ACCOUNT = "user@dev.xxemail"


class StubGmail:
    """Gmail-shaped stub on 127.0.0.1 with an ephemeral port."""

    def __init__(self):
        self.log = []
        self.route = None
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                stub.serve(self)

            def do_POST(self):  # noqa: N802
                stub.serve(self)

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )

    @property
    def base_url(self):
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/"

    def start(self):
        self._thread.start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5.0)

    def serve(self, handler):
        parsed = urllib.parse.urlparse(handler.path)
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length) if length else b""
        query = urllib.parse.parse_qs(parsed.query)
        self.log.append({
            "method": handler.command,
            "path": parsed.path,
            "query": query,
            "target": handler.path,
            "auth": handler.headers.get("Authorization"),
            "body": body,
        })
        outcome = self.route(handler.command, parsed.path, query,
                             dict(handler.headers), body)
        if outcome == "drop":
            try:
                handler.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            handler.connection.close()
            return
        status, extra_headers, payload = outcome
        raw = payload if isinstance(payload, bytes) else \
            json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        for key, value in (extra_headers or {}).items():
            handler.send_header(key, value)
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)


@pytest.fixture
def stub():
    server = StubGmail()
    server.start()
    yield server
    server.stop()


def profile(history="100"):
    return {"emailAddress": ACCOUNT, "historyId": history,
            "messagesTotal": 3, "threadsTotal": 2}


def labels():
    return {"labels": [{"id": "INBOX", "name": "INBOX",
                        "type": "system"}]}


def thread_list(*tids, token=None):
    payload = {"threads": [{"id": tid, "snippet": "s",
                             "historyId": "1"} for tid in tids],
               "resultSizeEstimate": len(tids)}
    if token is not None:
        payload["nextPageToken"] = token
    return payload


def headers(subject="Hello"):
    return [{"name": "Subject", "value": subject},
            {"name": "From",
             "value": "Jane Doe <jane@example.com>"}]


def thread_payload(tid, messages, snippet="snip"):
    return {
        "id": tid, "snippet": snippet, "historyId": "5",
        "messages": [
            {"id": mid, "threadId": tid, "labelIds": list(lbl),
             "snippet": "s", "historyId": "5",
             "internalDate": str(1000 + index),
             "payload": {"mimeType": "multipart/alternative",
                         "headers": headers("Sub " + mid)}}
            for index, (mid, lbl) in enumerate(messages)],
    }


def history(items, history_id="120", token=None):
    payload = {"history": items, "historyId": history_id}
    if token is not None:
        payload["nextPageToken"] = token
    return payload


def added(item_id, tid, mid=None):
    return {"id": item_id,
            "messagesAdded": [{"message": {
                "id": mid or ("m-" + tid), "threadId": tid}}]}


def ok(payload, headers=None):
    return (200, headers or {}, payload)


def engine_for(conn, stub, tokens=None, refresh=None, **kwargs):
    kwargs.setdefault("clock", lambda: 1700000000000)
    kwargs.setdefault("retry_sleep", lambda seconds: None)
    kwargs.setdefault("retry_jitter", lambda: 0.0)
    return SyncEngine(conn, urllib_transport,
                      tokens={ACCOUNT: "tok"} if tokens is None
                      else tokens,
                      refresh=refresh, base_url=stub.base_url, **kwargs)


def open_file_db(path):
    return db.open_db(str(path))


def test_stub_first_sync_hydrates(stub, tmp_path):
    conn = open_file_db(tmp_path / "mail.db")
    try:
        stub.route = lambda method, path, query, headers, body: \
            _first_sync_route(method, path, query)
        engine = engine_for(conn, stub)
        try:
            outcome = engine.sync_account(ACCOUNT)
        finally:
            engine.close()
        assert outcome.ok is True, outcome.error
        assert outcome.full is True
        assert outcome.new_ids == [], "first sync must never notify"
        assert outcome.checkpoint == "100"
        assert db.get_account(conn, ACCOUNT).history_id == "100"
        assert {row.id for row in db.all_mail(conn, ACCOUNT)} == \
            {"t1", "t2"}
        assert db.get_folder_page(conn, ACCOUNT,
                                  FULL_SYNC_PROGRESS_KEY) is None
    finally:
        conn.close()
    targets = [entry["target"] for entry in stub.log]
    assert any("users/me/profile" in target for target in targets)
    assert any("me/threads?" in target for target in targets)
    gets = [entry for entry in stub.log if "/threads/t" in entry["path"]]
    assert gets and all(entry["query"].get("format") == ["metadata"]
                        for entry in gets)
    assert all(entry["auth"] == "Bearer tok" for entry in stub.log)


def _first_sync_route(method, path, query):
    if path.endswith("/profile"):
        return ok(profile("100"))
    if path.endswith("/labels"):
        return ok(labels())
    if path.endswith("/threads"):
        return ok(thread_list("t1", "t2"))
    if "/threads/" in path:
        tid = path.rsplit("/threads/", 1)[1]
        return ok(thread_payload(
            tid, [(f"m-{tid}", ["INBOX", "CATEGORY_PERSONAL"])]))
    raise AssertionError("unexpected call: " + path)


def test_stub_second_sync_is_delta_only(stub, tmp_path):
    conn = open_file_db(tmp_path / "mail.db")
    try:
        stub.route = lambda method, path, query, headers, body: \
            _first_sync_route(method, path, query)
        first = engine_for(conn, stub)
        try:
            assert first.sync_account(ACCOUNT).ok is True
        finally:
            first.close()
        mark = len(stub.log)

        def phase_two(method, path, query, headers, body):
            if path.endswith("/profile"):
                return ok(profile("120"))
            if path.endswith("/labels"):
                return ok(labels())
            if path.endswith("/history"):
                assert query.get("startHistoryId") == ["100"]
                return ok(history([added("h1", "t3")],
                                  history_id="120"))
            if path.endswith("/threads/t3"):
                return ok(thread_payload(
                    "t3", [("m3", ["INBOX", "UNREAD",
                                   "CATEGORY_PERSONAL"])]))
            raise AssertionError("unexpected call: " + path)

        stub.route = phase_two
        second = engine_for(conn, stub)
        try:
            outcome = second.sync_account(ACCOUNT)
        finally:
            second.close()
    finally:
        conn.close()
    assert outcome.ok is True, outcome.error
    assert outcome.full is False
    assert outcome.new_ids == ["t3"]
    assert outcome.checkpoint == "h1"
    rerun = [entry["target"] for entry in stub.log[mark:]]
    assert any("users/me/history" in target for target in rerun)
    assert not any("me/threads?" in target for target in rerun), \
        "delta must not re-list threads"


def test_stub_401_mid_sync_refreshes_once(stub, tmp_path):
    conn = open_file_db(tmp_path / "mail.db")
    refreshed = []
    seen_401 = []

    def route(method, path, query, headers, body):
        if path.endswith("/profile"):
            return ok(profile("100"))
        if path.endswith("/labels"):
            return ok(labels())
        if path.endswith("/threads"):
            return ok(thread_list("t1"))
        if path.endswith("/threads/t1"):
            if not seen_401:
                seen_401.append(True)
                return (401, {}, {"error": {"code": 401}})
            return ok(thread_payload("t1", [("m1", ["INBOX"])]))
        raise AssertionError("unexpected call: " + path)

    def refresh(email, rejected):
        refreshed.append((email, rejected))
        return "tok-new"

    try:
        stub.route = route
        engine = engine_for(conn, stub, tokens={ACCOUNT: "tok-old"},
                            refresh=refresh)
        try:
            outcome = engine.sync_account(ACCOUNT)
        finally:
            engine.close()
    finally:
        conn.close()
    assert outcome.ok is True, outcome.error
    assert refreshed == [(ACCOUNT, "tok-old")]
    bearers = [entry["auth"] for entry in stub.log]
    assert "Bearer tok-old" in bearers
    assert "Bearer tok-new" in bearers
    assert len([entry for entry in stub.log
                if entry["path"].endswith("/threads/t1")]) == 2


def test_stub_second_401_surfaces_reauth(stub, tmp_path):
    conn = open_file_db(tmp_path / "mail.db")
    try:
        stub.route = lambda *args: (401, {}, {"error": {"code": 401}})
        engine = engine_for(conn, stub, tokens={ACCOUNT: "tok-old"},
                            refresh=lambda email, rejected: "tok-new")
        try:
            outcome = engine.sync_account(ACCOUNT)
        finally:
            engine.close()
    finally:
        conn.close()
    assert outcome.ok is False
    assert outcome.reauth is True
    assert outcome.error
    assert len(stub.log) == 2, "a second 401 must not loop"


def test_stub_404_history_rebuilds(stub, tmp_path):
    conn = open_file_db(tmp_path / "mail.db")
    db.upsert_account(conn, db.Account(
        email=ACCOUNT, display_name=ACCOUNT, history_id="old"))
    try:
        def route(method, path, query, headers, body):
            if path.endswith("/profile"):
                return ok(profile("200"))
            if path.endswith("/labels"):
                return ok(labels())
            if path.endswith("/history"):
                return (404, {}, {"error": {"code": 404}})
            if path.endswith("/threads"):
                return ok(thread_list("t9"))
            if path.endswith("/threads/t9"):
                return ok(thread_payload("t9", [("m9", ["INBOX"])]))
            raise AssertionError("unexpected call: " + path)

        stub.route = route
        engine = engine_for(conn, stub)
        try:
            outcome = engine.sync_account(ACCOUNT)
        finally:
            engine.close()
        assert outcome.ok is True, outcome.error
        assert outcome.checkpoint == "200"
        assert outcome.follow_up is False
        assert db.get_thread(conn, ACCOUNT, "t9") is not None
    finally:
        conn.close()


def test_stub_429_retry_after_is_honored(stub):
    calls = []
    sleeps = []

    def route(method, path, query, headers, body):
        calls.append(path)
        if len(calls) == 1:
            return (429, {"Retry-After": "3"},
                    {"error": {"code": 429}})
        return ok(profile("100"))

    stub.route = route
    client = GmailClient(transport=urllib_transport,
                         get_token=lambda: "tok",
                         base_url=stub.base_url,
                         sleep=sleeps.append,
                         jitter=lambda: 0.0)
    assert client.get_profile().history_id == "100"
    assert len(calls) == 2
    assert sleeps == [3.0]


def test_stub_500_backs_off_then_recovers(stub):
    calls = []
    sleeps = []

    def route(method, path, query, headers, body):
        calls.append(path)
        if len(calls) == 1:
            return (500, {"Retry-After": "soon"},
                    {"error": {"code": 500}})
        return ok(profile("100"))

    stub.route = route
    client = GmailClient(transport=urllib_transport,
                         get_token=lambda: "tok",
                         base_url=stub.base_url,
                         sleep=sleeps.append,
                         jitter=lambda: 0.5)
    assert client.get_profile().history_id == "100"
    assert len(calls) == 2
    assert sleeps == [1.5], \
        "unparsable Retry-After falls back to exponential backoff"


def test_stub_persistent_503_exhausts_budget(stub):
    sleeps = []
    stub.route = lambda *args: (503, {"Retry-After": "1"},
                               {"error": {"code": 503}})
    client = GmailClient(transport=urllib_transport,
                         get_token=lambda: "tok",
                         base_url=stub.base_url,
                         max_retries=2,
                         sleep=sleeps.append,
                         jitter=lambda: 0.0)
    try:
        client.get_profile()
    except GmailTransportError as caught:
        assert caught.status == 503
    else:
        raise AssertionError("exhausted retries must raise")
    assert len(stub.log) == 3, "1 initial attempt plus 2 retries"
    assert sleeps == [1.0, 1.0]


def test_retry_after_parsing():
    assert parse_retry_after({"Retry-After": "7"}) == 7.0
    assert parse_retry_after({"retry-after": "2"}) == 2.0
    assert parse_retry_after({"RETRY-AFTER": "0"}) == 0.0
    assert parse_retry_after({"Retry-After": "-5"}) == 0.0
    assert parse_retry_after({"Retry-After": "soon"}) is None
    assert parse_retry_after({}) is None


def test_stub_message_formats(stub):
    seen = []

    def route(method, path, query, req_headers, body):
        seen.append((path, query))
        if path.endswith("/messages/m1"):
            fmt = query.get("format", [""])[0]
            if fmt == "raw":
                return ok({"id": "m1", "threadId": "t1",
                           "raw": "RnJvbTogbWVAZXhhbXBsZS5jb20="})
            return ok({"id": "m1", "threadId": "t1",
                       "labelIds": ["INBOX"],
                       "payload": {"mimeType": "text/plain",
                                   "headers": headers("Hi")}})
        raise AssertionError("unexpected call: " + path)

    stub.route = route
    client = GmailClient(transport=urllib_transport,
                         get_token=lambda: "tok",
                         base_url=stub.base_url,
                         sleep=lambda seconds: None,
                         jitter=lambda: 0.0)
    full = client.get_message("m1", fmt="full")
    assert full.thread_id == "t1"
    raw = client.get_message("m1", fmt="raw")
    assert raw.id == "m1"
    formats = [query.get("format", [None])[0] for _, query in seen]
    assert formats == ["full", "raw"]


def test_stub_killed_full_sync_resumes(stub, tmp_path):
    db_path = str(tmp_path / "mail.db")
    state = {"kill_lists": True}

    def payload_for(tid):
        return thread_payload(tid, [(f"m-{tid}", ["INBOX"])])

    def route(method, path, query, headers, body):
        if path.endswith("/profile"):
            return ok(profile("300"))
        if path.endswith("/labels"):
            return ok(labels())
        if path.endswith("/threads"):
            if query.get("pageToken") == ["tok2"]:
                if state["kill_lists"]:
                    return "drop"
                return ok(thread_list("t3"))
            return ok(thread_list("t1", "t2", token="tok2"))
        if "/threads/" in path:
            return ok(payload_for(path.rsplit("/threads/", 1)[1]))
        raise AssertionError("unexpected call: " + path)

    stub.route = route
    conn = open_file_db(db_path)
    try:
        engine = engine_for(conn, stub)
        try:
            outcome = engine.sync_account(ACCOUNT)
        finally:
            engine.close()
    finally:
        conn.close()
    assert outcome.ok is False, "a dropped connection must fail the pass"
    assert outcome.offline is True
    assert outcome.error
    check = open_file_db(db_path)
    try:
        assert db.get_account(check, ACCOUNT).history_id is None, \
            "the checkpoint must not advance past unlanded work"
        assert db.get_thread(check, ACCOUNT, "t1") is not None
        assert db.get_thread(check, ACCOUNT, "t2") is not None
        assert db.get_thread(check, ACCOUNT, "t3") is None
        progress = db.get_folder_page(check, ACCOUNT,
                                      FULL_SYNC_PROGRESS_KEY)
        assert progress is not None and \
            progress.next_page_token == "tok2"
    finally:
        check.close()

    state["kill_lists"] = False
    mark = len(stub.log)
    conn = open_file_db(db_path)
    try:
        engine = engine_for(conn, stub)
        try:
            resumed = engine.sync_account(ACCOUNT)
        finally:
            engine.close()
        assert resumed.ok is True, resumed.error
        assert resumed.full is True
        assert resumed.checkpoint == "300"
        assert db.get_thread(conn, ACCOUNT, "t3") is not None
        assert {row.id for row in db.all_mail(conn, ACCOUNT)} == \
            {"t1", "t2", "t3"}
        assert db.get_folder_page(conn, ACCOUNT,
                                  FULL_SYNC_PROGRESS_KEY) is None
    finally:
        conn.close()
    rerun = stub.log[mark:]
    lists = [entry for entry in rerun
             if entry["path"].endswith("/threads")]
    assert lists, "the resumed pass must continue the listing"
    assert all(entry["query"].get("pageToken") == ["tok2"]
               for entry in lists), \
        "the resumed pass must not re-pull landed pages"
    gets = [entry["path"] for entry in rerun if "/threads/" in
            entry["path"]]
    assert not any(path.endswith("/threads/t1") for path in gets)
    assert not any(path.endswith("/threads/t2") for path in gets)


def test_stub_offline_surfaces_without_crashing(stub, tmp_path):
    conn = open_file_db(tmp_path / "mail.db")
    db.upsert_account(conn, db.Account(
        email=ACCOUNT, display_name=ACCOUNT, history_id="50"))
    try:
        stub.route = lambda *args: "drop"
        engine = engine_for(conn, stub)
        try:
            outcome = engine.sync_account(ACCOUNT)
        finally:
            engine.close()
    finally:
        conn.close()
    assert outcome.ok is False
    assert outcome.offline is True
    assert outcome.error
    assert outcome.reauth is False


def test_sync_modules_import_no_gtk():
    import pathlib

    for name in ("gmail_client.py", "sync_service.py"):
        source = pathlib.Path(__file__).resolve().parent.parent / \
            "src" / "xx_email" / name
        text = source.read_text()
        for token in ("Gtk", "Gdk", "Adw"):
            assert token not in text, \
                f"{name} must stay off the UI toolkit"


def test_stub_revoked_grant_surfaces_reauth(stub, tmp_path):
    conn = open_file_db(tmp_path / "mail.db")
    try:
        stub.route = lambda *args: (401, {}, {"error": {"code": 401}})

        def refresh(email, rejected):
            raise ReauthNeeded("invalid_grant")

        engine = engine_for(conn, stub, tokens={ACCOUNT: "tok-old"},
                            refresh=refresh)
        try:
            outcome = engine.sync_account(ACCOUNT)
        finally:
            engine.close()
    finally:
        conn.close()
    assert outcome.ok is False
    assert outcome.reauth is True
