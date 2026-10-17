"""Compose / outbox / schedule / attachment tests (no network, no display).

Ports the Android ComposeViewModel/MimeComposer/OutboxWorker contract
to the Linux pipeline: reply/reply-all/forward prefill exactly like
the app (quoting plus In-Reply-To/References), offline enqueue,
the undo claim-vs-cancel race, scheduled drain, the 18 MB attachment
budget with a 3 MB multipart case, and local draft autosave.
"""

import base64
import threading
from datetime import datetime, timedelta, timezone

import pytest

from xx_email import compose as compose_mod
from xx_email import contacts as contacts_mod
from xx_email import db
from xx_email import outbox as outbox_mod
from xx_email.compose import (
    OriginalMessage,
    StagedAttachment,
)

ACCOUNT = "user@dev.xxemail"
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


def _request(**overrides):
    fields = {"to_raw": "alice@example.com", "cc_raw": "",
              "bcc_raw": "", "subject": "Hello",
              "body": "Hi there"}
    fields.update(overrides)
    return compose_mod.validated_request(
        fields["to_raw"], fields["cc_raw"], fields["bcc_raw"],
        fields["subject"], fields["body"])


def _original(**overrides):
    fields = {
        "id": "m1", "thread_id": "t1", "subject": "Lunch?",
        "from_address": "Ada <ada@example.com>",
        "to_csv": "user@dev.xxemail",
        "cc_csv": "bo@example.com",
        "date": NOW, "snippet": "Tacos at noon",
        "body_plain": "Tacos at noon",
        "message_id_header": "<parent@example.com>",
        "references_header": "<grand@example.com>",
        "has_attachments": False,
    }
    fields.update(overrides)
    return OriginalMessage(**fields)


class HttpError(Exception):
    def __init__(self, status, message=""):
        super().__init__(message or f"HTTP {status}")
        self.status = status


# ------------------------------------------------------- threading headers


def test_references_combines_and_dedups_folded_chains():
    assert compose_mod.build_references("<a@x>", "<b@x>") == \
        "<a@x> <b@x>"
    assert compose_mod.build_references(None, "<b@x>") == "<b@x>"
    assert compose_mod.build_references("<a@x>", None) == "<a@x>"
    assert compose_mod.build_references(None, None) is None
    assert compose_mod.build_references("<a@x>\n <b@x>",
                                        "<b@x>") == "<a@x> <b@x>"


def test_compose_carries_threading_headers():
    request = _request()
    request.in_reply_to = "<abc@example.com>"
    payload = compose_mod.compose_message(ACCOUNT, request)
    text = payload.decode("utf-8")
    assert "In-Reply-To: <abc@example.com>" in text
    assert "References: <abc@example.com>" in text


def test_compose_extends_prior_references_chain():
    request = _request()
    request.in_reply_to = "<c@example.com>"
    request.references = "<a@example.com> <b@example.com>"
    text = compose_mod.compose_message(ACCOUNT, request).decode()
    assert "References: <a@example.com> <b@example.com> " \
        "<c@example.com>" in text


# ------------------------------------------------------------- MIME basics


def test_compose_simple_message_headers():
    text = compose_mod.compose_message(ACCOUNT, _request()).decode()
    assert f"From: {ACCOUNT}" in text
    assert "alice@example.com" in text
    assert "Subject: Hello" in text
    assert "Hi there" in text


def test_compose_encodes_non_ascii_subject():
    request = _request(subject="Grüße aus München")
    text = compose_mod.compose_message(ACCOUNT, request).decode()
    assert "subject: =?utf-8?" in text.lower()


def test_compose_rejects_missing_recipients():
    with pytest.raises(ValueError):
        compose_mod.compose_message(
            ACCOUNT, compose_mod.ComposeRequest(subject="s"))


def test_validated_request_rejects_blank_recipients():
    with pytest.raises(ValueError, match="No valid recipients"):
        compose_mod.validated_request("  ", "nope", "", "s", "b")
    assert compose_mod.validated_request(
        "", "Bob <bo@example.com>", "", "s", "b").cc == \
        ["bo@example.com"]


