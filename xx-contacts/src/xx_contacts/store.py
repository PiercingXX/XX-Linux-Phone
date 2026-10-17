"""SQLite contact store: xx-contacts owns its contacts (Revision 2 §1).

Source of truth at ``~/.local/share/xx-contacts/contacts.db``: schema
version in ``meta`` with incremental migrations, WAL journal mode,
``busy_timeout``, mode 0600, parameterized queries only, one writer at a
time (threading lock + ``BEGIN IMMEDIATE``), a global ``revision`` bumped
on every write, and tombstones on delete for later sync.

Pure Python + stdlib sqlite3: no GTK, no D-Bus, safe headless and in tests.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from xx_contacts.privacy import (
    ensure_private_dir,
    fix_file_mode,
    private_umask,
)

SCHEMA_VERSION = 1

DB_FILE_MODE = 0o600
BUSY_TIMEOUT_MS = 5000

VALID_TAGS = ("starred", "business", "blocked")


def is_valid_uid(value: str | None) -> bool:
    try:
        parsed = uuid.UUID(str(value), version=4)
    except (ValueError, AttributeError, TypeError):
        return False
    return str(parsed) == str(value).lower()


def new_uid() -> str:
    return str(uuid.uuid4())


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class PhoneEntry:
    number_raw: str = ""
    number_e164: str | None = None
    number_digits: str = ""
    type: str = ""
    label: str = ""
    is_primary: bool = False


@dataclass
class EmailEntry:
    address: str = ""
    type: str = ""
    label: str = ""
    is_primary: bool = False


@dataclass
class AddressEntry:
    po_box: str = ""
    extended: str = ""
    street: str = ""
    locality: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""
    type: str = ""
    label: str = ""
    is_primary: bool = False


@dataclass
class UrlEntry:
    value: str = ""
    type: str = ""


@dataclass
class ImEntry:
    value: str = ""
    protocol: str = ""
    type: str = ""


@dataclass
class RelationEntry:
    value: str = ""
    type: str = ""


@dataclass
class DateEntry:
    value: str = ""
    kind: str = ""


@dataclass
class ExtraLine:
    prop: str = ""
    params: str = ""
    value: str = ""
    group: str = ""

    def logical_line(self) -> str:
        head = self.prop.upper()
        if self.group:
            head = f"{self.group}.{head}"
        if self.params:
            head = f"{head};{self.params}"
        return f"{head}:{self.value}"


@dataclass
class ContactData:
    display_name: str = ""
    given: str = ""
    family: str = ""
    middle: str = ""
    prefix: str = ""
    suffix: str = ""
    nickname: str = ""
    org: str = ""
    title: str = ""
    birthday: str = ""
    anniversary: str = ""
    note: str = ""
    photo_path: str = ""
    starred: bool = False
    business: bool = False
    blocked: bool = False
    ringtone: str = ""
    phones: list[PhoneEntry] = field(default_factory=list)
    emails: list[EmailEntry] = field(default_factory=list)
    addresses: list[AddressEntry] = field(default_factory=list)
    urls: list[UrlEntry] = field(default_factory=list)
    ims: list[ImEntry] = field(default_factory=list)
    relations: list[RelationEntry] = field(default_factory=list)
    dates: list[DateEntry] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    extra: list[ExtraLine] = field(default_factory=list)


@dataclass
class ContactRecord(ContactData):
    uid: str = ""
    created: str = ""
    updated: str = ""
    revision: int = 0


_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    uid TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    given TEXT NOT NULL DEFAULT '',
    family TEXT NOT NULL DEFAULT '',
    middle TEXT NOT NULL DEFAULT '',
    prefix TEXT NOT NULL DEFAULT '',
    suffix TEXT NOT NULL DEFAULT '',
    nickname TEXT NOT NULL DEFAULT '',
    org TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    birthday TEXT NOT NULL DEFAULT '',
    anniversary TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    photo_path TEXT NOT NULL DEFAULT '',
    starred INTEGER NOT NULL DEFAULT 0,
    business INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    ringtone TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL DEFAULT '',
    updated TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS phones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_uid TEXT NOT NULL REFERENCES contacts(uid) ON DELETE CASCADE,
    number_raw TEXT NOT NULL DEFAULT '',
    number_e164 TEXT,
    number_digits TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_phones_contact ON phones(contact_uid);
CREATE INDEX IF NOT EXISTS idx_phones_digits ON phones(number_digits);
CREATE INDEX IF NOT EXISTS idx_phones_e164 ON phones(number_e164);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_uid TEXT NOT NULL REFERENCES contacts(uid) ON DELETE CASCADE,
    address TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_emails_contact ON emails(contact_uid);
CREATE TABLE IF NOT EXISTS addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_uid TEXT NOT NULL REFERENCES contacts(uid) ON DELETE CASCADE,
    po_box TEXT NOT NULL DEFAULT '',
    extended TEXT NOT NULL DEFAULT '',
    street TEXT NOT NULL DEFAULT '',
    locality TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    postal_code TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_addresses_contact ON addresses(contact_uid);
CREATE TABLE IF NOT EXISTS urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_uid TEXT NOT NULL REFERENCES contacts(uid) ON DELETE CASCADE,
    value TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_urls_contact ON urls(contact_uid);
CREATE TABLE IF NOT EXISTS ims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_uid TEXT NOT NULL REFERENCES contacts(uid) ON DELETE CASCADE,
    value TEXT NOT NULL DEFAULT '',
    protocol TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ims_contact ON ims(contact_uid);
CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_uid TEXT NOT NULL REFERENCES contacts(uid) ON DELETE CASCADE,
    value TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_relations_contact ON relations(contact_uid);
CREATE TABLE IF NOT EXISTS dates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_uid TEXT NOT NULL REFERENCES contacts(uid) ON DELETE CASCADE,
    value TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dates_contact ON dates(contact_uid);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    contact_uid TEXT NOT NULL REFERENCES contacts(uid) ON DELETE CASCADE,
    PRIMARY KEY (group_id, contact_uid)
);
CREATE INDEX IF NOT EXISTS idx_group_members_contact
    ON group_members(contact_uid);
CREATE TABLE IF NOT EXISTS extra_vcard (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_uid TEXT NOT NULL REFERENCES contacts(uid) ON DELETE CASCADE,
    prop TEXT NOT NULL DEFAULT '',
    params TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL DEFAULT '',
    grp TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_extra_contact ON extra_vcard(contact_uid);
CREATE TABLE IF NOT EXISTS tombstones (
    uid TEXT PRIMARY KEY,
    deleted_at TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0
);
"""

