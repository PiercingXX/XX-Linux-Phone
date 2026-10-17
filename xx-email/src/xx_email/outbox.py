"""Outbox send pipeline: offline queue, undo race, scheduled drain.

Ports ``MailRepository.enqueueSend``/``cancelQueuedSend``/
``retryFailedSend(s)``/``discardOutbox`` and ``OutboxWorker`` from
``ref/xx-email``. Blocking sqlite + file work by contract: call it on
a worker thread, never on the GTK main loop. Nothing here touches the
network directly — the caller injects ``send`` (Gmail ``messages.send``
over the worker's client), so enqueue succeeds with no network at all
(the airplane-mode contract: enqueue reports "Queued", never a
failed send).

Duplicate-send avoidance follows the Android approach. Gmail send
is not idempotent, so the port never retries a message that left the
device: a 2xx always wins (even when the response body fails to
decode), SENT is recorded before any post-send work, the QUEUED to
SENDING claim is a two-sided atomic compare against undo's QUEUED to
CANCELLED flip (whichever wins, the other deterministically loses),
and a row found SENDING with no live send (process death mid-send)
surfaces as FAILED for banner retry instead of silently resending.
"""

from __future__ import annotations

import base64
import contextlib
import os
import threading
import time
from dataclasses import dataclass

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

from xx_email import db as db_mod
from xx_email.compose import (
    ComposeRequest,
    StagedAttachment,
    compose_message,
    validated_request,
)
from xx_email.domain import (
    OutboxFiles,
    OutboxSend,
    SendCancelPolicy,
    SendOutcome,
    SendRetryPolicy,
)

MAX_ATTEMPTS = SendRetryPolicy.MAX_ATTEMPTS
INTERRUPTED_ERROR = "send interrupted"

_DRAIN_LOCK = threading.Lock()


@contextlib.contextmanager
def _exclusive_drain(files_dir: str | os.PathLike):
    """Serialize drains in-process and across processes.

    A second drain must never mark a row another live drain is
    sending as interrupted: the stale write flips the in-flight row
    to FAILED and a later retry sends a second copy. The thread lock
    covers same-process workers; the file lock covers the daemon.
    """
    with _DRAIN_LOCK:
        handle = None
        if fcntl is not None:
            try:
                handle = open(os.path.join(os.fspath(files_dir),
                                           "outbox.lock"), "a+b")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except OSError:
                if handle is not None:
                    handle.close()
                handle = None
        try:
            yield
        finally:
            if handle is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()


@dataclass
class DrainResult:
    """Outcome of one drained row (values for tests and the UI)."""

    entry_id: int
    outcome: str
    state: str
    error: str | None = None


def enqueue_send(conn, files_dir: str | os.PathLike,
                 account_email: str, request: ComposeRequest,
                 scheduled_at: int | None, undo_seconds: int,
                 now_ms: int | None = None) -> int:
    """Queue a send: MIME compose, file-backed payload, QUEUED row.

    Never touches the network. Returns the outbox row id (used by
    undo). Raises ValueError for bad fields, oversized attachments,
    or payload write failures (the half-written row is removed).
    """
    payload = compose_message(account_email, request)
    moment = now_ms if now_ms is not None else int(time.time() * 1000)
    plan = OutboxSend.plan(now_ms=moment, undo_seconds=undo_seconds,
                           scheduled_at=scheduled_at)
    draft = OutboxSend.entity(account_email, request.thread_id,
                              request.subject, plan, moment)
    entry_id = db_mod.insert_outbox(conn, db_mod.OutboxEntry(
        account_email=draft.account_email, kind=draft.kind,
        thread_id=draft.thread_id, subject=draft.subject,
        target_at=draft.target_at, state=draft.state,
        created_at=draft.created_at))
    try:
        path, size = OutboxSend.write_payload(files_dir, entry_id,
                                              payload)
        db_mod.set_outbox_payload(conn, entry_id, path, size)
    except Exception:
        try:
            db_mod.delete_outbox(conn, entry_id)
        except Exception:
            pass
        raise
    return entry_id