def test_explicit_attachment_type_wins_over_guess(tmp_path):
    blob = tmp_path / "blob-without-extension.bin"
    blob.write_bytes(b"\x01\x02\x03")
    staged = compose_mod.StagedAttachment(
        name="blob-without-extension.bin", mime_type="image/png",
        data=blob.read_bytes())
    request = _request()
    request.attachments = [staged]
    text = compose_mod.compose_message(ACCOUNT, request).decode(
        "utf-8", "replace")
    assert "multipart/mixed" in text
    assert "Content-Type: image/png" in text
    assert "application/octet-stream" not in text.split("\r\n\r\n")[0]


def test_compose_rejects_oversized_attachment_total():
    big = StagedAttachment(name="big.bin",
                           mime_type="application/octet-stream",
                           data=b"x" * (compose_mod
                                        .MAX_TOTAL_ATTACHMENT_BYTES + 1))
    request = _request()
    request.attachments = [big]
    with pytest.raises(ValueError, match="18 MB"):
        compose_mod.compose_message(ACCOUNT, request)


def test_three_mb_attachment_sends_as_multipart_mixed(tmp_path):
    size = 3 * 1024 * 1024
    source = tmp_path / "report.pdf"
    with open(source, "wb") as handle:
        handle.write(b"%PDF-1.4 body\n" * 4096)
        handle.truncate(size)
    stored, name, staged_size = compose_mod.store_upload(
        tmp_path / "uploads", "report.pdf", source)
    assert staged_size == size
    assert name == "report.pdf"
    staged = StagedAttachment(
        name=name, mime_type=compose_mod.guess_mime(name),
        data=stored.read_bytes())
    assert staged.mime_type == "application/pdf"
    request = _request(subject="with file", body="see attached")
    request.attachments = [staged]
    payload = compose_mod.compose_message(ACCOUNT, request)
    assert len(payload) > size
    assert b"multipart/mixed" in payload
    assert b"report.pdf" in payload
    assert b"Content-Type: application/pdf" in payload
    parsed = __import__("email").message_from_bytes(payload)
    parts = [part for part in parsed.walk()
             if part.get_content_disposition() == "attachment"]
    assert len(parts) == 1
    assert parts[0].get_payload(decode=True) == stored.read_bytes()
    raw = compose_mod.raw_base64url(payload)
    assert base64.urlsafe_b64decode(raw + "===") == payload


def test_store_upload_caps_huge_files(tmp_path):
    source = tmp_path / "huge.bin"
    with open(source, "wb") as handle:
        handle.seek(compose_mod.MAX_UPLOAD_BYTES)
        handle.write(b"\x00")
    with pytest.raises(ValueError, match="18 MB"):
        compose_mod.store_upload(tmp_path / "uploads", "huge.bin",
                                 source)


def test_stage_upload_sanitizes_and_caps(tmp_path):
    source = tmp_path / "note.txt"
    source.write_bytes(b"hello")
    staged = compose_mod.stage_upload(tmp_path / "uploads",
                                      "../evil.txt", source,
                                      mime_type="text/plain")
    assert staged.name == "evil.txt"
    assert staged.mime_type == "text/plain"
    assert staged.data == b"hello"
    with pytest.raises(ValueError):
        compose_mod.stage_upload(tmp_path / "uploads", "..", source)


def test_guess_mime_falls_back_to_octet_stream():
    assert compose_mod.guess_mime("a.pdf") == "application/pdf"
    assert compose_mod.guess_mime("a.JPG") == "image/jpeg"
    assert compose_mod.guess_mime("a.unknownext") == \
        "application/octet-stream"


# ------------------------------------------------------------ prefill modes


def test_reply_prefill_matches_android():
    prefill = compose_mod.prefill_for_mode(
        ACCOUNT, "reply", _original(), initial_subject="")
    assert prefill.to == "ada@example.com"
    assert prefill.cc == ""
    assert prefill.subject == "Re: Lunch?"
    assert prefill.in_reply_to == "<parent@example.com>"
    assert prefill.references == \
        "<grand@example.com> <parent@example.com>"
    assert prefill.thread_id == "t1"
    assert "> Tacos at noon" in prefill.body
    assert "wrote:" in prefill.body
    assert not prefill.offer_attachments


