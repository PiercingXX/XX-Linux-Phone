"""Store tests: fresh v3 schema, DAO parity, and the v1->v3 migration walk.

Ports MigrationTest (androidTest data/db) plus DAO-query golden cases
from Daos.kt: exact-token instr() label matching, inbox-category
queries, starred-excludes-trash, outbox two-sided CAS, folder pages,
snooze wakes, and FTS5 local search.
"""

import sqlite3

import pytest

from xx_email import db

ACCOUNT = "user@dev.xxemail"


@pytest.fixture
def conn():
    handle = db.connect(":memory:")
    db.migrate(handle)
    yield handle
    handle.close()


def make_thread(tid, **overrides):
    fields = {"account_email": ACCOUNT, "id": tid, "date": 1000}
    fields.update(overrides)
    return db.Thread(**fields)


def make_message(mid, tid, **overrides):
    fields = {"account_email": ACCOUNT, "id": mid, "thread_id": tid,
              "date": 1000}
    fields.update(overrides)
    return db.Message(**fields)


def test_fresh_schema_is_v3_with_all_tables(conn):
    assert db.user_version(conn) == 3
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"accounts", "labels", "threads", "messages", "messages_fts",
            "outbox", "snooze_wakes", "folder_pages"} <= names


def test_open_db_creates_and_migrates_file(tmp_path):
    path = tmp_path / "xxemail.db"
    handle = db.open_db(path)
    try:
        assert db.user_version(handle) == 3
    finally:
        handle.close()
    assert db.open_db(path).close() is None


def test_account_round_trip_and_history_id_stays_opaque(conn):
    huge = "18446744073709551615"
    db.upsert_account(conn, db.Account(email=ACCOUNT, display_name="Dev",
                                       history_id=huge,
                                       last_sync_at=1700000000000))
    loaded = db.get_account(conn, ACCOUNT)
    assert loaded is not None
    assert loaded.history_id == huge
    assert isinstance(loaded.history_id, str)
    assert [a.email for a in db.all_accounts(conn)] == [ACCOUNT]
    db.update_sync_point(conn, ACCOUNT, "999", 1700000001000)
    assert db.get_account(conn, ACCOUNT).history_id == "999"
    assert db.get_account(conn, "nobody@x") is None


def test_delete_account_clears_every_table(conn):
    db.upsert_account(conn, db.Account(email=ACCOUNT))
    db.upsert_labels(conn, [db.Label(ACCOUNT, "INBOX", "Inbox")])
    db.upsert_threads(conn, [make_thread("t")])
    db.upsert_messages(conn, [make_message("m", "t")])
    db.insert_outbox(conn, db.OutboxEntry(account_email=ACCOUNT,
                                          subject="s", target_at=1,
                                          created_at=1))
    db.upsert_snooze_wake(conn, db.SnoozeWake(ACCOUNT, "t", 2, 1))
    db.upsert_folder_page(conn, db.FolderPage(ACCOUNT, "INBOX", "tok"))
    db.delete_account(conn, ACCOUNT)
    assert db.all_accounts(conn) == []
    assert db.labels_for_account(conn, ACCOUNT) == []
    assert db.all_mail(conn, ACCOUNT) == []
    assert db.messages_for_thread(conn, ACCOUNT, "t") == []
    assert db.outbox_for_account(conn, ACCOUNT) == []
    assert db.snooze_wakes_for_account(conn, ACCOUNT) == []
    assert db.get_folder_page(conn, ACCOUNT, "INBOX") is None


def test_labels_ordered_and_replaced(conn):
    db.upsert_labels(conn, [db.Label(ACCOUNT, "b", "Zebra"),
                            db.Label(ACCOUNT, "a", "Alpha")])
    assert [label.name for label in db.labels_for_account(conn, ACCOUNT)] == \
        ["Alpha", "Zebra"]
    db.delete_labels_for_account(conn, ACCOUNT)
    assert db.labels_for_account(conn, ACCOUNT) == []


def test_inbox_category_primary_vs_social(conn):
    db.upsert_threads(conn, [
        make_thread("t-primary", categories=""),
        make_thread("t-social", categories="CATEGORY_SOCIAL", date=2000),
    ])
    primary = db.inbox_category(conn, ACCOUNT, "", include_empty=True)
    assert [t.id for t in primary] == ["t-primary"]
    social = db.inbox_category(conn, ACCOUNT, "CATEGORY_SOCIAL",
                               include_empty=False)
    assert [t.id for t in social] == ["t-social"]
    assert db.count_inbox_category(conn, ACCOUNT, "CATEGORY_SOCIAL",
                                   include_empty=False) == 1


