"""Mailbox / thread / search model for milestone 6 (stdlib only, no GTK).

Ports the UI-driving parts of the Android app that stay unit-testable:
``MailRepository.observeFolder`` folder dispatch, ``folderListQuery`` /
``folderPageKey`` pagination keys, the local-first thread actions with
their ``Undoable`` reverts (server calls are recorded as intents the UI
worker executes — never run here), ``searchServer`` / ``searchLocal``
routing and the local display projection, ``ThreadRow`` time labels,
the multi-select set, notification-tap routing (``NotificationTapNav``)
and the ``Routes`` helpers.

Threading: every function here is blocking sqlite work. The GTK pages
call in through a worker thread and post results with GLib.idle_add —
never on the main loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from xx_email import compose as compose_mod
from xx_email import db
from xx_email.domain import (
    INBOX_TABS,
    AddressUtils,
    LabelCsv,
    MailboxFolder,
)

INBOX_PAGE_KEY = "INBOX"
PAGE_SIZE = 50

OTHER_FOLDERS = (
    MailboxFolder.STARRED,
    MailboxFolder.SNOOZED,
    MailboxFolder.SENT,
    MailboxFolder.OUTBOX,
    MailboxFolder.DRAFTS,
    MailboxFolder.SPAM,
    MailboxFolder.TRASH,
    MailboxFolder.ALL_MAIL,
)

OPERATOR_HINTS = (
    "from:alice@example.com",
    "has:attachment",
    "after:2026/01/01 before:2026/02/01",
    "subject:invoice is:unread",
    "newer_than:7d",
)

SWIPE_LABELS = {
    "ARCHIVE": "Archive",
    "DELETE": "Delete",
    "MARK_READ": "Mark read",
    "STAR": "Star",
    "SNOOZE": "Snooze",
}


def swipe_label(action: str) -> str:
    """Text-first label for a swipe action (no icons on this UI)."""
    return SWIPE_LABELS.get(action, action)


def folder_page_key(folder: MailboxFolder) -> str | None:
    """Pagination cursor key. Inbox tabs share one INBOX listing/cursor."""
    if folder.category is not None:
        return INBOX_PAGE_KEY
    if folder.label_id is not None:
        return folder.label_id
    if folder == MailboxFolder.ALL_MAIL:
        return folder.name
    return None


def folder_list_query(folder: MailboxFolder) -> tuple[list[str] | None, bool]:
    """Server list params for a folder: (labelIds, includeSpamTrash).

    The second element is True only for TRASH/SPAM — Gmail excludes
    those unless includeSpamTrash is set (required to hydrate them).
    A None label list means a local-only view with no paging.
    """
    if folder.category is not None:
        return (["INBOX", folder.category], False)
    if folder.label_id is not None:
        return ([folder.label_id],
                folder.label_id == "TRASH" or folder.label_id == "SPAM")
    return (None, False)


def threads_for_folder(conn, account: str, folder: MailboxFolder,
                       limit: int = 200) -> list:
    """Local rows for a folder (the observeFolder dispatch, snapshot form)."""
    if folder.category is not None:
        return db.inbox_category(conn, account, folder.category or "",
                                 folder.include_empty_primary)
    if folder == MailboxFolder.STARRED:
        return db.starred_threads(conn, account)
    if folder == MailboxFolder.SNOOZED:
        return db.snoozed_threads(conn, account)
    if folder == MailboxFolder.ALL_MAIL:
        return db.all_mail(conn, account, limit=limit)
    if folder == MailboxFolder.OUTBOX:
        return []
    return db.threads_with_label(conn, account, folder.label_id or "",
                                 limit=limit)


def outbox_for_folder(conn, account: str) -> list:
    """Queued + failed + in-flight rows for the local Outbox folder."""
    return db.open_outbox_for_account(conn, account)


def outbox_display_row(entry, now_ms: int) -> db.Thread:
    """Project an outbox row onto a thread-shaped display row.

    The note names the queued/scheduled/sending/failed state, so the
    Outbox folder doubles as the scheduled-send list.
    """
    return db.Thread(
        account_email=entry.account_email, id=f"outbox-{entry.id}",
        snippet=compose_mod.outbox_status_line(
            entry.kind, entry.state, entry.target_at, entry.error,
            now_ms),
        subject=entry.subject or "(no subject)",
        from_address=entry.account_email, from_name="",
        date=entry.created_at, unread_count=0, starred=False,
        has_attachments=False,
    )


def should_search_server(query: str) -> bool:
    """Operator queries go server-side; plain terms hit the local index."""
    return ":" in (query or "")


def search_local_threads(conn, account: str, query: str) -> list:
    """Offline search grouped to threads, newest hit first.

    Ports MailRepository.searchLocal: FTS hits over subject/snippet/from
    group by thread; the latest hit's fields render the row, but inbox
    membership comes from the real aggregate when the thread exists
    locally so results never pretend every hit left the inbox.
    """
    hits = db.search_local(conn, account, query)
    if not hits:
        return []
    by_thread: dict[str, list] = {}
    for message in hits:
        by_thread.setdefault(message.thread_id, []).append(message)
    rows = []
    for thread_id, messages in by_thread.items():
        latest = max(messages, key=lambda m: m.date)
        aggregate = db.get_thread(conn, account, thread_id)
        rows.append(db.Thread(
            account_email=account, id=thread_id,
            snippet=latest.snippet, subject=latest.subject,
            from_address=latest.from_address,
            from_name=AddressUtils.split(latest.from_address)[0],
            date=latest.date, message_count=len(messages),
            unread_count=sum(1 for m in messages if not m.read),
            has_attachments=any(m.has_attachments for m in messages),
            starred=any(m.starred for m in messages),
            in_inbox=aggregate.in_inbox if aggregate is not None else False,
            labels_csv=aggregate.labels_csv
            if aggregate is not None else latest.labels_csv,
        ))
    rows.sort(key=lambda t: t.date, reverse=True)
    return rows


def time_ago(now_ms: int, epoch_ms: int) -> str:
    """Compact age label for a thread row ("now", "5m", "3h", "2d")."""
    if not epoch_ms or epoch_ms <= 0:
        return ""
    minutes = (now_ms - epoch_ms) // 60_000
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{minutes:d}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours:d}h"
    days = hours // 24
    if days < 7:
        return f"{days:d}d"
    return datetime.fromtimestamp(epoch_ms / 1000).strftime("%b %-d")


def time_ago_now(epoch_ms: int) -> str:
    return time_ago(int(time.time() * 1000), epoch_ms)


@dataclass
class Undoable:
    """A completed local-first action the user can revert while open.

    server_ops records the Gmail calls the worker already ran (in order)
    so a revert can mirror them; revert(conn) restores the local rows.
    """

    message: str
    server_ops: list = field(default_factory=list)
    _revert: object = field(default=None, repr=False)

    def revert(self, conn) -> None:
        assert self._revert is not None
        self._revert(conn)


def _snapshot(conn, account: str,
              thread_ids: list[str]) -> dict[str, db.Thread]:
    return {tid: db.get_thread(conn, account, tid)
            for tid in thread_ids
            if db.get_thread(conn, account, tid) is not None}


def _restore(conn, account: str, before: dict[str, db.Thread]) -> None:
    for row in before.values():
        db.set_inbox_and_labels(conn, account, row.id, row.in_inbox,
                                row.labels_csv)
        db.set_snoozed(conn, account, row.id, row.snoozed_until)
        current = db.get_thread(conn, account, row.id)
        if current is not None:
            up = db.Thread(**{**current.__dict__, "starred": row.starred,
                               "unread_count": row.unread_count})
            db.upsert_threads(conn, [up])


def apply_local_action(conn, account: str, action: str,
                       thread_ids: list[str], wake_at: int | None = None,
                       label_id: str | None = None,
                       add_label: bool = True) -> Undoable:
    """Apply a thread action to the local store; return its undo.

    Mirrors MailRepository's local-first mutations verbatim (the next
    delta sync reconciles any divergence when the server call later
    fails). Raises ValueError for missing snooze/label arguments.
    """
    ids = list(thread_ids)
    if action == "ARCHIVE":
        before = _snapshot(conn, account, ids)
        for tid in ids:
            row = db.get_thread(conn, account, tid)
            if row is None:
                continue
            db.set_inbox_and_labels(conn, account, tid, False,
                                    LabelCsv.remove(row.labels_csv, "INBOX"))
        ops = [("modify", tid, [], ["INBOX"]) for tid in ids]
        return Undoable("Archived", ops,
                        lambda c: _restore(c, account, before))
    if action == "DELETE":
        before = _snapshot(conn, account, ids)
        for tid in ids:
            row = db.get_thread(conn, account, tid)
            if row is None:
                continue
            db.set_inbox_and_labels(
                conn, account, tid, False,
                LabelCsv.add(LabelCsv.remove(row.labels_csv, "INBOX"),
                             "TRASH"))
        ops = [("trash", tid, [], []) for tid in ids]
        return Undoable("Moved to Trash", ops,
                        lambda c: _restore(c, account, before))
    if action == "MARK_READ":
        before = _snapshot(conn, account, ids)
        for tid in ids:
            row = db.get_thread(conn, account, tid)
            if row is None:
                continue
            db.set_inbox_and_labels(
                conn, account, tid, row.in_inbox,
                LabelCsv.remove(row.labels_csv, "UNREAD"))
            current = db.get_thread(conn, account, tid)
            db.upsert_threads(conn, [db.Thread(
                **{**current.__dict__, "unread_count": 0})])
        ops = [("modify", tid, [], ["UNREAD"]) for tid in ids]
        return Undoable("Marked read", ops,
                        lambda c: _restore(c, account, before))
    if action == "MARK_UNREAD":
        before = _snapshot(conn, account, ids)
        for tid in ids:
            row = db.get_thread(conn, account, tid)
            if row is None:
                continue
            db.set_inbox_and_labels(
                conn, account, tid, row.in_inbox,
                LabelCsv.add(row.labels_csv, "UNREAD"))
            current = db.get_thread(conn, account, tid)
            db.upsert_threads(conn, [db.Thread(
                **{**current.__dict__,
                   "unread_count": max(current.unread_count, 1)})])
        ops = [("modify", tid, ["UNREAD"], []) for tid in ids]
        return Undoable("Marked unread", ops,
                        lambda c: _restore(c, account, before))
    if action == "STAR":
        before = _snapshot(conn, account, ids)
        for tid in ids:
            row = db.get_thread(conn, account, tid)
            if row is None:
                continue
            flipped = not row.starred
            labels = LabelCsv.add(row.labels_csv, "STARRED") if flipped \
                else LabelCsv.remove(row.labels_csv, "STARRED")
            db.set_inbox_and_labels(conn, account, tid, row.in_inbox,
                                    labels)
            current = db.get_thread(conn, account, tid)
            db.upsert_threads(conn, [db.Thread(
                **{**current.__dict__, "starred": flipped})])
        ops = [("modify", tid, [], []) for tid in ids]
        return Undoable("Star updated", ops,
                        lambda c: _restore(c, account, before))
    if action == "REPORT_SPAM":
        before = _snapshot(conn, account, ids)
        for tid in ids:
            row = db.get_thread(conn, account, tid)
            if row is None:
                continue
            db.set_inbox_and_labels(
                conn, account, tid, False,
                LabelCsv.add(LabelCsv.remove(row.labels_csv, "INBOX"),
                             "SPAM"))
        ops = [("modify", tid, ["SPAM"], ["INBOX"]) for tid in ids]
        return Undoable("Reported spam", ops,
                        lambda c: _restore(c, account, before))
    if action == "APPLY_LABEL":
        if not label_id:
            raise ValueError("APPLY_LABEL needs a label_id")
        before = _snapshot(conn, account, ids)
        for tid in ids:
            row = db.get_thread(conn, account, tid)
            if row is None:
                continue
            labels = LabelCsv.add(row.labels_csv, label_id) if add_label \
                else LabelCsv.remove(row.labels_csv, label_id)
            db.set_inbox_and_labels(conn, account, tid, row.in_inbox,
                                    labels)
        verb = "Label applied" if add_label else "Label removed"
        add = [label_id] if add_label else []
        remove = [] if add_label else [label_id]
        ops = [("modify", tid, add, remove) for tid in ids]
        return Undoable(verb, ops, lambda c: _restore(c, account, before))
    if action == "SNOOZE":
        if wake_at is None:
            raise ValueError("SNOOZE needs a wake_at epoch-ms time")
        before = _snapshot(conn, account, ids)
        now_ms = int(time.time() * 1000)
        for tid in ids:
            row = db.get_thread(conn, account, tid)
            if row is None:
                continue
            db.set_snoozed(conn, account, tid, wake_at)
            db.set_inbox_and_labels(conn, account, tid, False,
                                    LabelCsv.remove(row.labels_csv, "INBOX"))
            db.upsert_snooze_wake(conn, db.SnoozeWake(account, tid,
                                                      wake_at, now_ms))
        ops = [("modify", tid, [], ["INBOX"]) for tid in ids]
        return Undoable("Snoozed" if len(ids) == 1
                        else f"{len(ids):d} snoozed", ops,
                        lambda c: _unsnooze_rows(c, account, ids))
    if action == "UNSNOOZE":
        _unsnooze_rows(conn, account, ids)
        ops = [("modify", tid, ["INBOX"], []) for tid in ids]
        return Undoable("Unsnoozed", ops,
                        lambda c: _restore(c, account, {}))
    raise ValueError(f"Unknown thread action: {action}")


def _unsnooze_rows(conn, account: str, thread_ids: list[str]) -> None:
    for tid in thread_ids:
        db.delete_snooze_wake(conn, account, tid)
        db.set_snoozed(conn, account, tid, None)
        row = db.get_thread(conn, account, tid)
        if row is not None:
            db.set_inbox_and_labels(conn, account, tid, True,
                                    LabelCsv.add(row.labels_csv, "INBOX"))


def default_expanded_ids(messages: list) -> set[str]:
    """Message ids expanded on thread open: the last unread only.

    Everything else stays collapsed until tapped. When nothing is
    unread, the newest message expands so the thread never opens
    blank; an empty thread expands nothing.
    """
    ids = [getattr(message, "id", "") for message in messages]
    ids = [mid for mid in ids if mid]
    if not ids:
        return set()
    for message in reversed(messages):
        if not getattr(message, "read", True):
            mid = getattr(message, "id", "")
            if mid:
                return {mid}
    return {ids[-1]}


def toggle_expanded(expanded: set[str], message_id: str) -> set[str]:
    """Flip one message's collapsed state; returns the new set."""
    updated = set(expanded)
    if message_id in updated:
        updated.discard(message_id)
    else:
        updated.add(message_id)
    return updated


