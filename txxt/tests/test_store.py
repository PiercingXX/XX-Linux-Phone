"""SQLite adaptations of the Android data-layer tests.

Ports app/data/InboundStoreTest.kt, OutboundStoreTest.kt and
QuarantineStoreTest.kt onto the real SQLite store (replacing the hand-rolled
fake DAOs), plus schema/migration/pragma coverage for the L1 store itself:
Room-v5 column parity, the 1->5 migration chain, WAL/busy_timeout/foreign
keys, parameterized queries, and the blocklist/starred/settings/meta tables.
"""

import sqlite3

import pytest

from txxt.conversations_core import COLLAPSED_PHOTO_PLACEHOLDER
from txxt.store import (
    BLOCKLIST_KINDS,
    SCHEMA_VERSION,
    ConversationRow,
    MessageRow,
    Store,
)

DATE = 1_700_000_000_000


def fresh_store():
    return Store.memory()


# ---- schema: Room v5 parity ----

def test_schema_version_is_six():
    # Room v5 plus Linux 5->6 delete-after-import plus 6->7 dialable address.
    assert SCHEMA_VERSION == 7
    assert fresh_store().schema_version == 7


def test_conversation_and_message_columns_match_room_v5():
    # Same column SET as Room v5 plus Linux pendingDelete plus dialableAddress;
    # order differs because SQLite ALTER TABLE appends migrated columns
    # (isMuted/isQuarantined/mutedUntilMillis land after sortOrder, exactly
    # as the 3->4 and 4->5 migrations add them, then pendingDelete last,
    # then dialableAddress via 6->7).
    store = fresh_store()
    conv_cols = store.read(
        lambda c: [r["name"] for r in c.execute("PRAGMA table_info(conversations)")])
    assert conv_cols == [
        "id", "participantAddresses", "isStarred", "isPinned", "isArchived",
        "sortOrder", "isMuted", "isQuarantined", "mutedUntilMillis",
        "dialableAddress",
    ]
    assert set(conv_cols) == {
        "id", "participantAddresses", "dialableAddress", "isStarred",
        "isPinned", "isArchived", "isMuted", "isQuarantined",
        "mutedUntilMillis", "sortOrder",
    }
    msg_cols = store.read(
        lambda c: [r["name"] for r in c.execute("PRAGMA table_info(messages)")])
    assert msg_cols == [
        "id", "conversationId", "direction", "transport", "body",
        "timestampMillis", "senderAddress", "isRead", "sent",
        "contentLocation", "mediaPath", "pendingDelete",
    ]


def test_pending_delete_flag_round_trips_and_lists():
    store = fresh_store()
    mid = store.persist_inbound_sms("+15551234567", "hello", DATE)
    assert store.get_message(mid).pending_delete is False
    assert store.pending_delete_rows() == []
    store.mark_pending_delete(mid)
    assert store.get_message(mid).pending_delete is True
    assert [row.id for row in store.pending_delete_rows()] == [mid]
    store.clear_pending_delete(mid)
    assert store.get_message(mid).pending_delete is False
    assert store.pending_delete_rows() == []