def test_reply_to_self_falls_back_to_original_to():
    original = _original(from_address="Me <user@dev.xxemail>",
                         to_csv="ada@example.com")
    prefill = compose_mod.prefill_for_mode(ACCOUNT, "reply",
                                           original)
    assert prefill.to == "ada@example.com"


def test_reply_all_keeps_cc_minus_self():
    prefill = compose_mod.prefill_for_mode(
        ACCOUNT, "reply_all", _original())
    assert prefill.to == "ada@example.com"
    assert prefill.cc == "bo@example.com"
    assert prefill.subject == "Re: Lunch?"


def test_forward_clears_recipients_and_offers_attachments():
    original = _original(has_attachments=True)
    prefill = compose_mod.prefill_for_mode(
        ACCOUNT, "forward", original, initial_subject="")
    assert prefill.to == ""
    assert prefill.cc == ""
    assert prefill.subject == "Fwd: Lunch?"
    assert not any(line.startswith("> ") for line in
                   prefill.body.splitlines())
    assert "Tacos at noon" in prefill.body
    assert prefill.in_reply_to == "<parent@example.com>"
    assert prefill.offer_attachments


def test_forward_keeps_existing_fwd_prefix():
    original = _original(subject="Fwd: Lunch?")
    prefill = compose_mod.prefill_for_mode(
        ACCOUNT, "forward", original, initial_subject="Fwd: Lunch?")
    assert prefill.subject == "Fwd: Lunch?"


def test_blank_quote_keeps_initial_values():
    prefill = compose_mod.prefill_for_mode(
        ACCOUNT, "new", OriginalMessage(), initial_to="a@x",
        initial_cc="b@x", initial_subject="Hi",
        thread_id_arg="")
    assert (prefill.to, prefill.cc,
            prefill.subject) == ("a@x", "b@x", "Hi")
    assert prefill.thread_id is None


def test_prefill_key_distinguishes_drafts():
    key = compose_mod.prefill_key(ACCOUNT, "", "", "", "t1", "m1",
                                  "reply")
    assert key == compose_mod.prefill_key(ACCOUNT, "", "", "", "t1",
                                          "m1", "reply")
    assert key != compose_mod.prefill_key(ACCOUNT, "", "", "", "t1",
                                          "m2", "reply")


def test_quote_header_names_unknown_date_and_sender():
    original = _original(date=0, from_address="",
                         body_plain="hello")
    prefill = compose_mod.prefill_for_mode(ACCOUNT, "reply",
                                           original)
    assert "an unknown date" in prefill.body
    assert "an unknown sender" in prefill.body


# ----------------------------------------------------------- schedule/draft


def test_schedule_presets_mirror_gmail_labels():
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    presets = compose_mod.schedule_presets(now)
    assert [label for label, _ in presets] == [
        "Tomorrow 8:00 AM", "Tomorrow 1:00 PM", "Monday 8:00 AM",
        "In 1 hour"]
    labels = {label: at for label, at in presets}
    assert labels["In 1 hour"] == int(
        (now + timedelta(hours=1)).timestamp() * 1000)
    assert labels["Tomorrow 8:00 AM"] < labels["Tomorrow 1:00 PM"]


def test_draft_autosave_round_trip(tmp_path):
    fields = {"to": "a@x", "cc": "", "bcc": "",
              "subject": "Hi", "body": "hello"}
    compose_mod.save_draft(tmp_path, ACCOUNT, fields, "t1", "m1",
                           "reply")
    assert compose_mod.load_draft(tmp_path, ACCOUNT, "t1", "m1",
                                  "reply") == fields
    compose_mod.clear_draft(tmp_path, ACCOUNT, "t1", "m1", "reply")
    assert compose_mod.load_draft(tmp_path, ACCOUNT, "t1", "m1",
                                  "reply") is None