MIGRATIONS: list[tuple[int, str]] = [
    (1, _MIGRATION_1),
]


class StoreError(Exception):
    pass


class NotFoundError(StoreError):
    pass


class ContactStore:
    """Single-writer SQLite contact database."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        if path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            ensure_private_dir(parent)
            ensure_private_dir(os.path.join(parent, "photos"))
            with private_umask():
                self._conn = sqlite3.connect(path, check_same_thread=False,
                                             isolation_level=None)
        else:
            self._conn = sqlite3.connect(path, check_same_thread=False,
                                         isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._apply_pragmas()
        self.migrate()
        self._fix_file_modes()

    def _apply_pragmas(self) -> None:
        self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS:d}")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")

    def _fix_file_modes(self) -> None:
        if self._path == ":memory:":
            return
        fix_file_mode(self._path, DB_FILE_MODE)
        for suffix in ("-wal", "-shm", "-journal"):
            fix_file_mode(self._path + suffix, DB_FILE_MODE)

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get_schema_version(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", ("schema_version",)
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return 0

    def get_revision(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", ("revision",)
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return 0

    def migrate(self) -> int:
        """Apply pending migrations in order; return the schema version."""
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta"
                " (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            version = self.get_schema_version()
            if version > SCHEMA_VERSION:
                raise StoreError(
                    f"database schema v{version} is newer than "
                    f"supported v{SCHEMA_VERSION}")
            for target, script in sorted(MIGRATIONS, key=lambda m: m[0]):
                if target > version:
                    self._conn.executescript(script)
                    self._conn.execute(
                        "INSERT INTO meta(key, value) VALUES"
                        " ('schema_version', ?) ON CONFLICT(key) DO UPDATE"
                        " SET value = excluded.value", (str(target),))
                    version = target
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES ('revision', '0')"
                " ON CONFLICT(key) DO NOTHING")
            return version

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        """Single-writer transaction: lock + BEGIN IMMEDIATE."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                yield cur
                self._conn.commit()
                self._fix_file_modes()
            except Exception:
                try:
                    self._conn.rollback()
                except sqlite3.Error:
                    pass
                raise

    def _bump_revision(self, cur: sqlite3.Cursor) -> int:
        cur.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1"
                    " WHERE key = 'revision'")
        row = cur.execute(
            "SELECT value FROM meta WHERE key = 'revision'").fetchone()
        return int(row["value"])

    def _insert_body(self, cur: sqlite3.Cursor, uid: str,
                     data: ContactData, revision: int, created: str,
                     updated: str) -> None:
        cur.execute(
            "INSERT INTO contacts(uid, display_name, given, family, middle,"
            " prefix, suffix, nickname, org, title, birthday, anniversary,"
            " note, photo_path, starred, business, blocked, ringtone,"
            " created, updated, revision)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?)",
            (uid, data.display_name, data.given, data.family, data.middle,
             data.prefix, data.suffix, data.nickname, data.org, data.title,
             data.birthday, data.anniversary, data.note, data.photo_path,
             int(bool(data.starred)), int(bool(data.business)),
             int(bool(data.blocked)), data.ringtone, created, updated,
             revision))
        for phone in data.phones:
            cur.execute(
                "INSERT INTO phones(contact_uid, number_raw, number_e164,"
                " number_digits, type, label, is_primary)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, phone.number_raw, phone.number_e164,
                 phone.number_digits, phone.type, phone.label,
                 int(bool(phone.is_primary))))
        for email in data.emails:
            cur.execute(
                "INSERT INTO emails(contact_uid, address, type, label,"
                " is_primary) VALUES (?, ?, ?, ?, ?)",
                (uid, email.address, email.type, email.label,
                 int(bool(email.is_primary))))
        for address in data.addresses:
            cur.execute(
                "INSERT INTO addresses(contact_uid, po_box, extended, street,"
                " locality, region, postal_code, country, type, label,"
                " is_primary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, address.po_box, address.extended, address.street,
                 address.locality, address.region, address.postal_code,
                 address.country, address.type, address.label,
                 int(bool(address.is_primary))))
        for url in data.urls:
            cur.execute(
                "INSERT INTO urls(contact_uid, value, type)"
                " VALUES (?, ?, ?)", (uid, url.value, url.type))
        for im in data.ims:
            cur.execute(
                "INSERT INTO ims(contact_uid, value, protocol, type)"
                " VALUES (?, ?, ?, ?)", (uid, im.value, im.protocol, im.type))
        for relation in data.relations:
            cur.execute(
                "INSERT INTO relations(contact_uid, value, type)"
                " VALUES (?, ?, ?)",
                (uid, relation.value, relation.type))
        for date in data.dates:
            cur.execute(
                "INSERT INTO dates(contact_uid, value, kind)"
                " VALUES (?, ?, ?)", (uid, date.value, date.kind))
        for name in data.groups:
            cleaned = name.strip()
            if not cleaned:
                continue
            cur.execute("INSERT INTO groups(name) VALUES (?)"
                        " ON CONFLICT(name) DO NOTHING", (cleaned,))
            row = cur.execute("SELECT id FROM groups WHERE name = ?",
                              (cleaned,)).fetchone()
            cur.execute(
                "INSERT INTO group_members(group_id, contact_uid)"
                " VALUES (?, ?) ON CONFLICT DO NOTHING",
                (row["id"], uid))
        for extra in data.extra:
            cur.execute(
                "INSERT INTO extra_vcard(contact_uid, prop, params, value,"
                " grp) VALUES (?, ?, ?, ?, ?)",
                (uid, extra.prop.upper(), extra.params, extra.value,
                 extra.group))

    def _delete_body(self, cur: sqlite3.Cursor, uid: str) -> None:
        cur.execute("DELETE FROM group_members WHERE contact_uid = ?", (uid,))
        cur.execute("DELETE FROM extra_vcard WHERE contact_uid = ?", (uid,))
        cur.execute("DELETE FROM phones WHERE contact_uid = ?", (uid,))
        cur.execute("DELETE FROM emails WHERE contact_uid = ?", (uid,))
        cur.execute("DELETE FROM addresses WHERE contact_uid = ?", (uid,))
        cur.execute("DELETE FROM urls WHERE contact_uid = ?", (uid,))
        cur.execute("DELETE FROM ims WHERE contact_uid = ?", (uid,))
        cur.execute("DELETE FROM relations WHERE contact_uid = ?", (uid,))
        cur.execute("DELETE FROM dates WHERE contact_uid = ?", (uid,))
        cur.execute("DELETE FROM contacts WHERE uid = ?", (uid,))

    def _read_record(self, cur: sqlite3.Cursor, uid: str) -> ContactRecord:
        row = cur.execute("SELECT * FROM contacts WHERE uid = ?",
                          (uid,)).fetchone()
        if row is None:
            raise NotFoundError(f"unknown contact: {uid}")
        record = ContactRecord(
            uid=row["uid"], display_name=row["display_name"],
            given=row["given"], family=row["family"], middle=row["middle"],
            prefix=row["prefix"], suffix=row["suffix"],
            nickname=row["nickname"], org=row["org"], title=row["title"],
            birthday=row["birthday"], anniversary=row["anniversary"],
            note=row["note"], photo_path=row["photo_path"],
            starred=bool(row["starred"]), business=bool(row["business"]),
            blocked=bool(row["blocked"]), ringtone=row["ringtone"],
            created=row["created"], updated=row["updated"],
            revision=int(row["revision"]))
        for prow in cur.execute(
                "SELECT * FROM phones WHERE contact_uid = ? ORDER BY id",
                (uid,)):
            record.phones.append(PhoneEntry(
                number_raw=prow["number_raw"], number_e164=prow["number_e164"],
                number_digits=prow["number_digits"], type=prow["type"],
                label=prow["label"], is_primary=bool(prow["is_primary"])))
        for erow in cur.execute(
                "SELECT * FROM emails WHERE contact_uid = ? ORDER BY id",
                (uid,)):
            record.emails.append(EmailEntry(
                address=erow["address"], type=erow["type"],
                label=erow["label"], is_primary=bool(erow["is_primary"])))
        for arow in cur.execute(
                "SELECT * FROM addresses WHERE contact_uid = ? ORDER BY id",
                (uid,)):
            record.addresses.append(AddressEntry(
                po_box=arow["po_box"], extended=arow["extended"],
                street=arow["street"], locality=arow["locality"],
                region=arow["region"], postal_code=arow["postal_code"],
                country=arow["country"], type=arow["type"],
                label=arow["label"], is_primary=bool(arow["is_primary"])))
        for urow in cur.execute(
                "SELECT * FROM urls WHERE contact_uid = ? ORDER BY id",
                (uid,)):
            record.urls.append(UrlEntry(value=urow["value"],
                                        type=urow["type"]))
        for irow in cur.execute(
                "SELECT * FROM ims WHERE contact_uid = ? ORDER BY id", (uid,)):
            record.ims.append(ImEntry(value=irow["value"],
                                      protocol=irow["protocol"],
                                      type=irow["type"]))
        for rrow in cur.execute(
                "SELECT * FROM relations WHERE contact_uid = ? ORDER BY id",
                (uid,)):
            record.relations.append(RelationEntry(value=rrow["value"],
                                                   type=rrow["type"]))
        for drow in cur.execute(
                "SELECT * FROM dates WHERE contact_uid = ? ORDER BY id",
                (uid,)):
            record.dates.append(DateEntry(value=drow["value"],
                                          kind=drow["kind"]))
        for grow in cur.execute(
                "SELECT g.name FROM groups g JOIN group_members m"
                " ON m.group_id = g.id WHERE m.contact_uid = ?"
                " ORDER BY m.rowid", (uid,)):
            record.groups.append(grow["name"])
        for xrow in cur.execute(
                "SELECT * FROM extra_vcard WHERE contact_uid = ?"
                " ORDER BY id", (uid,)):
            record.extra.append(ExtraLine(prop=xrow["prop"],
                                          params=xrow["params"],
                                          value=xrow["value"],
                                          group=xrow["grp"]))
        return record

    def create(self, data: ContactData, uid: str | None = None) -> str:
        """Insert a contact; bump the global revision; return the uid."""
        chosen = uid if is_valid_uid(uid) else new_uid()
        stamp = utcnow()
        with self.transaction() as cur:
            if cur.execute("SELECT 1 FROM contacts WHERE uid = ?",
                           (chosen,)).fetchone() is not None:
                raise StoreError(f"contact already exists: {chosen}")
            revision = self._bump_revision(cur)
            cur.execute("DELETE FROM tombstones WHERE uid = ?", (chosen,))
            self._insert_body(cur, chosen, data, revision, stamp, stamp)
        return chosen

    def get(self, uid: str) -> ContactRecord:
        with self._lock:
            cur = self._conn.cursor()
            return self._read_record(cur, uid)

    def update(self, uid: str, data: ContactData) -> int:
        """Replace a contact wholesale; return the new global revision."""
        stamp = utcnow()
        with self.transaction() as cur:
            if cur.execute("SELECT 1 FROM contacts WHERE uid = ?",
                           (uid,)).fetchone() is None:
                raise NotFoundError(f"unknown contact: {uid}")
            created = cur.execute("SELECT created FROM contacts"
                                  " WHERE uid = ?", (uid,)).fetchone()["created"]
            revision = self._bump_revision(cur)
            self._delete_body(cur, uid)
            self._insert_body(cur, uid, data, revision, created, stamp)
            return revision

    def delete(self, uid: str) -> int:
        """Delete a contact, leave a tombstone; return the new revision."""
        with self.transaction() as cur:
            if cur.execute("SELECT 1 FROM contacts WHERE uid = ?",
                           (uid,)).fetchone() is None:
                raise NotFoundError(f"unknown contact: {uid}")
            revision = self._bump_revision(cur)
            self._delete_body(cur, uid)
            cur.execute(
                "INSERT INTO tombstones(uid, deleted_at, revision)"
                " VALUES (?, ?, ?) ON CONFLICT(uid) DO UPDATE SET"
                " deleted_at = excluded.deleted_at,"
                " revision = excluded.revision",
                (uid, utcnow(), revision))
            return revision

    def set_tags(self, uid: str, tags: list[str] | tuple[str, ...]) -> int:
        """Set the starred/business/blocked flags; return the new revision."""
        wanted = {t for t in tags if t in VALID_TAGS}
        with self.transaction() as cur:
            row = cur.execute("SELECT starred, business, blocked FROM contacts"
                              " WHERE uid = ?", (uid,)).fetchone()
            if row is None:
                raise NotFoundError(f"unknown contact: {uid}")
            revision = self._bump_revision(cur)
            cur.execute(
                "UPDATE contacts SET starred = ?, business = ?, blocked = ?,"
                " updated = ?, revision = ? WHERE uid = ?",
                (int("starred" in wanted), int("business" in wanted),
                 int("blocked" in wanted), utcnow(), revision, uid))
            return revision

    def update_tags(self, uid: str, add: Iterable[str] = (),
                    remove: Iterable[str] = ()) -> int:
        """Atomically add/remove tags; return the new revision.

        The read-modify-write runs inside one store transaction, so two
        threads updating different tags cannot lose each other's change.
        Unknown tags are ignored, exactly as in ``set_tags``.
        """
        add_wanted = {t for t in (add or ()) if t in VALID_TAGS}
        remove_wanted = {t for t in (remove or ()) if t in VALID_TAGS}
        with self.transaction() as cur:
            row = cur.execute("SELECT starred, business, blocked FROM contacts"
                              " WHERE uid = ?", (uid,)).fetchone()
            if row is None:
                raise NotFoundError(f"unknown contact: {uid}")
            current = set()
            if row["starred"]:
                current.add("starred")
            if row["business"]:
                current.add("business")
            if row["blocked"]:
                current.add("blocked")
            wanted = (current - remove_wanted) | add_wanted
            revision = self._bump_revision(cur)
            cur.execute(
                "UPDATE contacts SET starred = ?, business = ?, blocked = ?,"
                " updated = ?, revision = ? WHERE uid = ?",
                (int("starred" in wanted), int("business" in wanted),
                 int("blocked" in wanted), utcnow(), revision, uid))
            return revision

    def apply_import(self, creates, updates
                     ) -> tuple[int, list[str], list[str]]:
        """Write one import batch in a single transaction, one revision.

        ``creates`` holds ``(uid, ContactData)`` pairs with fresh,
        collision-free uids; ``updates`` holds ``(uid, ContactData)``
        pairs for existing rows. Every touched row lands on the same
        new global revision, so the caller emits exactly one
        ``Changed`` signal for the whole batch. Empty batches bump
        nothing and return revision 0 with empty uid lists.
        """
        creates = list(creates or ())
        updates = list(updates or ())
        if not creates and not updates:
            return 0, [], []
        stamp = utcnow()
        with self.transaction() as cur:
            revision = self._bump_revision(cur)
            created_uids: list[str] = []
            for uid, data in creates:
                if cur.execute("SELECT 1 FROM contacts WHERE uid = ?",
                               (uid,)).fetchone() is not None:
                    raise StoreError(f"contact already exists: {uid}")
                cur.execute("DELETE FROM tombstones WHERE uid = ?", (uid,))
                self._insert_body(cur, uid, data, revision, stamp, stamp)
                created_uids.append(uid)
            updated_uids: list[str] = []
            for uid, data in updates:
                row = cur.execute("SELECT created FROM contacts"
                                  " WHERE uid = ?", (uid,)).fetchone()
                if row is None:
                    raise NotFoundError(f"unknown contact: {uid}")
                created = row["created"]
                self._delete_body(cur, uid)
                self._insert_body(cur, uid, data, revision, created, stamp)
                updated_uids.append(uid)
            return revision, created_uids, updated_uids

    def get_tags(self, uid: str) -> list[str]:
        record = self.get(uid)
        tags = []
        if record.starred:
            tags.append("starred")
        if record.business:
            tags.append("business")
        if record.blocked:
            tags.append("blocked")
        return tags

    def list_changes(self, since_revision: int = 0) -> tuple[int, list[str],
                                                             list[str]]:
        """Return ``(revision, changed_uids, deleted_uids)`` since a revision."""
        with self._lock:
            cur = self._conn.cursor()
            revision = self.get_revision()
            changed = [r["uid"] for r in cur.execute(
                "SELECT uid FROM contacts WHERE revision > ? ORDER BY uid",
                (int(since_revision),))]
            deleted = [r["uid"] for r in cur.execute(
                "SELECT uid FROM tombstones WHERE revision > ? ORDER BY uid",
                (int(since_revision),))]
            return revision, changed, deleted

    def all_uids(self) -> list[str]:
        with self._lock:
            cur = self._conn.cursor()
            return [r["uid"] for r in
                    cur.execute("SELECT uid FROM contacts ORDER BY"
                                " display_name, uid")]

    def all_records(self) -> list[ContactRecord]:
        with self._lock:
            cur = self._conn.cursor()
            uids = [r["uid"] for r in
                    cur.execute("SELECT uid FROM contacts ORDER BY uid")]
            return [self._read_record(cur, uid) for uid in uids]

    def count(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            row = cur.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()
            return int(row["n"])

    def lookup_email(self, address: str | None
                     ) -> tuple[str, str] | None:
        """Exact email lookup: ``(uid, display_name)`` or None.

        Case-insensitive on the whole address, never a substring or
        digit match, so a sender address cannot resolve to an
        unrelated contact.
        """
        key = (address or "").strip()
        if not key or "@" not in key:
            return None
        with self._lock:
            cur = self._conn.cursor()
            row = cur.execute(
                "SELECT contact_uid FROM emails"
                " WHERE lower(address) = lower(?)"
                " ORDER BY contact_uid LIMIT 1",
                (key,)).fetchone()
            if row is None:
                return None
            try:
                record = self._read_record(cur, row["contact_uid"])
            except NotFoundError:
                return None
            return record.uid, record.display_name

    @staticmethod
    def _primary_contact_value(record) -> str:
        """Best single contact value: primary email, else primary phone."""
        for email in record.emails:
            if email.is_primary and (email.address or "").strip():
                return email.address
        for email in record.emails:
            if (email.address or "").strip():
                return email.address
        for phone in record.phones:
            if phone.is_primary and (phone.number_raw or "").strip():
                return phone.number_raw
        if record.phones:
            return record.phones[0].number_raw
        return ""

    def search(self, query: str, limit: int = 50) -> list[tuple[str, str, str]]:
        """Search name, number digits and email; number hits rank first.

        Number matching is skipped for email-like queries and for
        queries with fewer than 3 digits, so an address such as
        ``john1@example.com`` cannot match every ``+1...`` contact. The
        third field is the primary email when the contact has one,
        else the primary phone.
        """
        from xx_contacts import phones as phone_util

        folded = (query or "").casefold().strip()
        if not folded:
            return []
        digits = phone_util.digits_of(folded)
        with self._lock:
            cur = self._conn.cursor()
            like = f"%{folded}%"
            rows = cur.execute(
                "SELECT uid, display_name FROM contacts"
                " WHERE lower(display_name) LIKE lower(?)"
                " OR uid IN (SELECT contact_uid FROM emails"
                " WHERE lower(address) LIKE lower(?))"
                " ORDER BY display_name, uid",
                (like, like)).fetchall()
            number_hits: list[str] = []
            if digits and "@" not in folded and len(digits) >= 3:
                for prow in cur.execute(
                        "SELECT DISTINCT contact_uid, number_digits"
                        " FROM phones"):
                    stored = prow["number_digits"] or ""
                    if stored and (stored.startswith(digits)
                                   or stored.endswith(digits)):
                        number_hits.append(prow["contact_uid"])
        ordered = [u for u in number_hits]
        seen = set(ordered)
        for row in rows:
            if row["uid"] not in seen:
                ordered.append(row["uid"])
                seen.add(row["uid"])
        results: list[tuple[str, str, str]] = []
        with self._lock:
            cur = self._conn.cursor()
            for uid in ordered[:max(int(limit), 0)]:
                try:
                    record = self._read_record(cur, uid)
                except NotFoundError:
                    continue
                primary = self._primary_contact_value(record)
                results.append((uid, record.display_name, primary))
        return results

    def primary_number(self, uid: str) -> str:
        try:
            record = self.get(uid)
        except NotFoundError:
            return ""
        for phone in record.phones:
            if phone.is_primary:
                return phone.number_raw
        return record.phones[0].number_raw if record.phones else ""


def default_db_path(home: str) -> str:
    return os.path.join(home, ".local", "share", "xx-contacts",
                        "contacts.db")


def default_photos_dir(home: str) -> str:
    return os.path.join(home, ".local", "share", "xx-contacts", "photos")


def default_exports_dir(home: str) -> str:
    return os.path.join(home, ".local", "share", "xx-contacts", "exports")
