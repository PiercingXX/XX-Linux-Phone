#!/usr/bin/env python3
"""Seed a HOME/XDG tree with realistic xx-email data through the real store.

Usage: demo_seed.py HOMEDIR [--empty]

Fills HOMEDIR with a shell config, an xx-email config and a SQLite store
holding two accounts, labeled threads across every inbox tab, a starred
thread, a snoozed thread, sent mail, a queued outbox send and message
bodies (one HTML with a remote image). Ids are Android-style millisecond
timestamps. ``--empty`` seeds no accounts so the app shows first-run setup.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

ACCOUNT = "mara.voss@example.com"
ACCOUNT2 = "dez.okafor@example.com"
THREAD_REPLY = "t-demo-thread-1"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _write(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def seed(home: str, empty: bool = False) -> dict:
    from xx_email import config as config_mod
    from xx_email import db as db_mod
    from xx_email import outbox as outbox_mod
    from xx_email import setup as setup_mod

    os.environ["HOME"] = home
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    os.environ["XDG_DATA_HOME"] = os.path.join(home, ".local", "share")
    os.environ["XDG_CACHE_HOME"] = os.path.join(home, ".cache")
    os.environ["XDG_STATE_HOME"] = os.path.join(home, ".local", "state")
    os.environ["XDG_RUNTIME_DIR"] = os.path.join(home, "run")

    dirs = setup_mod.ensure_app_dirs()
    now = _now_ms()

    shell_config = os.path.join(home, ".config", "xx-wm", "config.json")
    if not os.path.exists(shell_config):
        _write(shell_config, {"theme": "amoled"})

    stored = config_mod.load()
    stored.client_id = ("1234567890-abcdefgh.apps.googleusercontent.com")
    stored.last_account = "" if empty else ACCOUNT
    config_mod.save(stored)

    conn = db_mod.open_db(dirs["data"] / db_mod.DB_FILE_NAME)
    try:
        if empty:
            return {"account": "", "thread": ""}
        db_mod.upsert_account(conn, db_mod.Account(
            email=ACCOUNT, display_name="Mara Voss",
            history_id="18446744073709551615", last_sync_at=now))
        db_mod.upsert_account(conn, db_mod.Account(
            email=ACCOUNT2, display_name="Dez Okafor",
            history_id="18446744073709551614",
            last_sync_at=now - 3_600_000))
        db_mod.upsert_labels(conn, [
            db_mod.Label(account_email=ACCOUNT, id="INBOX",
                         name="INBOX", type="system"),
            db_mod.Label(account_email=ACCOUNT, id="STARRED",
                         name="STARRED", type="system"),
            db_mod.Label(account_email=ACCOUNT, id="l-receipts",
                         name="Receipts", type="user"),
            db_mod.Label(account_email=ACCOUNT, id="l-travel",
                         name="Travel", type="user"),
        ])
        people = [
            ("t-demo-thread-1", "Mara Voss", "mara.voss@example.com",
             "Dinner on the 42?", "on the 42 now. five min out",
             "CATEGORY_PERSONAL", 1, False, False, 0),
            ("t-demo-thread-2", "Ridgeline Dental",
             "reminders@ridgelinedental.example",
             "Cleaning Tue 10:30", "Cleaning Tue 10:30. Reply C to confirm",
             "CATEGORY_UPDATES", 1, False, True, 5),
            ("t-demo-thread-3", "Mom", "mom@example.com",
             "Re: Sunday", "bring the case, front entrance",
             "CATEGORY_PERSONAL", 0, True, False, 40),
            ("t-demo-thread-4", "Dez Okafor", "dez@example.com",
             "Board deck", "sent the photos, see attached",
             "CATEGORY_SOCIAL", 0, False, True, 180),
            ("t-demo-thread-5", "Juniper Hale", "juniper@example.com",
             "Invoice #2413", "subject:invoice is:unread",
             "CATEGORY_PROMOTIONS", 1, False, False, 400),
            ("t-demo-thread-6", "CI Bot", "ci@example.com",
             "Build is green", "nightly build passed on main",
             "CATEGORY_FORUMS", 0, False, False, 900),
            ("t-demo-thread-7", "Ada Lovelace", "ada@example.com",
             "Tacos at noon?", "still on for tonight?",
             "CATEGORY_PERSONAL", 1, False, False, 20),
        ]
        threads = []
        messages = []
        for index, (tid, name, addr, subject, snippet, cat,
                    unread, starred, attached, ago_min) in enumerate(people):
            date = now - ago_min * 60_000
            labels = "INBOX" + (",STARRED" if starred else "")
            if tid == "t-demo-thread-5":
                labels += ",l-receipts"
            threads.append(db_mod.Thread(
                account_email=ACCOUNT, id=tid, snippet=snippet,
                subject=subject,
                from_address=f"{name} <{addr}>", from_name=name,
                date=date, message_count=2 if index < 2 else 1,
                unread_count=unread, has_attachments=attached,
                starred=starred, in_inbox=True, categories=cat,
                labels_csv=labels))
            messages.append(db_mod.Message(
                account_email=ACCOUNT, id=f"m-{tid}-1",
                thread_id=tid, subject=subject,
                from_address=f"{name} <{addr}>", to_csv=ACCOUNT,
                cc_csv="", date=date - 300_000, snippet=snippet,
                read=not unread, starred=starred,
                has_attachments=attached, labels_csv=labels,
                message_id_header=f"<{tid}@example.com>",
                body_plain=f"{snippet}\n\nSee you soon,\n{name}",
                body_fetched=True))
        messages.append(db_mod.Message(
            account_email=ACCOUNT, id="m-t-demo-thread-1-2",
            thread_id="t-demo-thread-1", subject="Re: Dinner on the 42?",
            from_address=f"Mara Voss <{ACCOUNT}>",
            to_csv="mara.voss@example.com",
            cc_csv="", date=now - 60_000, snippet="yeah. 9 works",
            read=True, starred=False, has_attachments=False,
            labels_csv="SENT",
            message_id_header="<reply-1@example.com>",
            body_plain="yeah. 9 works — front entrance?",
            body_fetched=True))
        html_thread = db_mod.Thread(
            account_email=ACCOUNT, id="t-demo-html-1",
            snippet="Your receipt", subject="Your order receipt",
            from_address="Shop <orders@shop.example>",
            from_name="Shop", date=now - 60 * 60_000,
            message_count=1, unread_count=0, has_attachments=False,
            starred=False, in_inbox=True, categories="CATEGORY_UPDATES",
            labels_csv="INBOX,l-receipts")
        threads.append(html_thread)
        messages.append(db_mod.Message(
            account_email=ACCOUNT, id="m-t-demo-html-1-1",
            thread_id="t-demo-html-1", subject="Your order receipt",
            from_address="Shop <orders@shop.example>",
            to_csv=ACCOUNT, cc_csv="", date=now - 60 * 60_000,
            snippet="Your receipt", read=True, starred=False,
            has_attachments=False, labels_csv="INBOX",
            message_id_header="<html-1@example.com>",
            body_html=("<html><body><p>Thanks for your order!</p>"
                       '<img src="https://tracker.example/pixel.png">'
                       "</body></html>"),
            body_plain="Thanks for your order!", body_fetched=True))
        snoozed = db_mod.Thread(
            account_email=ACCOUNT, id="t-demo-snoozed-1",
            snippet="Flight change", subject="Your flight moved",
            from_address="Airline <noreply@airline.example>",
            from_name="Airline", date=now - 200 * 60_000,
            message_count=1, unread_count=0, has_attachments=False,
            starred=False, in_inbox=True, categories="CATEGORY_UPDATES",
            labels_csv="INBOX", snoozed_until=now + 3_600_000)
        threads.append(snoozed)
        sent = db_mod.Thread(
            account_email=ACCOUNT, id="t-demo-sent-1",
            snippet="sent the photos", subject="Re: Board deck",
            from_address=f"{ACCOUNT}", from_name="",
            date=now - 500 * 60_000, message_count=1,
            unread_count=0, has_attachments=True, starred=False,
            in_inbox=False, categories="", labels_csv="SENT")
        threads.append(sent)
        db_mod.upsert_threads(conn, threads)
        db_mod.upsert_messages(conn, messages)
        outbox_mod.enqueue_fields(
            conn, dirs["data"], ACCOUNT, "juniper@example.com", "",
            "", "Board deck", "See attached",
            scheduled_at=now + 3_600_000, undo_seconds=10, now_ms=now)
    finally:
        conn.close()
    return {"account": ACCOUNT, "thread": THREAD_REPLY}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    info = seed(argv[0], empty="--empty" in argv[1:])
    print(f"seeded {argv[0]} account={info['account']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
