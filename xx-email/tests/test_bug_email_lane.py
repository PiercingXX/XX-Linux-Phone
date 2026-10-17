"""Lane bug_email regression tests: reviewed findings 5, 6, 14, 15.

Each test reproduces the reviewer-reported defect against the real
code path (no display needed). They failed before the fix and guard
the corrected behavior now.
"""

import json
import threading
import time

import pytest

from xx_email import db
from xx_email import outbox as outbox_mod
from xx_email.compose import validated_request
from xx_email.gmail_client import (
    GmailClient,
    GmailTransportError,
    HttpResponse,
)
from xx_email.sync_service import SyncEngine

ACCOUNT_A = "a@dev.xxemail"
ACCOUNT_B = "b@dev.xxemail"
NOW = 1_700_000_000_000


@pytest.fixture
def conn():
    handle = db.connect(":memory:")
    db.migrate(handle)
    yield handle
    handle.close()


@pytest.fixture
def files_dir(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    return root


def _enqueue(conn, files_dir, account, **overrides):
    request = validated_request("to@example.com", "", "", "Hi", "body")
    return outbox_mod.enqueue_send(
        conn, files_dir, account, request, None, 0, now_ms=NOW,
        **overrides)


class _FakeClient:
    """Minimal Gmail client double recording per-account sends."""

    def __init__(self):
        self.calls = []

    def send(self, payload, thread_id=None):
        self.calls.append((thread_id, bytes(payload)))
        return {"id": "sent"}


# ------------------------------------------------------------- finding 5


def test_drain_sends_only_the_syncing_account(conn, files_dir):
    entry_a = _enqueue(conn, files_dir, ACCOUNT_A)
    entry_b = _enqueue(conn, files_dir, ACCOUNT_B)
    engine = SyncEngine(conn, transport=None, files_dir=files_dir,
                        clock=lambda: NOW)
    try:
        engine._drain_outbox(ACCOUNT_A, _FakeClient())
    finally:
        engine.close()
    assert db.get_outbox(conn, entry_a).state == "SENT"
    row_b = db.get_outbox(conn, entry_b)
    assert row_b.state == "QUEUED", \
        "account B's queued mail must wait for B's own sync"
    assert row_b.account_email == ACCOUNT_B


def test_drain_send_receives_only_the_filtered_account(conn,
                                                         files_dir):
    seen = []
    _enqueue(conn, files_dir, ACCOUNT_A)
    _enqueue(conn, files_dir, ACCOUNT_B)

    def _send(account, thread_id, payload):
        seen.append(account)
        return {"id": "x"}

    outbox_mod.drain_due_outbox(conn, files_dir, _send, NOW,
                                account=ACCOUNT_B)
    assert seen == [ACCOUNT_B]


# ------------------------------------------------------------- finding 6


def test_undo_action_invokes_the_toast_callback():
    import subprocess
    import sys
    from pathlib import Path

    driver = Path(__file__).resolve().parent / \
        "undo_activate_driver.py"
    proc = subprocess.run([sys.executable, str(driver)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "UNDO_OK" in proc.stdout


# ------------------------------------------------------------ finding 14


def _scripted(script):
    def _transport(method, url, headers, body):
        assert script, f"unexpected call: {method} {url}"
        step = script.pop(0)
        if step[0] == "ok":
            return HttpResponse(status=200,
                                body=json.dumps(step[1]).encode())
        return HttpResponse(status=step[1],
                            body=json.dumps(step[2]).encode())
    return _transport


def _profile(history="100"):
    return {"emailAddress": ACCOUNT_A, "historyId": history,
            "messagesTotal": 1, "threadsTotal": 1}


def _labels():
    return {"labels": [{"id": "INBOX", "name": "INBOX",
                        "type": "system"}]}


def test_delta_404_prunes_thread_and_keeps_checkpoint(conn):
    db.upsert_account(conn, db.Account(
        email=ACCOUNT_A, display_name=ACCOUNT_A, history_id="h0"))
    db.upsert_threads(conn, [db.Thread(
        account_email=ACCOUNT_A, id="t-gone", date=1000)])
    db.upsert_messages(conn, [db.Message(
        account_email=ACCOUNT_A, id="m-gone", thread_id="t-gone",
        date=1000)])
    script = [
        ("ok", _profile("101")),
        ("ok", _labels()),
        ("ok", {"history": [{"id": "h1", "messagesAdded": [
            {"message": {"id": "m-gone", "threadId": "t-gone"}}]}],
               "historyId": "101"}),
        ("http", 404, {"error": {"code": 404}}),
    ]
    engine = SyncEngine(conn, _scripted(script),
                        tokens={ACCOUNT_A: "tok"},
                        clock=lambda: NOW)
    try:
        outcome = engine.sync_account(ACCOUNT_A)
    finally:
        engine.close()
    assert outcome.ok is True
    assert db.get_thread(conn, ACCOUNT_A, "t-gone") is None
    assert db.get_message(conn, ACCOUNT_A, "m-gone") is None
    assert db.get_account(conn, ACCOUNT_A).history_id == "h1", \
        "a permanently deleted thread must not pin the checkpoint"
    assert outcome.follow_up is False


# ---------------------------------------------------------- finding 15a


class _ScriptedTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        status, payload = self.responses.pop(0)
        raw = json.dumps(payload).encode() if isinstance(
            payload, dict) else payload
        return HttpResponse(status=status, body=raw)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_send_never_retries_server_errors(status):
    transport = _ScriptedTransport(
        (status, {"error": "busy"}), (200, {"id": "m", "threadId": "t"}))
    client = GmailClient(transport=transport,
                         get_token=lambda: "tok",
                         sleep=lambda seconds: None, jitter=lambda: 0)
    with pytest.raises(GmailTransportError):
        client.send(b"raw", "t1")
    assert len(transport.calls) == 1, \
        "a non-idempotent send must not be retried"


def test_drain_marks_server_error_failed_without_retry(conn, files_dir):
    class _HttpError(Exception):
        def __init__(self, status, message=""):
            super().__init__(message or f"HTTP {status}")
            self.status = status

    def _flaky(account, thread_id, payload):
        raise _HttpError(500, "server down")

    entry_id = _enqueue(conn, files_dir, ACCOUNT_A)
    results = outbox_mod.drain_due_outbox(conn, files_dir, _flaky,
                                          NOW)
    assert [r.outcome for r in results] == ["MARK_FAILED"]
    assert db.get_outbox(conn, entry_id).state == "FAILED"


def test_drain_marks_lost_reply_failed_without_retry(conn, files_dir):
    def _lost(account, thread_id, payload):
        raise OSError("reply lost after Gmail accepted the send")

    entry_id = _enqueue(conn, files_dir, ACCOUNT_A)
    results = outbox_mod.drain_due_outbox(conn, files_dir, _lost,
                                          NOW)
    assert [r.outcome for r in results] == ["MARK_FAILED"]
    assert db.get_outbox(conn, entry_id).state == "FAILED"


# ---------------------------------------------------------- finding 15b


def test_concurrent_drains_send_exactly_once(tmp_path):
    path = tmp_path / "race.db"
    setup = db.open_db(path)
    files = tmp_path / "files"
    files.mkdir()
    request = validated_request("to@example.com", "", "", "Hi",
                                "body")
    entry_id = outbox_mod.enqueue_send(
        setup, files, ACCOUNT_A, request, None, 0, now_ms=NOW)
    setup.close()
    entered = threading.Event()
    release = threading.Event()

    def _slow_send(account, thread_id, payload):
        entered.set()
        assert release.wait(timeout=30)
        return {"id": "sent"}

    first_results = []
    second_results = []

    def _drain_into(bucket):
        handle = db.connect(str(path))
        try:
            bucket.extend(outbox_mod.drain_due_outbox(
                handle, files, _slow_send, NOW))
        finally:
            handle.close()

    worker = threading.Thread(target=_drain_into,
                              args=(first_results,))
    worker.start()
    assert entered.wait(timeout=30)
    rival = threading.Thread(target=_drain_into,
                             args=(second_results,))
    rival.start()
    time.sleep(0.5)
    assert rival.is_alive(), \
        "the second drain must wait instead of failing the live row"
    release.set()
    worker.join(timeout=30)
    rival.join(timeout=30)
    assert not worker.is_alive() and not rival.is_alive()
    assert second_results == [], \
        "a drain must not touch a row another drain is sending"
    assert [r.outcome for r in first_results] == ["MARK_SENT"]
    check = db.connect(str(path))
    try:
        assert check.execute(
            "SELECT state FROM outbox WHERE id = ?",
            (entry_id,)).fetchone()[0] == "SENT"
    finally:
        check.close()


# ---------------------------------------------------------- finding 15c


def _thread_payload(tid, mid, subject="Sub"):
    return {
        "id": tid, "snippet": "s", "historyId": "5",
        "messages": [{
            "id": mid, "threadId": tid, "labelIds": ["INBOX"],
            "snippet": "s", "historyId": "5",
            "internalDate": "1000",
            "payload": {"mimeType": "text/plain", "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From",
                 "value": "jane@example.com"}]}}],
    }


def test_hydrate_keeps_downloaded_bodies(conn):
    db.upsert_messages(conn, [db.Message(
        account_email=ACCOUNT_A, id="m1", thread_id="t1",
        subject="Old", date=1000)])
    db.update_message_bodies(conn, ACCOUNT_A, "m1", "<p>hi</p>",
                             "hi", "[]")
    engine = SyncEngine(conn, _scripted(
        [("ok", _thread_payload("t1", "m1", subject="New"))]),
        tokens={ACCOUNT_A: "tok"}, clock=lambda: NOW)
    try:
        assert engine.hydrate(ACCOUNT_A, ["t1"]) == set()
    finally:
        engine.close()
    row = db.get_message(conn, ACCOUNT_A, "m1")
    assert row.subject == "New"
    assert (row.body_html, row.body_plain, row.body_fetched) == \
        ("<p>hi</p>", "hi", True)
    assert row.attachments_json == "[]"
