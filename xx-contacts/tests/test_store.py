"""Store tests: schema + migrations, CRUD + revision/tombstones (L1)."""

import os
import sqlite3
import stat

import pytest

from xx_contacts.store import (
    SCHEMA_VERSION,
    ContactData,
    ContactStore,
    EmailEntry,
    NotFoundError,
    PhoneEntry,
    StoreError,
)

EXPECTED_TABLES = {
    "meta", "contacts", "phones", "emails", "addresses", "urls", "ims",
    "relations", "dates", "groups", "group_members", "extra_vcard",
    "tombstones",
}


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "contacts.db")


@pytest.fixture
def store(db_path):
    handle = ContactStore(db_path)
    yield handle
    handle.close()


def _contact(name="Ada Lovelace"):
    return ContactData(display_name=name,
                       phones=[PhoneEntry(number_raw="+1 555 0100",
                                           number_e164="+15550100",
                                           number_digits="15550100")])


def test_fresh_schema_version_and_revision(store):
    assert store.get_schema_version() == SCHEMA_VERSION
    assert store.get_revision() == 0


def test_expected_tables_exist(store, db_path):
    names = {r[0] for r in sqlite3.connect(db_path).execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert EXPECTED_TABLES <= names


def test_wal_mode_and_busy_timeout(store, db_path):
    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    conn.close()


def test_db_file_mode_0600(db_path):
    handle = ContactStore(db_path)
    handle.close()
    assert stat.S_IMODE(os.stat(db_path).st_mode) == 0o600


def test_migrate_is_idempotent(store):
    assert store.migrate() == SCHEMA_VERSION
    assert store.migrate() == SCHEMA_VERSION
    assert store.get_schema_version() == SCHEMA_VERSION


def test_reopen_preserves_data(store, db_path):
    uid = store.create(_contact())
    store.close()
    reopened = ContactStore(db_path)
    try:
        assert reopened.get(uid).display_name == "Ada Lovelace"
        assert reopened.get_schema_version() == SCHEMA_VERSION
    finally:
        reopened.close()


def test_newer_schema_refuses_to_open(tmp_path):
    path = str(tmp_path / "new.db")
    handle = ContactStore(path)
    handle.close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(StoreError):
        ContactStore(path)


def test_revision_bumps_on_every_write(store):
    assert store.get_revision() == 0
    first = store.create(_contact("Ada"))
    assert store.get_revision() == 1
    second = store.create(_contact("Grace"))
    assert store.get_revision() == 2
    store.update(first, _contact("Ada King"))
    assert store.get_revision() == 3
    assert store.get(first).revision == 3
    store.set_tags(second, ["starred"])
    assert store.get_revision() == 4
    store.delete(first)
    assert store.get_revision() == 5


def test_crud_round_trip(store):
    data = _contact()
    data.emails.append(EmailEntry(address="ada@example.org"))
    data.groups.append("Friends")
    uid = store.create(data)
    record = store.get(uid)
    assert record.display_name == "Ada Lovelace"
    assert record.phones[0].number_e164 == "+15550100"
    assert record.emails[0].address == "ada@example.org"
    assert record.groups == ["Friends"]
    assert record.created and record.updated


def test_create_rejects_duplicate_uid(store):
    uid = store.create(_contact())
    with pytest.raises(StoreError):
        store.create(_contact(), uid=uid)


def test_invalid_uid_gets_fresh_uuid(store):
    uid = store.create(_contact(), uid="not-a-uuid")
    assert uid != "not-a-uuid"
    assert store.get(uid).display_name == "Ada Lovelace"


def test_unknown_uid_errors(store):
    with pytest.raises(NotFoundError):
        store.get("00000000-0000-4000-8000-000000000000")
    with pytest.raises(NotFoundError):
        store.update("00000000-0000-4000-8000-000000000000", _contact())
    with pytest.raises(NotFoundError):
        store.delete("00000000-0000-4000-8000-000000000000")
    with pytest.raises(NotFoundError):
        store.set_tags("00000000-0000-4000-8000-000000000000", ["starred"])


def test_delete_leaves_tombstone(store):
    uid = store.create(_contact())
    store.delete(uid)
    with pytest.raises(NotFoundError):
        store.get(uid)
    revision, changed, deleted = store.list_changes(0)
    assert revision == 2
    assert changed == []
    assert deleted == [uid]


def test_list_changes_incremental(store):
    ada = store.create(_contact("Ada"))
    grace = store.create(_contact("Grace"))
    store.update(ada, _contact("Ada King"))
    store.delete(grace)
    revision, changed, deleted = store.list_changes(0)
    assert revision == 4
    assert changed == [ada]
    assert deleted == [grace]
    revision2, changed2, deleted2 = store.list_changes(2)
    assert revision2 == 4
    assert changed2 == [ada]
    assert deleted2 == [grace]
    _, changed3, deleted3 = store.list_changes(4)
    assert changed3 == [] and deleted3 == []


def test_recreate_clears_tombstone(store):
    uid = store.create(_contact())
    store.delete(uid)
    store.create(_contact("Ada Again"), uid=uid)
    _, _, deleted = store.list_changes(0)
    assert uid not in deleted
    assert store.get(uid).display_name == "Ada Again"


def test_set_tags_exact_and_revision(store):
    uid = store.create(_contact())
    store.set_tags(uid, ["starred", "business", "bogus"])
    assert store.get_tags(uid) == ["starred", "business"]
    record = store.get(uid)
    assert record.starred and record.business and not record.blocked
    store.set_tags(uid, [])
    assert store.get_tags(uid) == []


def test_search_name_digits_email(store):
    ada = store.create(_contact("Ada Lovelace"))
    store.create(ContactData(
        display_name="Grace Hopper",
        phones=[PhoneEntry(number_raw="+1 555 0202",
                            number_digits="15550202")],
        emails=[EmailEntry(address="grace@example.org")]))
    assert [u for u, _, _ in store.search("ada")] == [ada]
    assert [n for _, n, _ in store.search("0202")] == ["Grace Hopper"]
    assert [n for _, n, _ in store.search("grace@example")] == ["Grace Hopper"]
    assert store.search("") == []
    assert store.search("   ") == []


def test_search_ranks_number_first(store):
    store.create(ContactData(display_name="Ada Lovelace"))
    store.create(ContactData(
        display_name="Beatrice",
        phones=[PhoneEntry(number_raw="555ada1", number_digits="5551")]))
    hits = store.search("5551")
    assert hits and hits[0][1] == "Beatrice"


def test_injection_string_is_just_data(store):
    nasty = "x'); DROP TABLE contacts;--"
    store.create(_contact(nasty))
    assert store.count() == 1
    assert store.search("DROP TABLE") != []


def test_failed_transaction_rolls_back(store):
    with pytest.raises(RuntimeError):
        with store.transaction() as cur:
            cur.execute("INSERT INTO contacts(uid) VALUES (?)", ("abc",))
            raise RuntimeError("boom")
    assert store.count() == 0