def test_label_matching_is_exact_token(conn):
    db.upsert_threads(conn, [
        make_thread("t-consent", labels_csv="CONSENT"),
        make_thread("t-sent", labels_csv="SENT", date=2000),
    ])
    assert [t.id for t in db.threads_with_label(conn, ACCOUNT, "SENT")] == \
        ["t-sent"]
    assert db.count_with_label(conn, ACCOUNT, "SENT") == 1
    assert db.count_with_label(conn, ACCOUNT, "CONSENT") == 1


def test_starred_excludes_trash(conn):
    db.upsert_threads(conn, [
        make_thread("t-star", starred=True, labels_csv="STARRED"),
        make_thread("t-star-trash", starred=True,
                    labels_csv="STARRED,TRASH"),
    ])
    assert [t.id for t in db.starred_threads(conn, ACCOUNT)] == ["t-star"]


def test_snoozed_threads_ordered_and_hidden_from_inbox(conn):
    db.upsert_threads(conn, [
        make_thread("t-late", snoozed_until=3000, in_inbox=False),
        make_thread("t-early", snoozed_until=1000, in_inbox=False),
        make_thread("t-plain", date=5000),
    ])
    assert [t.id for t in db.snoozed_threads(conn, ACCOUNT)] == \
        ["t-early", "t-late"]
    assert [t.id for t in db.inbox_category(conn, ACCOUNT, "",
                                            include_empty=True)] == \
        ["t-plain"]
    db.set_snoozed(conn, ACCOUNT, "t-early", None)
    assert [t.id for t in db.snoozed_threads(conn, ACCOUNT)] == ["t-late"]


def test_set_inbox_and_labels_moves_thread(conn):
    db.upsert_threads(conn, [make_thread("t", labels_csv="INBOX")])
    db.set_inbox_and_labels(conn, ACCOUNT, "t", False, "SENT")
    loaded = db.get_thread(conn, ACCOUNT, "t")
    assert loaded is not None
    assert loaded.in_inbox is False
    assert loaded.labels_csv == "SENT"


def test_messages_ordered_and_bodies_updated(conn):
    db.upsert_messages(conn, [
        make_message("m2", "t", date=2000),
        make_message("m1", "t", date=1000),
    ])
    assert [m.id for m in db.messages_for_thread(conn, ACCOUNT, "t")] == \
        ["m1", "m2"]
    db.update_message_bodies(conn, ACCOUNT, "m1", "<p>hi</p>", "hi", '[{"n":1}]')
    loaded = db.get_message(conn, ACCOUNT, "m1")
    assert loaded is not None
    assert (loaded.body_html, loaded.body_plain,
            loaded.attachments_json) == ("<p>hi</p>", "hi", '[{"n":1}]')
    assert loaded.body_fetched is True


def test_search_local_matches_indexed_columns_only(conn):
    db.upsert_messages(conn, [
        make_message("m1", "t1", subject="Quarterly report",
                     snippet="numbers inside", from_address="boss@work.com",
                     to_csv="user@dev.xxemail"),
        make_message("m2", "t2", subject="Lunch",
                     snippet="sandwiches", from_address="pal@example.com"),
    ])
    assert [m.id for m in db.search_local(conn, ACCOUNT, "quarterly")] == \
        ["m1"]
    assert [m.id for m in db.search_local(conn, ACCOUNT, "boss@work.com")] == \
        ["m1"]
    assert db.search_local(conn, ACCOUNT, "   ") == []
    assert [m.id for m in db.search_local(conn, ACCOUNT, "sandwiches")] == \
        ["m2"]


def test_search_local_never_throws_on_operators(conn):
    db.upsert_messages(conn, [make_message("m1", "t1", subject="hello")])
    assert db.search_local(conn, ACCOUNT, "AND OR NOT") == []
    assert [m.id for m in db.search_local(conn, ACCOUNT, "(hello)")] == ["m1"]


def test_search_local_scoped_to_account(conn):
    db.upsert_messages(conn, [make_message("m1", "t1", subject="hello")])
    assert db.search_local(conn, "other@example.com", "hello") == []


def test_outbox_insert_and_failed_first_ordering(conn):
    first = db.insert_outbox(conn, db.OutboxEntry(account_email=ACCOUNT,
                                                 kind="SEND", subject="a",
                                                 target_at=30, created_at=30))
    second = db.insert_outbox(conn, db.OutboxEntry(account_email=ACCOUNT,
                                                  kind="SEND", subject="b",
                                                  target_at=10, created_at=10))
    assert db.get_outbox(conn, first).subject == "a"
    queued = db.queued_outbox(conn)
    assert [e.id for e in queued] == [second, first]
    db.set_outbox_state(conn, first, "FAILED", error="boom")
    assert [e.id for e in db.open_outbox_for_account(conn, ACCOUNT)] == \
        [first, second]
    assert [e.id for e in db.failed_outbox_for_account(conn, ACCOUNT)] == \
        [first]
    assert db.get_outbox(conn, first).error == "boom"