def test_draft_lists_newest_first_for_account(tmp_path):
    one = {"to": "a@x", "cc": "", "bcc": "", "subject": "one",
           "body": "1"}
    two = {"to": "b@x", "cc": "", "bcc": "", "subject": "two",
           "body": "2"}
    compose_mod.save_draft(tmp_path, ACCOUNT, one, "", "", "new")
    first = compose_mod.draft_path(tmp_path, ACCOUNT, "", "", "new")
    compose_mod.save_draft(tmp_path, "other@dev.xxemail", two, "",
                           "", "new")
    compose_mod.save_draft(tmp_path, ACCOUNT, two, "t9", "", "new")
    rows = compose_mod.list_drafts(tmp_path, ACCOUNT)
    assert [row["subject"] for row in rows] == ["two", "one"]
    assert all(row["account"] == ACCOUNT for row in rows)
    assert first.exists()


def test_corrupt_draft_reads_as_missing(tmp_path):
    target = compose_mod.draft_path(tmp_path, ACCOUNT, "", "",
                                    "new")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")
    assert compose_mod.load_draft(tmp_path, ACCOUNT, "", "",
                                  "new") is None


def test_outbox_status_names_scheduled_items():
    assert compose_mod.outbox_status_line("SEND", "QUEUED", NOW) == \
        "Queued"
    scheduled = compose_mod.outbox_status_line(
        "SCHEDULED_SEND", "QUEUED", NOW + 3_600_000, now_ms=NOW)
    assert scheduled.startswith("Scheduled")
    assert compose_mod.outbox_status_line(
        "SEND", "FAILED", NOW, error="boom") == "Failed — boom"
    assert compose_mod.outbox_status_line(
        "SEND", "SENDING", NOW) == "Sending…"


# ------------------------------------------------------ offline queue / undo


def _enqueue(conn, files_dir, **overrides):
    fields = {"scheduled_at": None, "undo_seconds": 10,
              "now_ms": NOW}
    fields.update(overrides)
    return outbox_mod.enqueue_fields(
        conn, files_dir, ACCOUNT, "alice@example.com", "", "",
        "Hello", "Hi there", thread_id="t1",
        scheduled_at=fields["scheduled_at"],
        undo_seconds=fields["undo_seconds"],
        now_ms=fields["now_ms"])


def test_airplane_compose_queues_without_network(conn, files_dir):
    entry_id = _enqueue(conn, files_dir)
    row = db.get_outbox(conn, entry_id)
    assert row.state == "QUEUED"
    assert row.kind == "SEND"
    assert row.target_at == NOW + 10_000
    assert row.subject == "Hello"
    payload = outbox_mod.resolve_payload(conn, files_dir, row)
    assert b"alice@example.com" in payload
    assert row.size == len(payload)


def test_scheduled_send_stores_target_and_kind(conn, files_dir):
    entry_id = _enqueue(conn, files_dir,
                        scheduled_at=NOW + 3_600_000)
    row = db.get_outbox(conn, entry_id)
    assert row.kind == "SCHEDULED_SEND"
    assert row.target_at == NOW + 3_600_000


def test_enqueue_rejects_bad_recipients_without_a_row(conn,
                                                      files_dir):
    with pytest.raises(ValueError):
        outbox_mod.enqueue_fields(
            conn, files_dir, ACCOUNT, "  ", "nope", "", "s", "b",
            undo_seconds=10, now_ms=NOW)
    assert db.queued_outbox(conn) == []


def test_undo_inside_window_wins_and_drain_sends_nothing(conn,
                                                        files_dir):
    sent = []
    entry_id = _enqueue(conn, files_dir, undo_seconds=30)
    assert outbox_mod.cancel_queued_send(conn, files_dir,
                                        entry_id) is True
    assert db.get_outbox(conn, entry_id) is None
    results = outbox_mod.drain_due_outbox(
        conn, files_dir, lambda *a: sent.append(a),
        NOW + 60_000)
    assert sent == []
    assert results == []


