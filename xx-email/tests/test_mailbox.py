"""Mailbox model tests: folder dispatch, pagination keys, local-first
actions with undo, offline search projection, selection, tap routing.

Ports the UI-driving contracts from MailboxScreen/MailboxViewModel,
MailRepository (observeFolder, folderListQuery, local-first actions,
searchLocal display projection) and NotificationTapNav — over the L1
sqlite store, no network, no display.
"""

import pytest

from xx_email import db, mailbox
from xx_email.domain import MailboxFolder


@pytest.fixture
def conn():
    handle = db.connect(":memory:")
    db.migrate(handle)
    yield handle
    handle.close()


ACCOUNT = "user@dev.xxemail"


def make_thread(tid, **overrides):
    fields = {"account_email": ACCOUNT, "id": tid, "date": 1000,
              "subject": "Sub " + tid,
              "from_address": "Jane Doe <jane@example.com>",
              "from_name": "Jane Doe", "labels_csv": "INBOX",
              "in_inbox": True}
    fields.update(overrides)
    return db.Thread(**fields)


def make_message(mid, tid, **overrides):
    fields = {"account_email": ACCOUNT, "id": mid, "thread_id": tid,
              "date": 1000, "subject": "Sub",
              "from_address": "jane@example.com", "to_csv": "me@x",
              "snippet": "snip", "labels_csv": "INBOX"}
    fields.update(overrides)
    return db.Message(**fields)


def test_inbox_tabs_dispatch_to_categories(conn):
    db.upsert_threads(conn, [
        make_thread("t1", categories="CATEGORY_SOCIAL"),
        make_thread("t2", categories=""),
    ])
    social = mailbox.threads_for_folder(conn, ACCOUNT,
                                        MailboxFolder.SOCIAL)
    assert [t.id for t in social] == ["t1"]
    primary = mailbox.threads_for_folder(conn, ACCOUNT,
                                         MailboxFolder.PRIMARY)
    assert [t.id for t in primary] == ["t2"]


def test_starred_excludes_trash(conn):
    db.upsert_threads(conn, [
        make_thread("t1", starred=True),
        make_thread("t2", starred=True, labels_csv="STARRED,TRASH"),
    ])
    assert [t.id for t in mailbox.threads_for_folder(
        conn, ACCOUNT, MailboxFolder.STARRED)] == ["t1"]


def test_snoozed_and_all_mail_and_labeled_folders(conn):
    db.upsert_threads(conn, [
        make_thread("t1", snoozed_until=999),
        make_thread("t2", labels_csv="INBOX,SENT"),
    ])
    assert [t.id for t in mailbox.threads_for_folder(
        conn, ACCOUNT, MailboxFolder.SNOOZED)] == ["t1"]
    assert {t.id for t in mailbox.threads_for_folder(
        conn, ACCOUNT, MailboxFolder.ALL_MAIL)} == {"t1", "t2"}
    assert [t.id for t in mailbox.threads_for_folder(
        conn, ACCOUNT, MailboxFolder.SENT)] == ["t2"]
    assert mailbox.threads_for_folder(conn, ACCOUNT,
                                      MailboxFolder.OUTBOX) == []


def test_label_csv_never_matches_substring(conn):
    db.upsert_threads(conn, [
        make_thread("t1", labels_csv="INBOX,CONSENT"),
    ])
    assert mailbox.threads_for_folder(conn, ACCOUNT,
                                      MailboxFolder.SENT) == []


def test_folder_page_keys_share_one_inbox_cursor():
    assert mailbox.folder_page_key(MailboxFolder.PRIMARY) == "INBOX"
    assert mailbox.folder_page_key(MailboxFolder.FORUMS) == "INBOX"
    assert mailbox.folder_page_key(MailboxFolder.SENT) == "SENT"
    assert mailbox.folder_page_key(MailboxFolder.SPAM) == "SPAM"
    assert mailbox.folder_page_key(MailboxFolder.ALL_MAIL) == "ALL_MAIL"
    assert mailbox.folder_page_key(MailboxFolder.STARRED) is None
    assert mailbox.folder_page_key(MailboxFolder.SNOOZED) is None
    assert mailbox.folder_page_key(MailboxFolder.OUTBOX) is None


def test_folder_list_query_sets_spam_trash_flag_only_there():
    labels, flag = mailbox.folder_list_query(MailboxFolder.SOCIAL)
    assert labels == ["INBOX", "CATEGORY_SOCIAL"]
    assert flag is False
    labels, flag = mailbox.folder_list_query(MailboxFolder.TRASH)
    assert labels == ["TRASH"]
    assert flag is True
    labels, flag = mailbox.folder_list_query(MailboxFolder.SPAM)
    assert flag is True
    labels, flag = mailbox.folder_list_query(MailboxFolder.SENT)
    assert labels == ["SENT"]
    assert flag is False
    labels, flag = mailbox.folder_list_query(MailboxFolder.ALL_MAIL)
    assert labels is None
    labels, flag = mailbox.folder_list_query(MailboxFolder.STARRED)
    assert labels is None