def test_outbox_two_sided_cas_undo_wins(conn):
    entry = db.insert_outbox(conn, db.OutboxEntry(account_email=ACCOUNT,
                                                 subject="s", target_at=1,
                                                 created_at=1))
    assert db.cancel_outbox_if_queued(conn, entry) is True
    assert db.claim_outbox_if_queued(conn, entry) is False
    assert db.get_outbox(conn, entry).state == "CANCELLED"


def test_outbox_two_sided_cas_claim_wins(conn):
    entry = db.insert_outbox(conn, db.OutboxEntry(account_email=ACCOUNT,
                                                 subject="s", target_at=1,
                                                 created_at=1))
    assert db.claim_outbox_if_queued(conn, entry) is True
    assert db.cancel_outbox_if_queued(conn, entry) is False
    assert db.get_outbox(conn, entry).state == "SENDING"


def test_outbox_attempts_payload_and_legacy_clear(conn):
    entry = db.insert_outbox(conn, db.OutboxEntry(account_email=ACCOUNT,
                                                 subject="s", target_at=1,
                                                 created_at=1,
                                                 rfc822_base64="aGVsbG8="))
    db.increment_outbox_attempts(conn, entry)
    db.set_outbox_payload(conn, entry, "outbox/1.eml", 2048)
    db.clear_legacy_payload(conn, entry)
    loaded = db.get_outbox(conn, entry)
    assert loaded is not None
    assert (loaded.attempts, loaded.path, loaded.size,
            loaded.rfc822_base64) == (1, "outbox/1.eml", 2048, None)
    assert db.queued_outbox_ids(conn, ACCOUNT) == [entry]
    db.delete_outbox(conn, entry)
    assert db.get_outbox(conn, entry) is None


def test_folder_pages_upsert_and_delete(conn):
    assert db.get_folder_page(conn, ACCOUNT, "INBOX") is None
    db.upsert_folder_page(conn, db.FolderPage(ACCOUNT, "INBOX", "tok-42"))
    assert db.get_folder_page(conn, ACCOUNT, "INBOX").next_page_token == \
        "tok-42"
    db.upsert_folder_page(conn, db.FolderPage(ACCOUNT, "INBOX", None))
    assert db.get_folder_page(conn, ACCOUNT, "INBOX").next_page_token is None
    db.delete_folder_pages_for_account(conn, ACCOUNT)
    assert db.get_folder_page(conn, ACCOUNT, "INBOX") is None


def test_snooze_wakes_due_and_delete(conn):
    db.upsert_snooze_wake(conn, db.SnoozeWake(ACCOUNT, "t1", 1000, 100))
    db.upsert_snooze_wake(conn, db.SnoozeWake(ACCOUNT, "t2", 3000, 100))
    assert [w.thread_id for w in db.due_snooze_wakes(conn, 1500)] == ["t1"]
    assert [w.thread_id for w in
            db.snooze_wakes_for_account(conn, ACCOUNT)] == ["t1", "t2"]
    db.delete_snooze_wake(conn, ACCOUNT, "t1")
    assert db.get_snooze_wake(conn, ACCOUNT, "t1") is None
    db.delete_snooze_wakes_for_account(conn, ACCOUNT)
    assert db.snooze_wakes_for_account(conn, ACCOUNT) == []


QUEUED_B64 = ("RnJvbTogc2VuZGVyQGV4YW1wbGUuY29tDQpUbzogdXNlckBkZXYueHhlbWFpbA0K"
              "U3ViamVjdDogUXVhcnRlcmx5IHJlcG9ydA0KDQpRdWV1ZWQgcGF5bG9hZA0K")
SNOOZED_UNTIL = 1893456000000