def test_cancel_after_claim_loses_the_race(conn, files_dir):
    entry_id = _enqueue(conn, files_dir, undo_seconds=0)
    assert db.claim_outbox_if_queued(conn, entry_id) is True
    assert outbox_mod.cancel_queued_send(conn, files_dir,
                                        entry_id) is False
    assert db.get_outbox(conn, entry_id).state == "SENDING"


def test_cancel_after_send_refuses(conn, files_dir):
    entry_id = _enqueue(conn, files_dir, undo_seconds=0)
    outbox_mod.drain_due_outbox(
        conn, files_dir, lambda *a: None, NOW)
    assert db.get_outbox(conn, entry_id).state == "SENT"
    assert outbox_mod.cancel_queued_send(conn, files_dir,
                                        entry_id) is False


def test_claim_vs_cancel_race_exactly_one_winner(tmp_path):
    path = tmp_path / "race.db"
    setup = db.open_db(path)
    ids = []
    for index in range(60):
        ids.append(db.insert_outbox(setup, db.OutboxEntry(
            account_email=ACCOUNT, kind="SEND", subject="r",
            target_at=NOW, state="QUEUED", created_at=NOW)))
    setup.close()
    barrier = threading.Barrier(2)
    outcomes: list[tuple] = []
    lock = threading.Lock()

    def _claim():
        handle = db.connect(str(path))
        try:
            for entry_id in ids:
                barrier.wait(timeout=10)
                won = db.claim_outbox_if_queued(handle, entry_id)
                with lock:
                    outcomes.append(("claim", entry_id, won))
        finally:
            handle.close()

    def _cancel():
        handle = db.connect(str(path))
        try:
            for entry_id in ids:
                barrier.wait(timeout=10)
                won = db.cancel_outbox_if_queued(handle, entry_id)
                with lock:
                    outcomes.append(("cancel", entry_id, won))
        finally:
            handle.close()

    claimer = threading.Thread(target=_claim)
    canceller = threading.Thread(target=_cancel)
    claimer.start()
    canceller.start()
    claimer.join(timeout=60)
    canceller.join(timeout=60)
    assert not claimer.is_alive() and not canceller.is_alive()
    by_id: dict[int, dict] = {}
    for side, entry_id, won in outcomes:
        by_id.setdefault(entry_id, {})[side] = won
    assert len(by_id) == len(ids)
    for entry_id, votes in by_id.items():
        assert votes.get("claim", False) + votes.get("cancel",
                                                     False) == 1, \
            entry_id


# ------------------------------------------------------------ drain / dedup


def test_queued_sends_once_online_then_drain_is_quiet(conn,
                                                     files_dir):
    calls = []
    entry_id = _enqueue(conn, files_dir, undo_seconds=0)
    results = outbox_mod.drain_due_outbox(
        conn, files_dir,
        lambda account, thread_id, payload: calls.append(
            (account, thread_id, payload)), NOW)
    assert [r.outcome for r in results] == ["MARK_SENT"]
    assert len(calls) == 1
    account, thread_id, payload = calls[0]
    assert (account, thread_id) == (ACCOUNT, "t1")
    assert b"alice@example.com" in payload
    row = db.get_outbox(conn, entry_id)
    assert row.state == "SENT"
    again = outbox_mod.drain_due_outbox(
        conn, files_dir, lambda *a: calls.append(a), NOW + 1_000)
    assert again == []
    assert len(calls) == 1


def test_scheduled_send_waits_for_its_time(conn, files_dir):
    calls = []
    entry_id = _enqueue(conn, files_dir,
                        scheduled_at=NOW + 3_600_000)
    early = outbox_mod.drain_due_outbox(
        conn, files_dir, lambda *a: calls.append(a), NOW)
    assert early == [] and calls == []
    assert db.get_outbox(conn, entry_id).state == "QUEUED"
    late = outbox_mod.drain_due_outbox(
        conn, files_dir, lambda *a: calls.append(a),
        NOW + 3_600_000)
    assert [r.outcome for r in late] == ["MARK_SENT"]
    assert len(calls) == 1