def test_linux_tables_exist_and_message_index():
    store = fresh_store()
    tables = store.read(
        lambda c: {r["name"] for r in
                   c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")})
    assert {"conversations", "messages", "blocklist", "starred", "settings", "meta"} <= tables
    index = store.read(
        lambda c: c.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'index_messages_conversationId'"
        ).fetchone()["sql"])
    assert "conversationId" in index


def test_pragmas_wal_busy_timeout_foreign_keys(tmp_path):
    store = Store(str(tmp_path / "txxt.db"))
    try:
        assert store.read(lambda c: c.execute("PRAGMA journal_mode").fetchone()[0]) == "wal"
        assert store.read(lambda c: c.execute("PRAGMA busy_timeout").fetchone()[0]) == 5000
        assert store.read(lambda c: c.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    finally:
        store.close()


def test_migration_chain_upgrades_v1_to_v5_preserving_rows(tmp_path):
    path = str(tmp_path / "txxt.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE conversations (id INTEGER NOT NULL PRIMARY KEY,"
        " participantAddresses TEXT NOT NULL, isStarred INTEGER NOT NULL DEFAULT 0,"
        " isPinned INTEGER NOT NULL DEFAULT 0, isArchived INTEGER NOT NULL DEFAULT 0,"
        " sortOrder TEXT NOT NULL DEFAULT 'PINNED_FIRST')")
    conn.execute(
        "CREATE TABLE messages (id INTEGER NOT NULL PRIMARY KEY,"
        " conversationId INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,"
        " direction TEXT NOT NULL, transport TEXT NOT NULL, body TEXT NOT NULL,"
        " timestampMillis INTEGER NOT NULL, senderAddress TEXT,"
        " isRead INTEGER NOT NULL DEFAULT 0)")
    conn.execute("INSERT INTO conversations (id, participantAddresses) VALUES (7, '15551234567')")
    conn.execute(
        "INSERT INTO messages (id, conversationId, direction, transport, body,"
        " timestampMillis, senderAddress, isRead)"
        " VALUES (9, 7, 'INCOMING', 'SMS', 'kept', 42, '+15551234567', 0)")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    store = Store(path)
    try:
        assert store.schema_version == 7
        row = store.get_conversation(7)
        assert row is not None and row.participant_addresses == "15551234567"
        assert row.dialable_address == "+15551234567"
        assert not row.is_muted and not row.is_quarantined and row.muted_until_millis == 0
        msg = store.get_message(9)
        assert msg is not None and msg.body == "kept" and msg.sent
        assert msg.content_location is None and msg.media_path is None
        assert msg.pending_delete is False
    finally:
        store.close()


def test_every_query_is_parameterized_not_interpolated():
    store = fresh_store()
    evil = "'; DROP TABLE messages; --"
    cid = store.find_or_create_conversation(evil)
    mid = store.persist_inbound_sms(evil, "x' OR '1'='1", DATE)
    assert store.get_conversation(cid) is not None
    assert store.get_message(mid).body == "x' OR '1'='1"
    assert store.get_messages_for_conversation(cid) != []


# ---- find-or-create ----

def test_existing_thread_found_without_a_second_insert():
    store = fresh_store()
    store.upsert_conversation(ConversationRow(id=42, participant_addresses="15551234567"))
    assert store.find_or_create_conversation("+15551234567") == 42
    assert store.find_or_create_conversation("+15551234567") == 42
    assert len(store.get_all_conversations()) == 1


def test_miss_creates_a_conversation_keyed_by_the_normalized_address(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    try:
        before = DATE - 5_000
        cid = store.find_or_create_conversation("+15551234567")
        assert cid >= before
        stored = store.get_conversation(cid)
        assert stored.participant_addresses == "15551234567"
        assert "\u0001" not in stored.participant_addresses
    finally:
        store.close()


def test_wall_clock_id_collision_is_bumped_past():
    store = fresh_store()
    import time

    now = int(time.time() * 1000)
    store.upsert_conversation(ConversationRow(id=now, participant_addresses="+15550001111"))
    cid = store.find_or_create_conversation("+15551234567")
    assert cid != now
    assert len(store.get_all_conversations()) == 2
    assert store.get_conversation(now).participant_addresses == "+15550001111"


# ---- inbound SMS ----

def test_persist_inbound_sms_row_shape():
    store = fresh_store()
    mid = store.persist_inbound_sms("+15551234567", "hello there", DATE)
    conv_id = store.get_all_conversations()[0].id
    stored = store.get_message(mid)
    assert stored.conversation_id == conv_id
    assert (stored.direction, stored.transport) == ("INCOMING", "SMS")
    assert stored.body == "hello there"
    assert stored.timestamp_millis == DATE
    assert stored.sender_address == "+15551234567"
    assert stored.is_read is False and stored.sent is True


def test_two_sms_from_one_sender_share_one_conversation():
    store = fresh_store()
    first = store.persist_inbound_sms("+15551234567", "one", DATE)
    second = store.persist_inbound_sms("+15551234567", "two", DATE + 1)
    assert len(store.get_all_conversations()) == 1
    assert store.get_message(first).conversation_id == store.get_message(second).conversation_id


def test_formats_converge_on_one_thread_but_rows_keep_raw_addresses():
    store = fresh_store()
    sms = store.persist_inbound_sms("+15551234567", "carrier form", DATE)
    bare = store.persist_inbound_sms("15551234567", "bare-digit form", DATE + 1)
    assert len(store.get_all_conversations()) == 1
    assert store.get_all_conversations()[0].participant_addresses == "15551234567"
    assert store.get_message(sms).conversation_id == store.get_message(bare).conversation_id
    assert store.get_message(sms).sender_address == "+15551234567"
    assert store.get_message(bare).sender_address == "15551234567"


def test_ten_digit_national_and_plus_one_share_one_conversation():
    store = fresh_store()
    store.persist_inbound_sms("555-123-4567", "national", DATE)
    store.persist_inbound_sms("+15551234567", "e164", DATE + 1)
    assert len(store.get_all_conversations()) == 1


def test_email_gateway_keys_by_trimmed_lowercase():
    store = fresh_store()
    first = store.persist_inbound_sms(" Foo@Carrier.Example.com ", "one", DATE)
    store.persist_inbound_sms("foo@carrier.example.com", "two", DATE + 1)
    assert len(store.get_all_conversations()) == 1
    assert store.get_all_conversations()[0].participant_addresses == "foo@carrier.example.com"
    assert store.get_message(first).sender_address == " Foo@Carrier.Example.com "


def test_alphanumeric_senders_land_on_separate_conversations():
    store = fresh_store()
    store.persist_inbound_sms("VERIFY", "code 1", DATE)
    store.persist_inbound_sms("AMAZON", "code 2", DATE + 1)
    assert len(store.get_all_conversations()) == 2
    assert {r.participant_addresses for r in store.get_all_conversations()} == {
        "verify", "amazon"}


def test_inbound_message_unarchives_its_conversation():
    store = fresh_store()
    cid = store.find_or_create_conversation("+15551234567")
    row = store.get_conversation(cid)
    store.set_flags(cid, row.flags().archive())
    store.persist_inbound_sms("+15551234567", "hello again", DATE)
    assert not store.get_conversation(cid).is_archived


def test_quarantined_sms_is_unread_flagged_and_not_surfaced():
    store = fresh_store()
    mid = store.persist_inbound_sms("+15559990000", "held", DATE, quarantined=True)
    conv = store.get_all_conversations()[0]
    assert conv.is_quarantined and not conv.is_archived
    stored = store.get_message(mid)
    assert stored.is_read is False and stored.body == "held"
    assert stored.sender_address == "+15559990000"


def test_delivered_sms_releases_a_previously_quarantined_thread():
    store = fresh_store()
    store.persist_inbound_sms("+15559990000", "held", DATE, quarantined=True)
    store.persist_inbound_sms("+15559990000", "released", DATE + 1)
    assert not store.get_all_conversations()[0].is_quarantined


def test_persist_inbound_mms_metadata_shape():
    store = fresh_store()
    mid = store.persist_inbound_mms_metadata(
        "+15559998888", DATE, content_location="http://mmsc.example/m/1")
    stored = store.get_message(mid)
    assert (stored.direction, stored.transport) == ("INCOMING", "MMS")
    assert stored.body == COLLAPSED_PHOTO_PLACEHOLDER == "[Photo]"
    assert stored.timestamp_millis == DATE
    assert stored.sender_address == "+15559998888"
    assert stored.is_read is False and stored.sent is True
    assert stored.content_location == "http://mmsc.example/m/1"


# ---- outbound ----

def test_persist_outgoing_sms_is_a_pending_read_row_with_null_sender():
    store = fresh_store()
    mid = store.persist_outgoing_sms("+15551234567", "quick reply", now_millis=DATE)
    stored = store.get_message(mid)
    assert stored.id == mid
    assert stored.conversation_id == store.get_all_conversations()[0].id
    assert (stored.direction, stored.transport) == ("OUTGOING", "SMS")
    assert stored.body == "quick reply" and stored.timestamp_millis == DATE
    assert stored.sender_address is None
    assert stored.is_read is True and stored.sent is False
    store.mark_sent(mid)
    assert store.get_message(mid).sent is True


def test_two_replies_share_one_normalized_conversation():
    store = fresh_store()
    first = store.persist_outgoing_sms("+15551234567", "first", now_millis=DATE)
    second = store.persist_outgoing_sms("15551234567", "second", now_millis=DATE + 1)
    assert len(store.get_all_conversations()) == 1
    assert store.get_all_conversations()[0].participant_addresses == "15551234567"
    assert first != second
    assert store.get_message(first).conversation_id == store.get_message(second).conversation_id


def test_outgoing_wall_clock_collision_is_bumped_past():
    store = fresh_store()
    import time

    now = int(time.time() * 1000)
    store.upsert_conversation(ConversationRow(id=now, participant_addresses="15551234567"))
    store.upsert_message(MessageRow(
        id=now, conversation_id=now, direction="INCOMING", transport="SMS",
        body="pre-existing", timestamp_millis=now, sender_address="+15551234567"))
    mid = store.persist_outgoing_sms("+15551234567", "reply", now_millis=DATE)
    assert mid != now
    assert store.get_message(now).body == "pre-existing"
    assert store.get_message(mid).body == "reply"


def test_sending_into_an_archived_conversation_unarchives_it():
    store = fresh_store()
    cid = store.find_or_create_conversation("+15551234567")
    store.set_flags(cid, store.get_conversation(cid).flags().archive())
    store.persist_outgoing_sms("+15551234567", "hi", now_millis=DATE)
    assert not store.get_conversation(cid).is_archived
    store.set_flags(cid, store.get_conversation(cid).flags().archive())
    store.persist_outgoing_mms("+15551234567", now_millis=DATE)
    assert not store.get_conversation(cid).is_archived


def test_persist_outgoing_mms_is_a_pending_photo_row():
    store = fresh_store()
    mid = store.persist_outgoing_mms("+15551234567", now_millis=DATE)
    stored = store.get_message(mid)
    assert stored.transport == "MMS" and stored.direction == "OUTGOING"
    assert stored.body == "[Photo]"
    assert stored.sender_address is None
    assert stored.is_read is True and stored.sent is False


def test_photo_and_text_sends_share_one_conversation_with_distinct_ids():
    store = fresh_store()
    text_id = store.persist_outgoing_sms("+15551234567", "look at this", now_millis=DATE)
    photo_id = store.persist_outgoing_mms("+15551234567", now_millis=DATE + 1)
    assert len(store.get_all_conversations()) == 1
    assert text_id != photo_id
    assert store.get_message(text_id).transport == "SMS"
    assert store.get_message(photo_id).transport == "MMS"


# ---- quarantine review ----

def test_quarantine_hold_deliver_and_delete():
    store = fresh_store()
    store.persist_inbound_sms("+15551112222", "spam?", DATE, quarantined=True)
    conv = store.get_all_conversations()[0]
    assert conv.is_quarantined
    assert store.get_all_conversations()[0].id is not None
    assert store.quarantine_sender(conv.id) == "+15551112222"
    store.quarantine_deliver(conv.id)
    assert not store.get_conversation(conv.id).is_quarantined
    assert len(store.get_messages_for_conversation(conv.id)) == 1
    store.quarantine_delete(conv.id)
    assert store.get_all_conversations() == []
    assert store.get_messages_for_conversation(conv.id) == []


# ---- message helpers ----

def test_mark_conversation_read_only_touches_incoming():
    store = fresh_store()
    store.persist_inbound_sms("+15551234567", "in", DATE)
    store.persist_outgoing_sms("+15551234567", "out", now_millis=DATE + 1)
    cid = store.get_all_conversations()[0].id
    store.mark_conversation_read(cid)
    rows = store.get_messages_for_conversation(cid)
    assert [r.timestamp_millis for r in rows] == sorted(r.timestamp_millis for r in rows)
    by_direction = {r.direction: r.is_read for r in rows}
    assert by_direction == {"INCOMING": True, "OUTGOING": True}


def test_delete_paths_and_replace_upsert():
    store = fresh_store()
    mid = store.persist_inbound_sms("+15551234567", "x", DATE)
    cid = store.get_all_conversations()[0].id
    store.delete_message(mid)
    assert store.get_message(mid) is None
    mid = store.persist_inbound_sms("+15551234567", "y", DATE)
    store.upsert_message(MessageRow(
        id=mid, conversation_id=cid, direction="INCOMING", transport="SMS",
        body="replaced", timestamp_millis=DATE, is_read=True))
    assert store.get_message(mid).body == "replaced"
    store.delete_messages_for_conversation(cid)
    assert store.get_messages_for_conversation(cid) == []
    store.delete_conversation(cid)
    assert store.get_conversation(cid) is None
    store.upsert_conversation(ConversationRow(id=1, participant_addresses="a"))
    store.unarchive(1)
    assert not store.get_conversation(1).is_archived


# ---- blocklist / starred tables ----

def test_blocklist_kinds_round_trip_and_reject_bad_kinds():
    store = fresh_store()
    assert set(BLOCKLIST_KINDS) == {"sender", "keyword", "phrase"}
    store.blocklist_add("sender", "+1 555 8888")
    store.blocklist_add("keyword", "loan")
    store.blocklist_add("phrase", "free money")
    store.blocklist_add("keyword", "   ")
    assert ("sender", "+1 555 8888") in store.blocklist_list()
    assert [v for _, v in store.blocklist_list("keyword")] == ["loan"]
    with pytest.raises(ValueError):
        store.blocklist_add("nope", "x")
    store.blocklist_remove("keyword", "loan")
    assert store.blocklist_list("keyword") == []


def test_address_block_membership_is_format_tolerant():
    store = fresh_store()
    store.blocklist_add("sender", "+15551234567")
    assert store.is_address_blocked("555-123-4567")
    assert not store.is_address_blocked("+1 555 9999")


def test_starred_round_trip_is_format_tolerant_and_unstar_clears_variants():
    store = fresh_store()
    store.star("+15551234567")
    store.star("AMAZON")
    store.star("   ")
    assert store.is_starred("555-123-4567")
    assert store.is_starred("amazon")
    assert not store.is_starred("VERIFY")
    assert set(store.starred_list()) == {"+15551234567", "AMAZON"}
    store.unstar("(555) 123-4567")
    assert not store.is_starred("+15551234567")
    assert store.is_starred("AMAZON")
    store.unstar("amazon")
    assert store.starred_list() == []


# ---- settings / meta ----

def test_settings_json_round_trip_with_defaults():
    store = fresh_store()
    assert store.get_setting("missing", default="dflt") == "dflt"
    store.set_setting("txxt_theme", "forest")
    store.set_setting("flags", {"a": [1, 2]})
    assert store.get_setting("txxt_theme") == "forest"
    assert store.get_setting("flags") == {"a": [1, 2]}


def test_meta_round_trip():
    store = fresh_store()
    assert store.get_meta("backup_version") is None
    store.set_meta("backup_version", "5")
    store.set_meta("last_reconcile_millis", "123")
    assert store.get_meta("backup_version") == "5"
    assert store.get_meta("last_reconcile_millis") == "123"


# ---- bug_txxt finding 30: blocked inbound stays out of the surfaced inbox ----

def test_blocked_style_persist_does_not_surface_archived_thread():
    store = fresh_store()
    first = store.persist_inbound_sms("+15550001111", "first", DATE)
    row = store.get_message(first)
    conv = store.get_conversation(row.conversation_id)
    store.set_flags(conv.id, conv.flags().archive())
    store.persist_inbound_sms("+15550001111", "spam", DATE + 1, surface=False)
    assert store.get_conversation(conv.id).is_archived is True


# ---- bug_txxt finding 33: crash between ALTER and version bump ----

def test_interrupted_migration_reopens_cleanly(tmp_path):
    path = str(tmp_path / "txxt.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE conversations (id INTEGER NOT NULL PRIMARY KEY,"
        " participantAddresses TEXT NOT NULL, isStarred INTEGER NOT NULL DEFAULT 0,"
        " isPinned INTEGER NOT NULL DEFAULT 0, isArchived INTEGER NOT NULL DEFAULT 0,"
        " sortOrder TEXT NOT NULL DEFAULT 'PINNED_FIRST')")
    conn.execute(
        "CREATE TABLE messages (id INTEGER NOT NULL PRIMARY KEY,"
        " conversationId INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,"
        " direction TEXT NOT NULL, transport TEXT NOT NULL, body TEXT NOT NULL,"
        " timestampMillis INTEGER NOT NULL, senderAddress TEXT,"
        " isRead INTEGER NOT NULL DEFAULT 0)")
    for table in ("blocklist", "starred", "settings", "meta"):
        conn.execute(
            f"CREATE TABLE {table} ("
            + ("kind TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (kind, value)"
               if table == "blocklist" else
               "address TEXT NOT NULL PRIMARY KEY"
               if table == "starred" else
               "key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL")
            + ")")
    # Crash after the 1->2 ALTER but before its version bump.
    conn.execute("ALTER TABLE messages ADD COLUMN sent INTEGER NOT NULL DEFAULT 1")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    store = Store(path)
    try:
        assert store.schema_version == SCHEMA_VERSION
        cols = store.read(
            lambda c: [r["name"] for r in c.execute("PRAGMA table_info(messages)")])
        assert cols.count("sent") == 1
    finally:
        store.close()
    reopened = Store(path)
    try:
        assert reopened.schema_version == SCHEMA_VERSION
    finally:
        reopened.close()


def test_failed_migration_step_leaves_version_untouched(tmp_path):
    from txxt import store as store_mod

    path = str(tmp_path / "txxt.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE conversations (id INTEGER NOT NULL PRIMARY KEY,"
        " participantAddresses TEXT NOT NULL, isStarred INTEGER NOT NULL DEFAULT 0,"
        " isPinned INTEGER NOT NULL DEFAULT 0, isArchived INTEGER NOT NULL DEFAULT 0,"
        " sortOrder TEXT NOT NULL DEFAULT 'PINNED_FIRST')")
    conn.execute(
        "CREATE TABLE messages (id INTEGER NOT NULL PRIMARY KEY,"
        " conversationId INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,"
        " direction TEXT NOT NULL, transport TEXT NOT NULL, body TEXT NOT NULL,"
        " timestampMillis INTEGER NOT NULL, senderAddress TEXT,"
        " isRead INTEGER NOT NULL DEFAULT 0)")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    real = dict(store_mod.MIGRATIONS)
    poisoned = dict(real)
    poisoned[(1, 2)] = [
        "ALTER TABLE messages ADD COLUMN sent INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE messages_missing ADD COLUMN boom_broken INTEGER NOT NULL DEFAULT 1",
    ]
    store_mod.MIGRATIONS.clear()
    store_mod.MIGRATIONS.update(poisoned)
    try:
        with pytest.raises(Exception):
            Store(path)
    finally:
        store_mod.MIGRATIONS.clear()
        store_mod.MIGRATIONS.update(real)
    check = sqlite3.connect(path)
    try:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 1
        cols = [r[1] for r in check.execute("PRAGMA table_info(messages)")]
        assert "sent" not in cols
    finally:
        check.close()
    store = Store(path)
    try:
        assert store.schema_version == SCHEMA_VERSION
    finally:
        store.close()