def enqueue_fields(conn, files_dir: str | os.PathLike,
                   account_email: str, to_raw: str, cc_raw: str,
                   bcc_raw: str, subject: str, body: str, *,
                   thread_id: str | None = None,
                   in_reply_to: str | None = None,
                   references: str | None = None,
                   attachments: list[StagedAttachment] | None = None,
                   scheduled_at: int | None = None,
                   undo_seconds: int = 10,
                   now_ms: int | None = None) -> int:
    """Validate raw compose fields and enqueue (page-facing shortcut)."""
    request = validated_request(
        to_raw, cc_raw, bcc_raw, subject, body, thread_id=thread_id,
        in_reply_to=in_reply_to, references=references,
        attachments=attachments)
    return enqueue_send(conn, files_dir, account_email, request,
                        scheduled_at, undo_seconds, now_ms=now_ms)


def cancel_queued_send(conn, files_dir: str | os.PathLike,
                       entry_id: int) -> bool:
    """Undo-send: drop a still-QUEUED row (worker work is cancelled too).

    The conditional flip is the undo side of the claim-vs-undo race:
    once the worker claimed the row (SENDING) or finished it, this
    refuses with False. The caller cancels the scheduled worker kick
    first, mirroring ``cancelUniqueWork`` on Android.
    """
    row = db_mod.get_outbox(conn, entry_id)
    if row is None or not SendCancelPolicy.can_undo(row.state):
        return False
    if not db_mod.cancel_outbox_if_queued(conn, entry_id):
        return False
    OutboxFiles.delete_payload_file(files_dir, row.path, entry_id)
    db_mod.delete_outbox(conn, entry_id)
    return True


def retry_failed_send(conn, entry_id: int,
                      account_email: str | None = None) -> bool:
    """Flip one FAILED row back to QUEUED for redelivery."""
    row = db_mod.get_outbox(conn, entry_id)
    if row is None or not OutboxSend.can_retry(row.state):
        return False
    if account_email is not None and \
            row.account_email != account_email:
        return False
    db_mod.set_outbox_state(conn, entry_id, "QUEUED", error=None)
    return True


def discard_outbox(conn, files_dir: str | os.PathLike, entry_id: int,
                   account_email: str | None = None) -> bool:
    """Discard a QUEUED or FAILED row, payload file included."""
    row = db_mod.get_outbox(conn, entry_id)
    if row is None or not OutboxSend.can_discard(row.state):
        return False
    if account_email is not None and \
            row.account_email != account_email:
        return False
    if row.state == "QUEUED":
        if not db_mod.cancel_outbox_if_queued(conn, entry_id):
            return False
    OutboxFiles.delete_payload_file(files_dir, row.path, entry_id)
    db_mod.delete_outbox(conn, entry_id)
    return True


def retry_failed_sends(conn, account_email: str) -> int:
    """Retry-all for the failed-send banner; returns rows retried."""
    rows = db_mod.failed_outbox_for_account(conn, account_email)
    for row in rows:
        db_mod.set_outbox_state(conn, row.id, "QUEUED", error=None)
    return len(rows)


def due_outbox(conn, now_ms: int,
               account: str | None = None) -> list:
    """QUEUED rows whose undo window / schedule time has passed."""
    return [entry for entry in db_mod.queued_outbox(conn, account)
            if entry.target_at <= now_ms]


def resolve_payload(conn, files_dir: str | os.PathLike,
                    entry) -> bytes:
    """Payload bytes for one row, migrating legacy base64 rows to files.

    Prefers the file-backed payload; a legacy row carrying only the
    base64 column is decoded, persisted to disk, and cleared off the
    row so upgraded queues keep sending without cursor-sized reads.
    """
    resolved = OutboxFiles.resolve(files_dir, entry.path)
    if resolved is not None:
        return resolved.read_bytes()
    if entry.rfc822_base64:
        text = entry.rfc822_base64.strip()
        text += "=" * (-len(text) % 4)
        raw = base64.urlsafe_b64decode(text)
        try:
            path, size = OutboxSend.write_payload(files_dir, entry.id,
                                                  bytes(raw))
            db_mod.set_outbox_payload(conn, entry.id, path, size)
            db_mod.clear_legacy_payload(conn, entry.id)
        except Exception:
            pass
        return bytes(raw)
    raise OSError(f"Outbox #{entry.id} payload file is missing "
                  f"({entry.path})")