def test_undo_window_holds_the_send(conn, files_dir):
    calls = []
    _enqueue(conn, files_dir, undo_seconds=30)
    held = outbox_mod.drain_due_outbox(
        conn, files_dir, lambda *a: calls.append(a), NOW)
    assert held == [] and calls == []
    released = outbox_mod.drain_due_outbox(
        conn, files_dir, lambda *a: calls.append(a),
        NOW + 30_000)
    assert len(calls) == 1
    assert [r.outcome for r in released] == ["MARK_SENT"]


def test_decode_failure_after_2xx_still_counts_as_sent(conn,
                                                      files_dir):
    def _send(account, thread_id, payload):
        _ = (account, thread_id, payload)
        raise HttpError(200, "undecodable body")

    entry_id = _enqueue(conn, files_dir, undo_seconds=0)
    results = outbox_mod.drain_due_outbox(conn, files_dir, _send,
                                          NOW)
    assert [r.outcome for r in results] == ["MARK_SENT"]
    assert db.get_outbox(conn, entry_id).state == "SENT"


def test_permanent_rejection_marks_failed(conn, files_dir):
    def _send(account, thread_id, payload):
        _ = (account, thread_id, payload)
        raise HttpError(400, "bad request")

    entry_id = _enqueue(conn, files_dir, undo_seconds=0)
    results = outbox_mod.drain_due_outbox(conn, files_dir, _send,
                                          NOW)
    assert [r.outcome for r in results] == ["MARK_FAILED"]
    row = db.get_outbox(conn, entry_id)
    assert row.state == "FAILED"
    assert row.error == "bad request"


def test_server_error_fails_without_retry(conn, files_dir):
    calls = []

    def _flaky(account, thread_id, payload):
        _ = (account, thread_id, payload)
        calls.append(1)
        raise HttpError(500, "server down")

    entry_id = _enqueue(conn, files_dir, undo_seconds=0)
    results = outbox_mod.drain_due_outbox(conn, files_dir,
                                          _flaky, NOW)
    assert [r.outcome for r in results] == ["MARK_FAILED"]
    assert db.get_outbox(conn, entry_id).state == "FAILED"
    assert len(calls) == 1, \
        "the upload is not idempotent: never retry it automatically"


def test_transport_error_fails_without_retry(conn,
                                            files_dir):
    def _offline(account, thread_id, payload):
        _ = (account, thread_id, payload)
        raise OSError("no route to host")

    entry_id = _enqueue(conn, files_dir, undo_seconds=0)
    results = outbox_mod.drain_due_outbox(conn, files_dir,
                                          _offline, NOW)
    assert [r.outcome for r in results] == ["MARK_FAILED"]
    assert db.get_outbox(conn, entry_id).state == "FAILED"


def test_send_interrupted_by_death_recovers_to_failed(conn,
                                                      files_dir):
    calls = []
    entry_id = _enqueue(conn, files_dir, undo_seconds=0)
    assert db.claim_outbox_if_queued(conn, entry_id) is True
    results = outbox_mod.drain_due_outbox(
        conn, files_dir, lambda *a: calls.append(a), NOW)
    assert calls == []
    assert (results[0].entry_id, results[0].state) == (
        entry_id, "FAILED")
    assert db.get_outbox(conn, entry_id).error == \
        outbox_mod.INTERRUPTED_ERROR


def test_legacy_base64_row_migrates_to_file_on_send(conn,
                                                   files_dir):
    payload = b"From: a@x\r\nTo: b@x\r\nSubject: old\r\n\r\nbody\r\n"
    raw = base64.urlsafe_b64encode(payload).decode("ascii")
    entry_id = db.insert_outbox(conn, db.OutboxEntry(
        account_email=ACCOUNT, kind="SEND", rfc822_base64=raw,
        subject="old", target_at=NOW, state="QUEUED",
        created_at=NOW))
    seen = []
    results = outbox_mod.drain_due_outbox(
        conn, files_dir,
        lambda account, thread_id, body: seen.append(body), NOW)
    assert [r.outcome for r in results] == ["MARK_SENT"]
    assert seen == [payload]
    row = db.get_outbox(conn, entry_id)
    assert row.rfc822_base64 is None
    assert row.path == f"outbox/{entry_id}.eml"


