"""SQLite calendar store: xx-calendar owns its events (Revision 2 §1).

Source of truth at ``~/.local/share/xx-calendar/calendar.db``: schema
version in ``meta`` with incremental migrations, WAL journal mode,
``busy_timeout``, mode 0600 files in 0700 dirs, parameterized queries only,
one writer at a time (threading lock + ``BEGIN IMMEDIATE``), a global
``revision`` bumped on every write, and tombstones on delete for later sync.

Each VEVENT keeps its raw text in ``events.ics`` plus indexed columns the
service and recurrence expansion query on: uid, calendar id, DTSTART/DTEND
as UTC int64 millis, the original TZID, the all-day flag, RRULE text, the
RECURRENCE-ID override instant, status, and the row revision. Exception
rows (RECURRENCE-ID overrides) share the event UID and are keyed by
``(uid, recurrence_id)`` with ``recurrence_id = 0`` for the parent row.

Pure Python + stdlib sqlite3: no GTK, no D-Bus, safe headless and in tests.
"""
from __future__ import annotations

import os
import sqlite3
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

SCHEMA_VERSION = 2

DB_FILE_MODE = 0o600
DB_DIR_MODE = 0o700
BUSY_TIMEOUT_MS = 5000


def ensure_private_dir(path: str) -> str:
    """Create *path* with mode 0700; repair an existing dir to 0700."""
    os.makedirs(path, mode=DB_DIR_MODE, exist_ok=True)
    try:
        if stat.S_IMODE(os.stat(path).st_mode) != DB_DIR_MODE:
            os.chmod(path, DB_DIR_MODE)
    except OSError:
        pass
    return path


def fix_file_mode(path: str, mode: int = DB_FILE_MODE) -> None:
    """Repair *path* to *mode* when it exists with another mode."""
    try:
        if stat.S_IMODE(os.stat(path).st_mode) != mode:
            os.chmod(path, mode)
    except OSError:
        pass


@contextmanager
def private_umask() -> Iterator[None]:
    """Run a creation block under ``umask 077``; restore it after."""
    old = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(old)


@dataclass
class CalendarRecord:
    id: str = ""
    display_name: str = ""
    account_name: str = ""
    color: str = ""
    is_visible: bool = True
    is_writable: bool = True
    sigil_tier: int = 0
    revision: int = 0


@dataclass
class EventRecord:
    uid: str = ""
    recurrence_id: int = 0
    calendar_id: str = ""
    ics: str = ""
    dtstart: int = 0
    dtend: int = 0
    tzid: str = ""
    tzid_end: str = ""
    all_day: bool = False
    rrule: str = ""
    status: str = ""
    revision: int = 0
    created: str = ""
    updated: str = ""


@dataclass
class EventChange:
    uid: str = ""
    recurrence_id: int = 0