def _outcome_field(outcome, name: str, default=None):
    if isinstance(outcome, dict):
        return outcome.get(name, default)
    return getattr(outcome, name, default)


def sync_status_line(outcome=None, syncing: bool = False) -> str:
    """Sync state line under the mailbox list (from SyncOutcome).

    Covers the refresh button's states: syncing, fresh success (with
    the new-mail count), offline cache, sign-in-again, and failures.
    Empty when nothing happened yet.
    """
    if syncing:
        return "Syncing…"
    if outcome is None:
        return ""
    if _outcome_field(outcome, "reauth", False):
        return "Sign in again to keep syncing"
    if _outcome_field(outcome, "offline", False):
        return "Offline — showing downloaded mail"
    error = _outcome_field(outcome, "error", None)
    if error:
        return f"Sync failed — {error}"
    if not _outcome_field(outcome, "ok", False):
        return ""
    fresh = _outcome_field(outcome, "new_ids", []) or []
    if len(fresh) == 1:
        return "Synced — 1 new message"
    if fresh:
        return f"Synced — {len(fresh):d} new messages"
    return "Synced"


def search_status(server: bool, offline: bool, count: int) -> str:
    """Result note for the search page (offline fallback is visible)."""
    if count <= 0:
        if offline:
            return "Offline — no downloaded matches"
        return "No results on the server" if server else "No results"
    if offline:
        return "Offline results"
    return "Server results" if server else "Downloaded results"