def drain_due_outbox(conn, files_dir: str | os.PathLike, send,
                     now_ms: int,
                     max_attempts: int = MAX_ATTEMPTS,
                     on_sent=None,
                     account: str | None = None) -> list[DrainResult]:
    """Send every due QUEUED row through ``send`` (worker thread only).

    ``send(account_email, thread_id, payload_bytes)`` performs the
    Gmail upload and returns on success; any exception carries an
    optional ``status`` attribute (HTTP status when the server
    answered). Rows still inside their undo window or scheduled for
    the future are left QUEUED; SENDING rows with no live send are
    recovered to FAILED. When ``account`` is given, only that
    account's rows are touched, so one account's sync can never send
    another account's queued mail. A send error marks the row FAILED
    for banner retry instead of requeueing: the upload is not
    idempotent, so an automatic retry can deliver a second copy.
    Never raises: per-row failures land in the returned results.
    """
    results: list[DrainResult] = []
    with _exclusive_drain(files_dir):
        for stale in db_mod.sending_outbox(conn, account):
            db_mod.set_outbox_state(conn, stale.id, "FAILED",
                                    error=INTERRUPTED_ERROR)
            results.append(DrainResult(entry_id=stale.id,
                                       outcome="FAILED",
                                       state="FAILED",
                                       error=INTERRUPTED_ERROR))
        for entry in db_mod.queued_outbox(conn, account):
            if entry.target_at > now_ms:
                continue
            if not db_mod.claim_outbox_if_queued(conn, entry.id):
                continue
            db_mod.increment_outbox_attempts(conn, entry.id)
            attempts_used = entry.attempts + 1
            try:
                payload = resolve_payload(conn, files_dir, entry)
            except Exception as exc:
                results.append(_apply_retry(
                    conn, files_dir, entry, attempts_used, None, True,
                    str(exc), max_attempts))
                continue
            try:
                send(entry.account_email, entry.thread_id, payload)
            except Exception as exc:
                status = getattr(exc, "status", None)
                if isinstance(status, int) and \
                        200 <= status <= 299:
                    outcome = SendOutcome.MARK_SENT
                else:
                    outcome = SendOutcome.MARK_FAILED
                results.append(_apply_retry(
                    conn, files_dir, entry, attempts_used, None, False,
                    str(exc) or None, max_attempts,
                    forced=outcome))
                continue
            db_mod.set_outbox_state(conn, entry.id, "SENT")
            OutboxFiles.delete_payload_file(files_dir, entry.path,
                                            entry.id)
            if on_sent is not None:
                try:
                    on_sent(entry)
                except Exception:
                    pass
            results.append(DrainResult(entry_id=entry.id,
                                       outcome="MARK_SENT",
                                       state="SENT"))
    return results


def _apply_retry(conn, files_dir, entry, attempts_used: int,
                 status: int | None, transport_error: bool,
                 error: str | None, max_attempts: int,
                 forced=None) -> DrainResult:
    outcome = forced if forced is not None else \
        SendRetryPolicy.decide(status, transport_error)
    if outcome == SendOutcome.MARK_SENT:
        db_mod.set_outbox_state(conn, entry.id, "SENT")
        OutboxFiles.delete_payload_file(files_dir, entry.path,
                                        entry.id)
        return DrainResult(entry_id=entry.id, outcome="MARK_SENT",
                           state="SENT")
    if outcome == SendOutcome.MARK_FAILED:
        db_mod.set_outbox_state(conn, entry.id, "FAILED", error=error)
        return DrainResult(entry_id=entry.id, outcome="MARK_FAILED",
                           state="FAILED", error=error)
    if attempts_used >= max_attempts:
        db_mod.set_outbox_state(conn, entry.id, "FAILED", error=error)
        return DrainResult(entry_id=entry.id, outcome="MARK_FAILED",
                           state="FAILED", error=error)
    db_mod.set_outbox_state(conn, entry.id, "QUEUED", error=error)
    return DrainResult(entry_id=entry.id, outcome="RETRY",
                       state="QUEUED", error=error)
