"""Snooze wake processing for milestone 8 (stdlib only, no GTK).

Snoozing removes INBOX server-side immediately and records a durable
wake row (mailbox.apply_local_action SNOOZE); the background service
restores INBOX at wake time through process_due_wakes. Wake rows live
in SQLite, so they survive reboots, and every tick drains whatever is
due — a timer with Persistent=true therefore catches up missed wakes
on boot. A wake that fails stays queued and is retried until it lands:
an unwoken snooze is mail silently stuck out of the inbox.

Blocking by contract: run on a worker thread, never the GTK loop.
"""

from __future__ import annotations

from xx_email import db
from xx_email.domain import LabelCsv

UNINSTALL_WARNING = (
    "While snoozed, mail leaves the inbox in every mail client, and "
    "wake times live only on this device. Removing this account or "
    "uninstalling the app while mail is still snoozed strands it in "
    "All Mail / search until it is unsnoozed elsewhere — unsnooze "
    "everything first."
)

WAKE_ADD_LABELS = ["INBOX"]


def process_due_wakes(conn, client_for_account, now_ms: int) -> dict:
    """Restore every due wake row; failures stay queued for retry.

    client_for_account(account) returns a Gmail client with
    modify_thread. Returns {"woken": [...ids], "failed": [...ids]} and
    never raises: per-wake failures keep their row so the next tick
    retries them.
    """
    woken: list[str] = []
    failed: list[str] = []
    try:
        due = db.due_snooze_wakes(conn, int(now_ms))
    except Exception:
        return {"woken": woken, "failed": failed}
    for wake in due:
        try:
            client = client_for_account(wake.account_email)
            client.modify_thread(wake.thread_id,
                                 add=list(WAKE_ADD_LABELS))
        except Exception:
            failed.append(wake.thread_id)
            continue
        try:
            db.delete_snooze_wake(conn, wake.account_email,
                                  wake.thread_id)
            db.set_snoozed(conn, wake.account_email, wake.thread_id,
                            None)
            row = db.get_thread(conn, wake.account_email,
                                wake.thread_id)
            if row is not None:
                db.set_inbox_and_labels(
                    conn, wake.account_email, wake.thread_id, True,
                    LabelCsv.add(row.labels_csv, "INBOX"))
            woken.append(wake.thread_id)
        except Exception:
            failed.append(wake.thread_id)
    return {"woken": woken, "failed": failed}


def pending_wake_count(conn, account: str) -> int:
    """Still-snoozed threads for one account (remove-flow warning)."""
    try:
        return len(db.snooze_wakes_for_account(conn, account))
    except Exception:
        return 0