def test_archive_moves_and_undo_restores(conn):
    db.upsert_threads(conn, [make_thread("t1")])
    undo = mailbox.apply_local_action(conn, ACCOUNT, "ARCHIVE", ["t1"])
    assert undo.message == "Archived"
    assert undo.server_ops == [("modify", "t1", [], ["INBOX"])]
    assert db.get_thread(conn, ACCOUNT, "t1").in_inbox is False
    undo.revert(conn)
    row = db.get_thread(conn, ACCOUNT, "t1")
    assert row.in_inbox is True
    assert "INBOX" in row.labels_csv


def test_trash_restores_prior_inbox_membership(conn):
    db.upsert_threads(conn, [make_thread("t1", in_inbox=False,
                                         labels_csv="SENT")])
    undo = mailbox.apply_local_action(conn, ACCOUNT, "DELETE", ["t1"])
    assert "TRASH" in db.get_thread(conn, ACCOUNT, "t1").labels_csv
    undo.revert(conn)
    row = db.get_thread(conn, ACCOUNT, "t1")
    assert row.in_inbox is False
    assert row.labels_csv == "SENT"


def test_star_toggles_each_row_from_own_state(conn):
    db.upsert_threads(conn, [make_thread("t1", starred=True),
                             make_thread("t2", starred=False)])
    mailbox.apply_local_action(conn, ACCOUNT, "STAR", ["t1", "t2"])
    assert db.get_thread(conn, ACCOUNT, "t1").starred is False
    assert db.get_thread(conn, ACCOUNT, "t2").starred is True


def test_mark_read_and_unread(conn):
    db.upsert_threads(conn, [make_thread("t1", unread_count=2,
                                         labels_csv="INBOX,UNREAD")])
    mailbox.apply_local_action(conn, ACCOUNT, "MARK_READ", ["t1"])
    row = db.get_thread(conn, ACCOUNT, "t1")
    assert row.unread_count == 0
    assert "UNREAD" not in row.labels_csv
    mailbox.apply_local_action(conn, ACCOUNT, "MARK_UNREAD", ["t1"])
    row = db.get_thread(conn, ACCOUNT, "t1")
    assert row.unread_count >= 1
    assert "UNREAD" in row.labels_csv


def test_report_spam_leaves_inbox(conn):
    db.upsert_threads(conn, [make_thread("t1")])
    undo = mailbox.apply_local_action(conn, ACCOUNT, "REPORT_SPAM",
                                       ["t1"])
    row = db.get_thread(conn, ACCOUNT, "t1")
    assert row.in_inbox is False
    assert "SPAM" in row.labels_csv
    undo.revert(conn)
    assert db.get_thread(conn, ACCOUNT, "t1").in_inbox is True


def test_apply_label_add_and_remove(conn):
    db.upsert_threads(conn, [make_thread("t1")])
    mailbox.apply_local_action(conn, ACCOUNT, "APPLY_LABEL", ["t1"],
                                label_id="L1", add_label=True)
    assert "L1" in db.get_thread(conn, ACCOUNT, "t1").labels_csv
    mailbox.apply_local_action(conn, ACCOUNT, "APPLY_LABEL", ["t1"],
                                label_id="L1", add_label=False)
    assert "L1" not in db.get_thread(conn, ACCOUNT, "t1").labels_csv
    with pytest.raises(ValueError):
        mailbox.apply_local_action(conn, ACCOUNT, "APPLY_LABEL", ["t1"])
    with pytest.raises(ValueError):
        mailbox.apply_local_action(conn, ACCOUNT, "BOGUS", ["t1"])


def test_snooze_records_wake_and_undo_restores(conn):
    db.upsert_threads(conn, [make_thread("t1")])
    undo = mailbox.apply_local_action(conn, ACCOUNT, "SNOOZE", ["t1"],
                                      wake_at=9_999_999)
    row = db.get_thread(conn, ACCOUNT, "t1")
    assert row.in_inbox is False
    assert row.snoozed_until == 9_999_999
    assert db.get_snooze_wake(conn, ACCOUNT, "t1") is not None
    assert undo.message == "Snoozed"
    undo.revert(conn)
    row = db.get_thread(conn, ACCOUNT, "t1")
    assert row.in_inbox is True
    assert row.snoozed_until is None
    assert db.get_snooze_wake(conn, ACCOUNT, "t1") is None
    with pytest.raises(ValueError):
        mailbox.apply_local_action(conn, ACCOUNT, "SNOOZE", ["t1"])