_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calendars (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    account_name TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    is_visible INTEGER NOT NULL DEFAULT 1,
    is_writable INTEGER NOT NULL DEFAULT 1,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    uid TEXT NOT NULL,
    recurrence_id INTEGER NOT NULL DEFAULT 0,
    calendar_id TEXT NOT NULL DEFAULT '',
    ics TEXT NOT NULL DEFAULT '',
    dtstart INTEGER NOT NULL DEFAULT 0,
    dtend INTEGER NOT NULL DEFAULT 0,
    all_day INTEGER NOT NULL DEFAULT 0,
    rrule TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL DEFAULT '',
    updated TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (uid, recurrence_id)
);
CREATE INDEX IF NOT EXISTS idx_events_calendar ON events(calendar_id);
CREATE INDEX IF NOT EXISTS idx_events_dtstart ON events(dtstart);
CREATE INDEX IF NOT EXISTS idx_events_revision ON events(revision);
CREATE TABLE IF NOT EXISTS tombstones (
    uid TEXT NOT NULL,
    recurrence_id INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (uid, recurrence_id)
);
"""

_MIGRATION_2 = """
ALTER TABLE calendars ADD COLUMN sigil_tier INTEGER NOT NULL DEFAULT 0;
ALTER TABLE events ADD COLUMN tzid TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN tzid_end TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN status TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_events_uid ON events(uid);
CREATE INDEX IF NOT EXISTS idx_events_rrule ON events(rrule);
CREATE INDEX IF NOT EXISTS idx_events_dtend ON events(dtend);
CREATE INDEX IF NOT EXISTS idx_tombstones_revision ON tombstones(revision);
"""

MIGRATIONS: list[tuple[int, str]] = [
    (1, _MIGRATION_1),
    (2, _MIGRATION_2),
]


class StoreError(Exception):
    pass


class NotFoundError(StoreError):
    pass


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class CalendarStore:
    """Single-writer SQLite calendar database."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        if path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            ensure_private_dir(parent)
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

    def _existing_columns(self, table: str) -> set[str]:
        try:
            rows = self._conn.execute(
                f'PRAGMA table_info("{table}")').fetchall()
        except sqlite3.Error:
            return set()
        return {str(row["name"]) for row in rows} if rows else set()

    @staticmethod
    def _split_statements(script: str) -> list[str]:
        return [part.strip() for part in script.split(";") if part.strip()]

    def _apply_migration_script(self, script: str) -> None:
        for statement in self._split_statements(script):
            upper = statement.strip().upper()
            if upper.startswith("ALTER TABLE"):
                tokens = statement.split()
                try:
                    table = tokens[2].strip('"[]')
                    column = tokens[5].strip('"[]').split("(")[0]
                except IndexError:
                    self._conn.execute(statement)
                    continue
                if column in self._existing_columns(table):
                    continue
                try:
                    self._conn.execute(statement)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
                    if column not in self._existing_columns(table):
                        raise
                continue
            self._conn.execute(statement)

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
                    self._conn.execute("BEGIN IMMEDIATE")
                    try:
                        self._apply_migration_script(script)
                        self._conn.execute(
                            "INSERT INTO meta(key, value) VALUES"
                            " ('schema_version', ?) ON CONFLICT(key) DO UPDATE"
                            " SET value = excluded.value", (str(target),))
                        self._conn.execute("COMMIT")
                    except Exception:
                        try:
                            self._conn.execute("ROLLBACK")
                        except sqlite3.Error:
                            pass
                        raise
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

    # ------------------------------------------------------------ calendars

    def create_calendar(self, record: CalendarRecord) -> int:
        """Insert a calendar; bump the global revision; return it."""
        if not record.id:
            raise StoreError("calendar id must not be empty")
        with self.transaction() as cur:
            if cur.execute("SELECT 1 FROM calendars WHERE id = ?",
                           (record.id,)).fetchone() is not None:
                raise StoreError(f"calendar already exists: {record.id}")
            revision = self._bump_revision(cur)
            cur.execute(
                "INSERT INTO calendars(id, display_name, account_name,"
                " color, is_visible, is_writable, sigil_tier, revision)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (record.id, record.display_name, record.account_name,
                 record.color, int(bool(record.is_visible)),
                 int(bool(record.is_writable)), int(record.sigil_tier),
                 revision))
            return revision

    def get_calendar(self, calendar_id: str) -> CalendarRecord:
        with self._lock:
            cur = self._conn.cursor()
            return self._read_calendar(cur, calendar_id)

    def _read_calendar(self, cur: sqlite3.Cursor,
                       calendar_id: str) -> CalendarRecord:
        row = cur.execute("SELECT * FROM calendars WHERE id = ?",
                          (calendar_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"unknown calendar: {calendar_id}")
        keys = row.keys()
        return CalendarRecord(
            id=row["id"], display_name=row["display_name"],
            account_name=row["account_name"], color=row["color"],
            is_visible=bool(row["is_visible"]),
            is_writable=bool(row["is_writable"]),
            sigil_tier=int(row["sigil_tier"]) if "sigil_tier" in keys else 0,
            revision=int(row["revision"]))

    def list_calendars(self) -> list[CalendarRecord]:
        with self._lock:
            cur = self._conn.cursor()
            ids = [r["id"] for r in cur.execute(
                "SELECT id FROM calendars ORDER BY display_name, id")]
            return [self._read_calendar(cur, cid) for cid in ids]

    def update_calendar(self, record: CalendarRecord) -> int:
        """Replace a calendar wholesale; return the new global revision."""
        with self.transaction() as cur:
            if cur.execute("SELECT 1 FROM calendars WHERE id = ?",
                           (record.id,)).fetchone() is None:
                raise NotFoundError(f"unknown calendar: {record.id}")
            revision = self._bump_revision(cur)
            cur.execute(
                "UPDATE calendars SET display_name = ?, account_name = ?,"
                " color = ?, is_visible = ?, is_writable = ?,"
                " sigil_tier = ?, revision = ? WHERE id = ?",
                (record.display_name, record.account_name, record.color,
                 int(bool(record.is_visible)), int(bool(record.is_writable)),
                 int(record.sigil_tier), revision, record.id))
            return revision

    def delete_calendar(self, calendar_id: str) -> int:
        """Delete a calendar, its events, and leave tombstones."""
        stamp = utcnow()
        with self.transaction() as cur:
            if cur.execute("SELECT 1 FROM calendars WHERE id = ?",
                           (calendar_id,)).fetchone() is None:
                raise NotFoundError(f"unknown calendar: {calendar_id}")
            revision = self._bump_revision(cur)
            doomed = cur.execute(
                "SELECT uid, recurrence_id FROM events"
                " WHERE calendar_id = ?", (calendar_id,)).fetchall()
            for row in doomed:
                cur.execute(
                    "INSERT INTO tombstones(uid, recurrence_id, deleted_at,"
                    " revision) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(uid, recurrence_id) DO UPDATE SET"
                    " deleted_at = excluded.deleted_at,"
                    " revision = excluded.revision",
                    (row["uid"], row["recurrence_id"], stamp, revision))
            cur.execute("DELETE FROM events WHERE calendar_id = ?",
                        (calendar_id,))
            cur.execute("DELETE FROM calendars WHERE id = ?", (calendar_id,))
            return revision

    # --------------------------------------------------------------- events

    @staticmethod
    def _event_params(record: EventRecord, revision: int, created: str,
                      updated: str) -> tuple:
        return (record.uid, int(record.recurrence_id), record.calendar_id,
                record.ics, int(record.dtstart), int(record.dtend),
                record.tzid, record.tzid_end, int(bool(record.all_day)),
                record.rrule, record.status, revision, created, updated)

    def put_event(self, record: EventRecord) -> int:
        """Insert or replace one event row; return the new revision."""
        if not record.uid:
            raise StoreError("event uid must not be empty")
        stamp = utcnow()
        with self.transaction() as cur:
            existing = cur.execute(
                "SELECT created FROM events WHERE uid = ?"
                " AND recurrence_id = ?",
                (record.uid, int(record.recurrence_id))).fetchone()
            created = existing["created"] if existing else stamp
            revision = self._bump_revision(cur)
            cur.execute(
                "DELETE FROM tombstones WHERE uid = ? AND recurrence_id = ?",
                (record.uid, int(record.recurrence_id)))
            cur.execute(
                "INSERT INTO events(uid, recurrence_id, calendar_id, ics,"
                " dtstart, dtend, tzid, tzid_end, all_day, rrule, status,"
                " revision, created, updated)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(uid, recurrence_id) DO UPDATE SET"
                " calendar_id = excluded.calendar_id, ics = excluded.ics,"
                " dtstart = excluded.dtstart, dtend = excluded.dtend,"
                " tzid = excluded.tzid, tzid_end = excluded.tzid_end,"
                " all_day = excluded.all_day, rrule = excluded.rrule,"
                " status = excluded.status, revision = excluded.revision,"
                " updated = excluded.updated",
                self._event_params(record, revision, created, stamp))
            return revision

    def all_keys(self) -> set[tuple[str, int]]:
        """Every ``(uid, recurrence_id)`` pair in the store, one query."""
        with self._lock:
            cur = self._conn.cursor()
            return {(r["uid"], int(r["recurrence_id"])) for r in cur.execute(
                "SELECT uid, recurrence_id FROM events")}

    def bulk_put_events(self, records: list[EventRecord]) -> int:
        """Upsert many rows in one transaction with one revision bump."""
        if not records:
            return self.get_revision()
        stamp = utcnow()
        with self.transaction() as cur:
            revision = self._bump_revision(cur)
            for record in records:
                if not record.uid:
                    raise StoreError("event uid must not be empty")
                existing = cur.execute(
                    "SELECT created FROM events WHERE uid = ?"
                    " AND recurrence_id = ?",
                    (record.uid, int(record.recurrence_id))).fetchone()
                created = existing["created"] if existing else stamp
                cur.execute(
                    "DELETE FROM tombstones WHERE uid = ?"
                    " AND recurrence_id = ?",
                    (record.uid, int(record.recurrence_id)))
                cur.execute(
                    "INSERT INTO events(uid, recurrence_id, calendar_id, ics,"
                    " dtstart, dtend, tzid, tzid_end, all_day, rrule, status,"
                    " revision, created, updated)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(uid, recurrence_id) DO UPDATE SET"
                    " calendar_id = excluded.calendar_id,"
                    " ics = excluded.ics,"
                    " dtstart = excluded.dtstart, dtend = excluded.dtend,"
                    " tzid = excluded.tzid, tzid_end = excluded.tzid_end,"
                    " all_day = excluded.all_day, rrule = excluded.rrule,"
                    " status = excluded.status,"
                    " revision = excluded.revision,"
                    " updated = excluded.updated",
                    self._event_params(record, revision, created, stamp))
            return revision

    def get_event(self, uid: str, recurrence_id: int = 0) -> EventRecord:
        with self._lock:
            cur = self._conn.cursor()
            return self._read_event(cur, uid, recurrence_id)

    def _read_event(self, cur: sqlite3.Cursor, uid: str,
                    recurrence_id: int) -> EventRecord:
        row = cur.execute(
            "SELECT * FROM events WHERE uid = ? AND recurrence_id = ?",
            (uid, int(recurrence_id))).fetchone()
        if row is None:
            raise NotFoundError(f"unknown event: {uid}")
        keys = row.keys()
        return EventRecord(
            uid=row["uid"], recurrence_id=int(row["recurrence_id"]),
            calendar_id=row["calendar_id"], ics=row["ics"],
            dtstart=int(row["dtstart"]), dtend=int(row["dtend"]),
            tzid=row["tzid"] if "tzid" in keys else "",
            tzid_end=row["tzid_end"] if "tzid_end" in keys else "",
            all_day=bool(row["all_day"]), rrule=row["rrule"],
            status=row["status"] if "status" in keys else "",
            revision=int(row["revision"]), created=row["created"],
            updated=row["updated"])

    def delete_event(self, uid: str, recurrence_id: int = 0) -> int:
        """Delete one event row and leave a tombstone; return revision."""
        with self.transaction() as cur:
            if cur.execute(
                    "SELECT 1 FROM events WHERE uid = ? AND recurrence_id = ?",
                    (uid, int(recurrence_id))).fetchone() is None:
                raise NotFoundError(f"unknown event: {uid}")
            revision = self._bump_revision(cur)
            cur.execute(
                "DELETE FROM events WHERE uid = ? AND recurrence_id = ?",
                (uid, int(recurrence_id)))
            cur.execute(
                "INSERT INTO tombstones(uid, recurrence_id, deleted_at,"
                " revision) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(uid, recurrence_id) DO UPDATE SET"
                " deleted_at = excluded.deleted_at,"
                " revision = excluded.revision",
                (uid, int(recurrence_id), utcnow(), revision))
            return revision

    def delete_series(self, uid: str) -> int:
        """Delete a whole series (parent plus overrides); return revision."""
        with self.transaction() as cur:
            rows = cur.execute("SELECT recurrence_id FROM events"
                               " WHERE uid = ?", (uid,)).fetchall()
            if not rows:
                raise NotFoundError(f"unknown event: {uid}")
            revision = self._bump_revision(cur)
            stamp = utcnow()
            for row in list(rows):
                cur.execute(
                    "INSERT INTO tombstones(uid, recurrence_id, deleted_at,"
                    " revision) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(uid, recurrence_id) DO UPDATE SET"
                    " deleted_at = excluded.deleted_at,"
                    " revision = excluded.revision",
                    (uid, int(row["recurrence_id"]), stamp, revision))
            cur.execute("DELETE FROM events WHERE uid = ?", (uid,))
            return revision

    def overrides_for(self, uid: str) -> list[EventRecord]:
        """All RECURRENCE-ID override rows for one UID, ordered by id."""
        with self._lock:
            cur = self._conn.cursor()
            rows = cur.execute(
                "SELECT recurrence_id FROM events WHERE uid = ?"
                " AND recurrence_id != 0 ORDER BY recurrence_id",
                (uid,)).fetchall()
            return [self._read_event(cur, uid, int(r["recurrence_id"]))
                    for r in rows]

    def events_in_window(self, start_utc: int, end_utc: int,
                         calendar_ids: list[str] | tuple[str, ...] | None
                         = None) -> list[EventRecord]:
        """Rows that may cover ``[start_utc, end_utc)``.

        Returns one-shots overlapping the window plus every recurring
        candidate (recurrence expansion narrows those afterwards), so the
        query stays windowed while unbounded rules are never read past.
        """
        with self._lock:
            cur = self._conn.cursor()
            query = ("SELECT uid, recurrence_id FROM events"
                     " WHERE (rrule != '' OR (dtstart < ? AND dtend > ?)"
                     " OR (recurrence_id != 0 AND recurrence_id >= ?"
                     " AND recurrence_id < ?))")
            args: list = [int(end_utc), int(start_utc),
                          int(start_utc), int(end_utc)]
            if calendar_ids is not None:
                ids = list(calendar_ids)
                if not ids:
                    return []
                placeholders = ",".join("?" for _ in ids)
                query += f" AND calendar_id IN ({placeholders})"
                args.extend(ids)
            query += " ORDER BY dtstart, uid"
            rows = cur.execute(query, args).fetchall()
            return [self._read_event(cur, r["uid"], int(r["recurrence_id"]))
                    for r in rows]

    def list_all_events(self, calendar_ids=None) -> list[EventRecord]:
        """Every stored row, optionally filtered to *calendar_ids*."""
        with self._lock:
            cur = self._conn.cursor()
            query = "SELECT uid, recurrence_id FROM events"
            args: list = []
            if calendar_ids is not None:
                ids = list(calendar_ids)
                if not ids:
                    return []
                placeholders = ",".join("?" for _ in ids)
                query += f" WHERE calendar_id IN ({placeholders})"
                args.extend(ids)
            query += " ORDER BY uid, recurrence_id"
            rows = cur.execute(query, args).fetchall()
            return [self._read_event(cur, r["uid"], int(r["recurrence_id"]))
                    for r in rows]

    def count_events(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            row = cur.execute("SELECT COUNT(*) AS n FROM events").fetchone()
            return int(row["n"])

    # ---------------------------------------------------------------- sync

    def list_changes(self, since_revision: int = 0
                     ) -> tuple[int, list[str], list[str]]:
        """Return ``(revision, changed_uids, deleted_uids)`` since a rev.

        ``since_revision`` is exclusive: a client that has applied ``N``
        passes ``N`` and learns everything newer. Changed holds UIDs with a
        row revision above the mark; deleted holds tombstoned UIDs above it.
        """
        with self._lock:
            cur = self._conn.cursor()
            revision = self.get_revision()
            changed = [r["uid"] for r in cur.execute(
                "SELECT DISTINCT uid FROM events WHERE revision > ?"
                " ORDER BY uid", (int(since_revision),))]
            deleted = [r["uid"] for r in cur.execute(
                "SELECT DISTINCT uid FROM tombstones WHERE revision > ?"
                " ORDER BY uid", (int(since_revision),))]
            return revision, changed, deleted


def default_db_path(home: str) -> str:
    return os.path.join(home, ".local", "share", "xx-calendar",
                        "calendar.db")


def ensure_store_dir(path: str) -> str:
    """Create the store directory (0700) before opening files in it."""
    return ensure_private_dir(os.path.dirname(os.path.abspath(path)))