class Selection:
    """Ordered multi-select set for long-press selection."""

    def __init__(self) -> None:
        self._ids: list[str] = []

    def __len__(self) -> int:
        return len(self._ids)

    def __contains__(self, thread_id: object) -> bool:
        return thread_id in self._ids

    def __iter__(self):
        return iter(list(self._ids))

    def toggle(self, thread_id: str) -> bool:
        """Flip membership; returns True when now selected."""
        if thread_id in self._ids:
            self._ids.remove(thread_id)
            return False
        self._ids.append(thread_id)
        return True

    def clear(self) -> None:
        del self._ids[:]

    def ids(self) -> list[str]:
        return list(self._ids)


def notification_tap_action(current_route: str | None,
                            current_account: str | None,
                            target_account: str | None,
                            target_thread_id: str | None) -> tuple[str, str,
                                                                  str | None]:
    """Route a new-mail notification tap (NotificationTapNav port).

    Returns ("thread", account, threadId) when already on the target
    mailbox (push the thread), ("mailbox", account, None) when
    elsewhere (land on the mailbox first), ("noop", "", None) with no
    actionable target.
    """
    if not target_account or not target_thread_id:
        return ("noop", "", None)
    on_target = current_account is not None and \
        current_route == route_mailbox(current_account) and \
        current_account == target_account
    if on_target:
        return ("thread", target_account, target_thread_id)
    return ("mailbox", target_account, None)


def route_mailbox(account: str) -> str:
    return f"mailbox/{account}"


def route_thread(account: str, thread_id: str) -> str:
    return f"thread/{account}/{thread_id}"


def route_search(account: str) -> str:
    return f"search/{account}"


def start_route(accounts: list[str], last_account: str | None,
                tap_account: str | None) -> str:
    """Cold-start destination: tap target, last-used, first, or setup."""
    if tap_account is not None and tap_account in accounts:
        return route_mailbox(tap_account)
    if last_account is not None and last_account in accounts:
        return route_mailbox(last_account)
    if accounts:
        return route_mailbox(accounts[0])
    return "setup"


def tabs() -> list:
    return list(INBOX_TABS)