def test_unsnooze_restores_inbox(conn):
    db.upsert_threads(conn, [make_thread("t1")])
    mailbox.apply_local_action(conn, ACCOUNT, "SNOOZE", ["t1"],
                                wake_at=9_999_999)
    mailbox.apply_local_action(conn, ACCOUNT, "UNSNOOZE", ["t1"])
    row = db.get_thread(conn, ACCOUNT, "t1")
    assert row.in_inbox is True
    assert "INBOX" in row.labels_csv


def test_search_routing_and_hints():
    assert mailbox.should_search_server("from:alice") is True
    assert mailbox.should_search_server("subject:x is:unread") is True
    assert mailbox.should_search_server("invoice") is False
    assert mailbox.should_search_server("") is False
    assert len(mailbox.OPERATOR_HINTS) == 5


def test_search_local_groups_messages_to_threads(conn):
    db.upsert_threads(conn, [make_thread("t1")])
    db.upsert_messages(conn, [
        make_message("m1", "t1", subject="Invoice March",
                     snippet="total due", date=2000),
        make_message("m2", "t1", subject="Invoice April",
                     snippet="total due", date=3000),
    ])
    rows = mailbox.search_local_threads(conn, ACCOUNT, "invoice")
    assert [t.id for t in rows] == ["t1"]
    assert rows[0].message_count == 2
    assert rows[0].subject == "Invoice April"
    assert rows[0].in_inbox is True
    assert mailbox.search_local_threads(conn, ACCOUNT, "zzz-no-hit") == []
    assert mailbox.search_local_threads(conn, ACCOUNT, "AND") == []
    assert mailbox.search_local_threads(conn, ACCOUNT, "AND/OR") == []


def test_search_local_never_matches_bodies(conn):
    db.upsert_threads(conn, [make_thread("t1")])
    db.upsert_messages(conn, [
        make_message("m1", "t1", subject="Hello", snippet="world"),
    ])
    conn.execute("UPDATE messages SET bodyPlain = 'supercalifragilistic' "
                 "WHERE id = 'm1'")
    assert mailbox.search_local_threads(conn, ACCOUNT,
                                        "supercalifragilistic") == []


def test_time_ago_labels():
    now = 1_700_000_000_000
    assert mailbox.time_ago(now, 0) == ""
    assert mailbox.time_ago(now, now - 30_000) == "now"
    assert mailbox.time_ago(now, now - 5 * 60_000) == "5m"
    assert mailbox.time_ago(now, now - 3 * 3_600_000) == "3h"
    assert mailbox.time_ago(now, now - 2 * 86_400_000) == "2d"


def test_selection_toggle_and_clear():
    selection = mailbox.Selection()
    assert selection.toggle("t1") is True
    assert selection.toggle("t2") is True
    assert "t1" in selection
    assert len(selection) == 2
    assert selection.toggle("t1") is False
    assert selection.ids() == ["t2"]
    selection.clear()
    assert len(selection) == 0


def test_notification_tap_routing():
    action = mailbox.notification_tap_action("mailbox/a@x", "a@x",
                                             "a@x", "t1")
    assert action == ("thread", "a@x", "t1")
    action = mailbox.notification_tap_action("mailbox/b@x", "b@x",
                                             "a@x", "t1")
    assert action == ("mailbox", "a@x", None)
    action = mailbox.notification_tap_action(None, None, "a@x", "t1")
    assert action == ("mailbox", "a@x", None)
    assert mailbox.notification_tap_action(None, None, None,
                                           None)[0] == "noop"
    assert mailbox.notification_tap_action(None, None, "a@x",
                                           None)[0] == "noop"


def test_routes_and_start_destination():
    assert mailbox.route_mailbox("a@x") == "mailbox/a@x"
    assert mailbox.route_thread("a@x", "t") == "thread/a@x/t"
    assert mailbox.route_search("a@x") == "search/a@x"
    assert mailbox.start_route([], None, None) == "setup"
    assert mailbox.start_route(["a@x"], "a@x", None) == "mailbox/a@x"
    assert mailbox.start_route(["a@x", "b@x"], "b@x",
                               None) == "mailbox/b@x"
    assert mailbox.start_route(["a@x", "b@x"], "gone@x",
                               None) == "mailbox/a@x"
    assert mailbox.start_route(["a@x", "b@x"], "a@x",
                               "b@x") == "mailbox/b@x"


def test_swipe_labels_are_text():
    assert mailbox.swipe_label("ARCHIVE") == "Archive"
    assert mailbox.swipe_label("DELETE") == "Delete"
