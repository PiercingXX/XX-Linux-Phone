"""Server-driven fills behind the mailbox UI: hydrate + folder pages.

Covers the two public SyncEngine seams the mailbox/search pages use
on worker threads — load-more pages (list, hydrate, persist the new
cursor) and server-search hydration — over a scripted transport.
"""

import json

from xx_email import db
from xx_email.gmail_client import HttpResponse
from xx_email.sync_service import SyncEngine

ACCOUNT = "user@dev.xxemail"


class DictTransport:
    """Route canned Gmail payloads by URL shape; records every call."""

    def __init__(self, thread_pages, threads):
        self.thread_pages = list(thread_pages)
        self.threads = dict(threads)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append(url)
        if url.endswith("/profile"):
            return _ok({"emailAddress": ACCOUNT, "historyId": "9"})
        if url.endswith("/labels"):
            return _ok({"labels": []})
        if "/threads/" in url:
            tid = url.split("/threads/")[1].split("?")[0]
            if tid in self.threads:
                return _ok(self.threads[tid])
            return HttpResponse(status=404, body=b"{}")
        if "/threads" in url:
            page = self.thread_pages.pop(0)
            return _ok(page)
        raise AssertionError("unexpected call: " + url)


def _ok(payload):
    return HttpResponse(status=200, body=json.dumps(payload).encode())


def thread_payload(tid, date="1001"):
    return {
        "id": tid, "snippet": "snip", "historyId": "5",
        "messages": [{
            "id": "m-" + tid, "threadId": tid,
            "labelIds": ["INBOX", "UNREAD"], "snippet": "s",
            "historyId": "5", "internalDate": date,
            "payload": {"mimeType": "text/plain", "headers": [
                {"name": "Subject", "value": "Sub " + tid},
                {"name": "From", "value": "jane@example.com"},
            ]},
        }],
    }


def make_engine(conn, transport):
    engine = SyncEngine(conn, transport, tokens={ACCOUNT: "tok"},
                        clock=lambda: 1700000000000)
    db.upsert_account(conn, db.Account(email=ACCOUNT))
    return engine


def open_conn():
    handle = db.connect(":memory:")
    db.migrate(handle)
    return handle


def test_fetch_folder_page_hydrates_and_persists_cursor():
    conn = open_conn()
    transport = DictTransport(
        [{"threads": [{"id": "t1"}, {"id": "t2"}],
          "nextPageToken": "tok-2"}],
        {"t1": thread_payload("t1"), "t2": thread_payload("t2")})
    engine = make_engine(conn, transport)
    try:
        rows, next_token = engine.fetch_folder_page(
            ACCOUNT, "INBOX", ["INBOX"], False, None)
        assert [row.id for row in rows] == ["t1", "t2"]
        assert next_token == "tok-2"
        saved = db.get_folder_page(conn, ACCOUNT, "INBOX")
        assert saved.next_page_token == "tok-2"
        assert db.get_thread(conn, ACCOUNT, "t1") is not None
    finally:
        engine.close()
        conn.close()


def test_fetch_folder_page_skips_failed_hydration():
    conn = open_conn()
    transport = DictTransport(
        [{"threads": [{"id": "t1"}, {"id": "gone"}]}],
        {"t1": thread_payload("t1")})
    engine = make_engine(conn, transport)
    try:
        rows, next_token = engine.fetch_folder_page(
            ACCOUNT, "SENT", ["SENT"], False, "tok-1")
        assert [row.id for row in rows] == ["t1"]
        assert next_token is None
    finally:
        engine.close()
        conn.close()


def test_hydrate_returns_failed_ids_for_search_fill():
    conn = open_conn()
    transport = DictTransport([], {"t1": thread_payload("t1")})
    engine = make_engine(conn, transport)
    try:
        failed = engine.hydrate(ACCOUNT, ["t1", "gone"])
        assert failed == {"gone"}
        assert db.get_thread(conn, ACCOUNT, "t1") is not None
        assert db.get_thread(conn, ACCOUNT, "gone") is None
    finally:
        engine.close()
        conn.close()
