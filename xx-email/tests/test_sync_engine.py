"""Sync engine tests over the injectable transport (no network).

Covers the milestone 5 contract: first sync hydrates tabs, airplane
refresh errors then recovers, the second sync is delta-only (asserted
on the request log), history-overflow rebuilds from the held
checkpoint, 401 refreshes once then surfaces re-auth, and the
synchronous TokenStore stays wrapped in its worker.
"""

import json
import threading

import pytest

from xx_email import auth as auth_module
from xx_email import db
from xx_email.auth import ReauthNeeded
from xx_email.gmail_client import HttpResponse
from xx_email.sync_service import (
    SyncEngine,
    production_refresh,
)
from xx_email.token_store import (
    MemoryTokenBackend,
    TokenRecord,
    TokenStore,
    TokenStoreWorker,
)

ACCOUNT = "user@dev.xxemail"


class FakeTransport:
    """Scripted Gmail responses in call order; records every call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url,
                           "headers": dict(headers)})
        assert self.script, f"unexpected call: {method} {url}"
        step = self.script.pop(0)
        if step[0] == "ok":
            return HttpResponse(status=200,
                                body=json.dumps(step[1]).encode())
        if step[0] == "http":
            return HttpResponse(status=step[1],
                                body=json.dumps(step[2]).encode())
        raise step[1]


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


def make_engine(conn, script, tokens=None, refresh=None, **kwargs):
    transport = FakeTransport(script)
    engine = SyncEngine(conn, transport,
                        tokens={ACCOUNT: "tok"} if tokens is None
                        else tokens,
                        refresh=refresh,
                        clock=lambda: 1700000000000, **kwargs)
    engine.fake = transport
    return engine


@pytest.fixture
def conn():
    handle = db.connect(":memory:")
    db.migrate(handle)
    yield handle
    handle.close()


def urls(engine):
    return [call["url"] for call in engine.fake.calls]


def test_first_sync_hydrates_tabs_and_sets_checkpoint(conn):
    engine = make_engine(conn, [
        ("ok", profile("100")),
        ("ok", labels()),
        ("ok", thread_list("t1", "t2")),
        ("ok", thread_payload("t1", [("m1", ["INBOX",
                                             "CATEGORY_PERSONAL"])])),
        ("ok", thread_payload("t2", [("m2", ["INBOX", "UNREAD",
                                             "CATEGORY_SOCIAL"])])),
    ])
    outcome = engine.sync_account(ACCOUNT)
    assert outcome.ok is True
    assert outcome.full is True
    assert outcome.new_ids == [], "first sync must never notify"
    assert outcome.checkpoint == "100"
    assert db.get_account(conn, ACCOUNT).history_id == "100"
    assert {row.id for row in db.all_mail(conn, ACCOUNT)} == \
        {"t1", "t2"}
    page = db.get_folder_page(conn, ACCOUNT, "INBOX")
    assert page is not None and page.next_page_token is None
    assert any("users/me/profile" in url for url in urls(engine))
    assert any("me/threads?" in url for url in urls(engine))


def test_second_sync_is_delta_only(conn):
    first = make_engine(conn, [
        ("ok", profile("100")),
        ("ok", labels()),
        ("ok", thread_list("t1")),
        ("ok", thread_payload("t1", [("m1", ["INBOX",
                                             "CATEGORY_PERSONAL"])])),
    ])
    assert first.sync_account(ACCOUNT).ok is True
    engine = make_engine(conn, [
        ("ok", profile("120")),
        ("ok", labels()),
        ("ok", history([added("h1", "t3")], history_id="120")),
        ("ok", thread_payload("t3", [("m3", ["INBOX", "UNREAD",
                                             "CATEGORY_PERSONAL"])])),
    ])
    outcome = engine.sync_account(ACCOUNT)
    assert outcome.ok is True
    assert outcome.full is False
    assert outcome.new_ids == ["t3"]
    assert outcome.checkpoint == "h1"
    assert any("users/me/history" in url for url in urls(engine))
    assert not any("me/threads?" in url for url in urls(engine)), \
        "delta must not re-list threads"
    assert db.get_thread(conn, ACCOUNT, "t3") is not None


def test_history_expired_rebuilds_full(conn):
    db.upsert_account(conn, db.Account(
        email=ACCOUNT, display_name=ACCOUNT, history_id="old"))
    engine = make_engine(conn, [
        ("ok", profile("200")),
        ("ok", labels()),
        ("http", 404, {"error": {"code": 404}}),
        ("ok", thread_list("t9")),
        ("ok", thread_payload("t9", [("m9", ["INBOX"])])),
    ])
    outcome = engine.sync_account(ACCOUNT)
    assert outcome.ok is True
    assert outcome.checkpoint == "200", \
        "rebuild must advance past the expired id"
    assert outcome.follow_up is False
    assert db.get_thread(conn, ACCOUNT, "t9") is not None


def test_overflow_holds_checkpoint_and_follow_up_drains(conn):
    db.upsert_account(conn, db.Account(
        email=ACCOUNT, display_name=ACCOUNT, history_id="h0"))
    items = [added(f"h{n}", f"t{n}") for n in range(1, 62)]
    script = [("ok", profile("99")), ("ok", labels()),
              ("ok", history(items, history_id="99"))]
    for n in range(1, 61):
        script.append(("ok", thread_payload(
            f"t{n}", [(f"m{n}", ["INBOX"])])))
    seen = []
    engine = make_engine(conn, script,
                         on_follow_up=seen.append)
    outcome = engine.sync_account(ACCOUNT)
    assert outcome.ok is True
    assert outcome.follow_up is True
    assert seen == [ACCOUNT]
    assert outcome.checkpoint == "h60", \
        "checkpoint stops at the last hydrated item"
    assert db.get_thread(conn, ACCOUNT, "t60") is not None
    assert db.get_thread(conn, ACCOUNT, "t61") is None
    follow = make_engine(conn, [
        ("ok", profile("99")),
        ("ok", labels()),
        ("ok", history([added("h61", "t61")], history_id="99")),
        ("ok", thread_payload("t61", [("m61", ["INBOX"])])),
    ])
    assert follow.sync_account(ACCOUNT).ok is True
    assert db.get_thread(conn, ACCOUNT, "t61") is not None


def test_401_refreshes_once_then_succeeds(conn):
    refreshed = []

    def refresh(email, rejected):
        refreshed.append((email, rejected))
        return "tok-new"

    engine = make_engine(conn, [
        ("http", 401, {"error": {"code": 401}}),
        ("ok", profile("100")),
        ("ok", labels()),
        ("ok", thread_list()),
    ], tokens={ACCOUNT: "tok-old"}, refresh=refresh)
    outcome = engine.sync_account(ACCOUNT)
    assert outcome.ok is True
    assert refreshed == [(ACCOUNT, "tok-old")]
    bearers = [call["headers"]["Authorization"]
               for call in engine.fake.calls]
    assert bearers[0] == "Bearer tok-old"
    assert bearers[1] == "Bearer tok-new"


def test_second_401_surfaces_reauth_without_looping(conn):
    engine = make_engine(conn, [
        ("http", 401, {"error": {"code": 401}}),
        ("http", 401, {"error": {"code": 401}}),
    ], tokens={ACCOUNT: "tok-old"},
        refresh=lambda email, rejected: "tok-new")
    outcome = engine.sync_account(ACCOUNT)
    assert outcome.ok is False
    assert outcome.reauth is True
    assert len(engine.fake.calls) == 2


def test_offline_error_surfaces_without_moving_checkpoint(conn):
    db.upsert_account(conn, db.Account(
        email=ACCOUNT, display_name=ACCOUNT, history_id="50"))
    db.upsert_threads(conn, [db.Thread(account_email=ACCOUNT,
                                       id="t1", date=1000)])
    engine = make_engine(conn, [("raise", OSError("net down"))])
    outcome = engine.sync_account(ACCOUNT)
    assert outcome.ok is False
    assert outcome.offline is True
    assert outcome.error
    assert db.get_account(conn, ACCOUNT).history_id == "50"
    assert db.get_thread(conn, ACCOUNT, "t1") is not None


def test_server_error_is_not_offline(conn):
    engine = make_engine(conn, [
        ("http", 500, {"error": {"code": 500}}),
    ] * 5, retry_sleep=lambda seconds: None,
        retry_jitter=lambda: 0)
    outcome = engine.sync_account(ACCOUNT)
    assert outcome.ok is False
    assert outcome.offline is False
    assert outcome.reauth is False
    assert len(engine.fake.calls) == 5, \
        "a 500 must exhaust the retry budget before surfacing"


def test_deleted_message_prunes_empty_thread(conn):
    first = make_engine(conn, [
        ("ok", profile("100")),
        ("ok", labels()),
        ("ok", thread_list("t1")),
        ("ok", thread_payload("t1", [("m1", ["INBOX"])])),
    ])
    assert first.sync_account(ACCOUNT).ok is True
    engine = make_engine(conn, [
        ("ok", profile("110")),
        ("ok", labels()),
        ("ok", history([{"id": "h2",
                         "messagesDeleted": [{"message": {
                             "id": "m1"}}]}],
                       history_id="110")),
    ])
    assert engine.sync_account(ACCOUNT).ok is True
    assert db.get_message(conn, ACCOUNT, "m1") is None
    assert db.get_thread(conn, ACCOUNT, "t1") is None


def test_snoozed_thread_keeps_wake_across_rehydration(conn):
    first = make_engine(conn, [
        ("ok", profile("100")),
        ("ok", labels()),
        ("ok", thread_list("t1")),
        ("ok", thread_payload("t1", [("m1", ["INBOX"])])),
    ])
    assert first.sync_account(ACCOUNT).ok is True
    db.set_snoozed(conn, ACCOUNT, "t1", 1800000000000)
    engine = make_engine(conn, [
        ("ok", profile("110")),
        ("ok", labels()),
        ("ok", history([added("h2", "t1", mid="m2")],
                       history_id="110")),
        ("ok", thread_payload(
            "t1", [("m1", ["INBOX"]), ("m2", ["INBOX"])])),
    ])
    assert engine.sync_account(ACCOUNT).ok is True
    assert db.get_thread(conn, ACCOUNT,
                         "t1").snoozed_until == 1800000000000


def test_production_refresh_uses_token_worker(monkeypatch):
    import threading

    caller = threading.get_ident()
    idents = []

    class IdentBackend(MemoryTokenBackend):
        def load(self, account):
            idents.append(threading.get_ident())
            return super().load(account)

    worker = TokenStoreWorker(TokenStore(IdentBackend()))
    worker.save(ACCOUNT, TokenRecord(refresh_token="rt",
                                     client_id="cid"))
    seen = {}

    def fake_refresh(client_id, refresh_token, transport=None):
        seen["args"] = (client_id, refresh_token)
        return auth_module.TokenResponse(
            access_token="tok-fresh", refresh_token="rt2",
            expires_in=3600, id_token=None, raw={})

    monkeypatch.setattr(auth_module, "refresh_access_token",
                        fake_refresh)
    try:
        refresh = production_refresh(worker)
        assert refresh(ACCOUNT, "tok-stale") == "tok-fresh"
        stored = worker.load(ACCOUNT)
    finally:
        worker.close()
    assert seen["args"] == ("cid", "rt")
    assert stored.refresh_token == "rt2"
    assert idents and all(ident != caller for ident in idents)


def test_revoked_grant_surfaces_reauth(monkeypatch):
    worker = TokenStoreWorker(TokenStore(MemoryTokenBackend()))
    worker.save(ACCOUNT, TokenRecord(refresh_token="rt",
                                     client_id="cid"))

    def fake_refresh(client_id, refresh_token, transport=None):
        raise ReauthNeeded("invalid_grant")

    monkeypatch.setattr(auth_module, "refresh_access_token",
                        fake_refresh)
    try:
        with pytest.raises(ReauthNeeded):
            production_refresh(worker)(ACCOUNT, "tok-stale")
    finally:
        worker.close()


def test_sync_async_posts_outcome_back():
    import sqlite3
    import sys

    # The async pass runs on a pool thread, so the handed-over
    # connection must allow cross-thread use (single in-flight pass
    # here, so no extra locking needed).
    handle = sqlite3.connect(":memory:", check_same_thread=False)
    handle.row_factory = sqlite3.Row
    db.migrate(handle)
    engine = make_engine(handle, [
        ("ok", profile("100")),
        ("ok", labels()),
        ("ok", thread_list()),
    ])
    outcomes = []
    modules_before = set(sys.modules)
    try:
        try:
            import gi  # noqa: F401
            gi.require_version("GLib", "2.0")
            from gi.repository import GLib
        except (ImportError, ValueError):
            finished = threading.Event()
            engine.sync_async(
                ACCOUNT,
                lambda outcome: (outcomes.append(outcome),
                                 finished.set()))
            assert finished.wait(timeout=10)
        else:
            loop = GLib.MainLoop()

            def safety():
                loop.quit()
                return False

            GLib.timeout_add(10000, safety)
            engine.sync_async(ACCOUNT,
                              lambda outcome: (outcomes.append(outcome),
                                               loop.quit()))
            loop.run()
    finally:
        engine.close()
        handle.close()
        for name in [mod for mod in sys.modules
                     if mod not in modules_before]:
            del sys.modules[name]
    assert len(outcomes) == 1
    assert outcomes[0].ok is True, outcomes[0].error
    assert outcomes[0].checkpoint == "100"