def test_retry_and_discard_follow_android_gates(conn, files_dir):
    entry_id = _enqueue(conn, files_dir, undo_seconds=0)
    assert outbox_mod.retry_failed_send(conn, entry_id) is False
    db.set_outbox_state(conn, entry_id, "FAILED", error="down")
    assert outbox_mod.retry_failed_send(conn, entry_id,
                                        "other@x") is False
    assert outbox_mod.retry_failed_send(conn, entry_id,
                                        ACCOUNT) is True
    assert db.get_outbox(conn, entry_id).state == "QUEUED"
    assert outbox_mod.discard_outbox(conn, files_dir, entry_id,
                                     ACCOUNT) is True
    assert db.get_outbox(conn, entry_id) is None
    other = _enqueue(conn, files_dir, undo_seconds=0)
    db.set_outbox_state(conn, other, "SENDING")
    assert outbox_mod.discard_outbox(conn, files_dir, other,
                                     ACCOUNT) is False
    assert outbox_mod.retry_failed_sends(conn, ACCOUNT) == 0
    db.set_outbox_state(conn, other, "FAILED", error="x")
    assert outbox_mod.retry_failed_sends(conn, ACCOUNT) == 1


def test_due_outbox_lists_only_elapsed_rows(conn, files_dir):
    early = _enqueue(conn, files_dir, undo_seconds=0)
    _enqueue(conn, files_dir, scheduled_at=NOW + 9_999_999)
    assert [row.id for row in outbox_mod.due_outbox(
        conn, NOW)] == [early]


# ------------------------------------------------------- sync engine wiring


def test_engine_drain_sends_due_outbox_on_its_pass(conn,
                                                  files_dir):
    from xx_email.sync_service import SyncEngine

    _enqueue(conn, files_dir, undo_seconds=0)
    calls = []

    class _Client:
        def send(self, payload, thread_id):
            calls.append((thread_id, payload))
            return {"id": "sent-1"}

    engine = SyncEngine(conn, transport=None, files_dir=files_dir,
                        clock=lambda: NOW)
    try:
        engine._drain_outbox(ACCOUNT, _Client())
    finally:
        engine.close()
    assert len(calls) == 1
    assert db.queued_outbox(conn) == []


def test_engine_without_files_dir_skips_drain(conn, files_dir):
    from xx_email.sync_service import SyncEngine

    entry_id = _enqueue(conn, files_dir, undo_seconds=0)
    engine = SyncEngine(conn, transport=None, clock=lambda: NOW)
    try:
        engine._drain_outbox(ACCOUNT, object())
    finally:
        engine.close()
    assert db.get_outbox(conn, entry_id).state == "QUEUED"


def test_engine_send_and_archive_removes_inbox(conn, files_dir):
    from xx_email.sync_service import SyncEngine

    db.upsert_threads(conn, [db.Thread(
        account_email=ACCOUNT, id="t1", date=NOW,
        labels_csv="INBOX", in_inbox=True)])
    _enqueue(conn, files_dir, undo_seconds=0)
    modified = []

    class _Client:
        def send(self, payload, thread_id):
            _ = payload
            return {"id": "sent-1"}

        def modify_thread(self, thread_id, add=None,
                          remove=None):
            modified.append((thread_id, add, remove))
            return None

    engine = SyncEngine(conn, transport=None, files_dir=files_dir,
                        clock=lambda: NOW, send_and_archive=True)
    try:
        engine._drain_outbox(ACCOUNT, _Client())
    finally:
        engine.close()
    assert modified == [("t1", None, ["INBOX"])]
    row = db.get_thread(conn, ACCOUNT, "t1")
    assert row.in_inbox is False


# ------------------------------------------------------------- autocomplete