def _seed_v1(handle):
    handle.execute(
        "INSERT INTO accounts (email, displayName, historyId, lastSyncAt)"
        " VALUES (?, ?, ?, ?)",
        (ACCOUNT, "Dev User", 9223372036854775807, 1700000000000))
    handle.execute(
        "INSERT INTO outbox (accountEmail, kind, threadId, rfc822Base64,"
        " subject, targetAt, state, attempts, error, createdAt)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ACCOUNT, "SEND", "thread-queued", QUEUED_B64, "Re: quarterly report",
         1700000036000, "QUEUED", 0, None, 1700000018000))
    handle.execute(
        "INSERT INTO threads (accountEmail, id, snippet, subject, fromAddress,"
        " fromName, date, messageCount, unreadCount, hasAttachments, starred,"
        " inInbox, categories, labelsCsv, snoozedUntil)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ACCOUNT, "t-snoozed", "zephyrquartz draft notes", "Snoozed thread",
         "sender@example.com", "Sender", 1699999999000, 2, 1, 1, 0, 0, "",
         "INBOX", SNOOZED_UNTIL))
    handle.execute(
        "INSERT INTO messages (accountEmail, id, threadId, subject, fromAddress,"
        " toCsv, ccCsv, date, snippet, read, starred, hasAttachments, labelsCsv,"
        " messageIdHeader, bodyHtml, bodyPlain, bodyFetched)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ACCOUNT, "m-1", "t-snoozed", "Snoozed thread", "sender@example.com",
         ACCOUNT, "", 1699999999000, "zephyrquartz draft notes", 0, 0, 1,
         "INBOX", "<m-1@example.com>", "<p>body</p>", "body", 1))
    handle.commit()


def _v1_conn():
    handle = sqlite3.connect(":memory:")
    handle.row_factory = sqlite3.Row
    db.create_v1_schema(handle)
    assert db.user_version(handle) == 1
    return handle


def test_migrate_1_to_2_history_text_and_rows_survive():
    handle = _v1_conn()
    _seed_v1(handle)
    handle.execute("PRAGMA user_version=1")
    assert db.migrate(handle) == 3
    kind = handle.execute(
        "SELECT typeof(historyId), historyId FROM accounts WHERE email = ?",
        (ACCOUNT,)).fetchone()
    assert tuple(kind) == ("text", "9223372036854775807")
    row = handle.execute(
        "SELECT kind, threadId, rfc822Base64, subject, state, attempts, path,"
        " size FROM outbox WHERE state = 'QUEUED'").fetchone()
    assert tuple(row) == ("SEND", "thread-queued", QUEUED_B64,
                          "Re: quarterly report", "QUEUED", 0, None, 0)
    snoozed = handle.execute(
        "SELECT snoozedUntil, subject, labelsCsv FROM threads"
        " WHERE id = 't-snoozed'").fetchone()
    assert tuple(snoozed) == (SNOOZED_UNTIL, "Snoozed thread", "INBOX")
    tables = {r[0] for r in handle.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"snooze_wakes", "folder_pages"} <= tables
    handle.close()


def test_migrate_1_to_3_v2_rows_and_fts_survive():
    handle = _v1_conn()
    _seed_v1(handle)
    db._migrate_1_to_2(handle)
    handle.execute(
        "INSERT INTO snooze_wakes (accountEmail, threadId, targetAt, createdAt)"
        " VALUES (?, ?, ?, ?)",
        (ACCOUNT, "t-snoozed", 1893456001000, 1700000020000))
    handle.execute(
        "INSERT INTO outbox (accountEmail, kind, path, size, subject, targetAt,"
        " state, attempts, createdAt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ACCOUNT, "SCHEDULED_SEND", "outbox/99.eml", 2048,
         "v2 file-backed send", 1700003600000, "QUEUED", 0, 1700000021000))
    handle.commit()
    db._migrate_2_to_3(handle)
    handle.execute("PRAGMA user_version=3")
    columns = {r[1] for r in handle.execute("PRAGMA table_info(messages)")}
    assert {"attachmentsJson", "bodyHtml", "labelsCsv"} <= columns
    handle.execute(
        "INSERT INTO folder_pages (accountEmail, folderKey, nextPageToken)"
        " VALUES (?, ?, ?)", (ACCOUNT, "INBOX", "tok-42"))
    assert handle.execute(
        "SELECT nextPageToken FROM folder_pages"
        " WHERE accountEmail = ? AND folderKey = ?",
        (ACCOUNT, "INBOX")).fetchone()[0] == "tok-42"
    body = handle.execute(
        "SELECT bodyHtml, attachmentsJson, messageIdHeader FROM messages"
        " WHERE id = 'm-1'").fetchone()
    assert tuple(body) == ("<p>body</p>", None, "<m-1@example.com>")
    assert handle.execute(
        "SELECT count(*) FROM messages_fts"
        " WHERE messages_fts MATCH 'zephyrquartz'").fetchone()[0] == 1
    assert handle.execute(
        "SELECT targetAt FROM snooze_wakes WHERE threadId = 't-snoozed'"
        ).fetchone()[0] == 1893456001000
    backed = handle.execute(
        "SELECT path, size, rfc822Base64 FROM outbox"
        " WHERE kind = 'SCHEDULED_SEND'").fetchone()
    assert tuple(backed) == ("outbox/99.eml", 2048, None)
    handle.close()