def test_normalize_suggestions_reads_real_search_tuples():
    rows = contacts_mod.normalize_suggestions(
        [("u1", "Ada Lovelace", "ada@example.com"),
         ("u2", "  ", "x@y"),
         ("u3", "", "")])
    assert rows == [("u1", "Ada Lovelace", "ada@example.com")]
    assert contacts_mod.normalize_suggestions("oops") == []
    many = [(f"u{i}", f"Name {i}", "") for i in range(30)]
    assert len(contacts_mod.normalize_suggestions(many,
                                                  limit=8)) == 8


def test_suggestions_without_contacts_service_call_back_empty(
        monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "gi", None)
    seen = []
    contacts_mod.search_suggestions_async("ada", seen.append)
    assert seen == [[]]


def test_eds_suggestion_fallback_stays_empty():
    assert contacts_mod.search_eds_suggestions("ada@example.com",
                                               8) == []


def test_contacts_search_signature_matches_service_xml():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    candidates = (root / "ref" / "xx-contacts",
                  root.parent / "xx-contacts")
    service = next((p / "src" / "xx_contacts" / "service.py"
                    for p in candidates if (p / "src" / "xx_contacts"
                                            / "service.py").exists()),
                   None)
    if service is None:
        pytest.skip("xx-contacts source not found")
    xml = service.read_text(encoding="utf-8")
    assert contacts_mod.CONTACTS_BUS_NAME in xml
    assert '<arg direction="in" type="s" name="query"/>' in xml
    assert '<arg direction="in" type="u" name="limit"/>' in xml
    assert '<arg direction="out" type="a(sss)"' in xml


# ---------------------------------------------------------- compose contract


def _compose_source():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "src" /
            "xx_email" / "ui" / "compose_page.py").read_text(
                encoding="utf-8")


def test_compose_uses_portal_picker_not_legacy_dialog():
    text = _compose_source()
    assert "Gtk.FileDialog" in text
    assert "open_multiple" in text
    assert "FileChooserDialog" not in text
    assert "get_filename" not in text
    assert "dialog.run" not in text


def test_compose_fields_wait_for_tap_with_one_documented_exception():
    text = _compose_source()
    assert "tap_to_focus" in text
    assert "Explicit-open exception" in text
    assert "Adw.Dialog" in text
    assert "set_ellipsize" in text or "set_wrap" in text


def test_tap_to_focus_waits_for_capture_tap():
    from pathlib import Path
    common = (Path(__file__).resolve().parent.parent / "src" /
              "xx_email" / "ui" / "common.py").read_text(
                  encoding="utf-8")
    assert "set_can_focus(False)" in common
    assert "PropagationPhase.CAPTURE" in common


def test_compose_sends_off_the_main_loop():
    text = _compose_source()
    for marker in ("subprocess", "time.sleep", "call_sync"):
        assert marker not in text
    assert "Gtk.FileDialog" in text


def test_outbox_folder_row_names_scheduled_state():
    from xx_email import mailbox as mailbox_mod

    entry = db.OutboxEntry(
        id=7, account_email=ACCOUNT, kind="SCHEDULED_SEND",
        subject="Board deck", target_at=NOW + 3_600_000,
        state="QUEUED", created_at=NOW)
    row = mailbox_mod.outbox_display_row(entry, NOW)
    assert row.id == "outbox-7"
    assert row.subject == "Board deck"
    assert row.snippet.startswith("Scheduled")
    failed = db.OutboxEntry(
        id=8, account_email=ACCOUNT, kind="SEND", subject="Hi",
        target_at=NOW, state="FAILED", error="down",
        created_at=NOW)
    assert mailbox_mod.outbox_display_row(failed, NOW).snippet == \
        "Failed — down"


def test_full_date_line_matches_android_shape():
    line = compose_mod.full_date(NOW)
    assert line and "AM" in line or "PM" in line
    assert str(datetime.fromtimestamp(
        NOW / 1000).astimezone().year) in line
    assert compose_mod.full_date(0) == ""


def test_seeded_ms_dates_fit_int64_gobject():
    assert NOW > 2_147_483_647
    from pathlib import Path
    common = (Path(__file__).resolve().parent.parent / "src" /
              "xx_email" / "ui" / "common.py").read_text(
                  encoding="utf-8")
    assert "GObject.Property(type=int)" not in common

