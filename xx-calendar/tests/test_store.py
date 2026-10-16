"""Store tests: schema + migrations, file modes, List(since) semantics.

Headless: stdlib sqlite3 only, no display, no GTK import.
"""
import os
import sqlite3
import stat
import threading

import pytest

from xx_calendar.store import (
    CalendarRecord,
    CalendarStore,
    EventRecord,
    NotFoundError,
    StoreError,
    default_db_path,
)

V1_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta(key, value) VALUES ('schema_version', '1');
INSERT INTO meta(key, value) VALUES ('revision', '4');
CREATE TABLE calendars (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    account_name TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    is_visible INTEGER NOT NULL DEFAULT 1,
    is_writable INTEGER NOT NULL DEFAULT 1,
    revision INTEGER NOT NULL DEFAULT 0
);
INSERT INTO calendars(id, display_name, revision)
    VALUES ('local', 'On This Phone', 2);
CREATE TABLE events (
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
INSERT INTO events(uid, calendar_id, ics, dtstart, dtend, revision)
    VALUES ('evt-v1', 'local', 'BEGIN:VEVENT', 1000, 2000, 4);
CREATE TABLE tombstones (
    uid TEXT NOT NULL,
    recurrence_id INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (uid, recurrence_id)
);
"""


def _event(uid="evt-1", calendar_id="local", start=1000, end=2000,
           rrule="", recurrence_id=0, status="", tzid=""):
    return EventRecord(uid=uid, recurrence_id=recurrence_id,
                       calendar_id=calendar_id,
                       ics="BEGIN:VEVENT\r\nUID:test\r\nEND:VEVENT",
                       dtstart=start, dtend=end, tzid=tzid,
                       all_day=False, rrule=rrule, status=status)


def _store(path=":memory:"):
    return CalendarStore(path)


def test_fresh_store_is_current_version_with_zero_revision():
    store = _store()
    try:
        assert store.get_schema_version() == 2
        assert store.get_revision() == 0
        revision, changed, deleted = store.list_changes(0)
        assert (revision, changed, deleted) == (0, [], [])
    finally:
        store.close()


def test_migration_from_v1_preserves_rows_and_adds_columns(tmp_path):
    path = str(tmp_path / "calendar.db")
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.close()
    store = CalendarStore(path)
    try:
        assert store.get_schema_version() == 2
        assert store.get_revision() == 4
        record = store.get_event("evt-v1")
        assert record.dtstart == 1000
        assert record.tzid == ""
        assert record.status == ""
        calendar = store.get_calendar("local")
        assert calendar.display_name == "On This Phone"
        assert calendar.sigil_tier == 0
    finally:
        store.close()


def test_migration_is_idempotent_and_rejects_newer_schema(tmp_path):
    path = str(tmp_path / "calendar.db")
    store = CalendarStore(path)
    try:
        assert store.migrate() == 2
        assert store.migrate() == 2
    finally:
        store.close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(StoreError):
        CalendarStore(path)


def test_file_and_dir_modes_are_private(tmp_path):
    path = str(tmp_path / "sub" / "calendar.db")
    store = CalendarStore(path)
    try:
        store.create_calendar(CalendarRecord(id="local", display_name="L"))
        store.put_event(_event())
    finally:
        store.close()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    for suffix in ("-wal", "-shm"):
        sidecar = path + suffix
        if os.path.exists(sidecar):
            assert stat.S_IMODE(os.stat(sidecar).st_mode) == 0o600


def test_first_run_creates_missing_parent_dirs(tmp_path):
    path = str(tmp_path / "deep" / "nested" / "calendar.db")
    store = CalendarStore(path)
    try:
        assert os.path.exists(path)
    finally:
        store.close()


def test_wal_mode_and_busy_timeout(tmp_path):
    path = str(tmp_path / "calendar.db")
    store = CalendarStore(path)
    try:
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        timeout = store._conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5000
    finally:
        store.close()


def test_calendar_crud_and_revision_bumps():
    store = _store()
    try:
        first = store.create_calendar(CalendarRecord(id="a", display_name="A"))
        second = store.create_calendar(CalendarRecord(id="b", display_name="B",
                                                      is_visible=False))
        assert (first, second) == (1, 2)
        assert [c.id for c in store.list_calendars()] == ["a", "b"]
        assert store.get_calendar("b").is_visible is False
        updated = store.update_calendar(CalendarRecord(id="b",
                                                       display_name="B2",
                                                       sigil_tier=3))
        assert updated == 3
        assert store.get_calendar("b").sigil_tier == 3
        with pytest.raises(StoreError):
            store.create_calendar(CalendarRecord(id="a", display_name="dup"))
        with pytest.raises(NotFoundError):
            store.get_calendar("missing")
        assert store.delete_calendar("a") == 4
        assert [c.id for c in store.list_calendars()] == ["b"]
        with pytest.raises(NotFoundError):
            store.delete_calendar("a")
    finally:
        store.close()


def test_event_crud_with_overrides_and_tombstones():
    store = _store()
    try:
        store.create_calendar(CalendarRecord(id="local", display_name="L"))
        assert store.put_event(_event(uid="e1", rrule="FREQ=DAILY")) == 2
        assert store.put_event(_event(uid="e1", recurrence_id=5000,
                                      start=5000, end=6000)) == 3
        parent = store.get_event("e1")
        assert parent.rrule == "FREQ=DAILY"
        assert [o.recurrence_id for o in store.overrides_for("e1")] == [5000]
        assert store.put_event(_event(uid="e1", start=1111)) == 4
        assert store.get_event("e1").dtstart == 1111
        assert store.delete_event("e1", recurrence_id=5000) == 5
        assert store.overrides_for("e1") == []
        with pytest.raises(NotFoundError):
            store.delete_event("e1", recurrence_id=5000)
        assert store.delete_series("e1") == 6
        with pytest.raises(NotFoundError):
            store.get_event("e1")
        with pytest.raises(StoreError):
            store.put_event(EventRecord(uid=""))
    finally:
        store.close()


def test_list_since_semantics_are_exclusive():
    store = _store()
    try:
        store.create_calendar(CalendarRecord(id="local", display_name="L"))
        store.put_event(_event(uid="a"))
        store.put_event(_event(uid="b"))
        revision, changed, deleted = store.list_changes(0)
        assert revision == 3
        assert changed == ["a", "b"]
        assert deleted == []
        store.put_event(_event(uid="a", start=9999))
        store.delete_event("b")
        revision, changed, deleted = store.list_changes(3)
        assert revision == 5
        assert changed == ["a"]
        assert deleted == ["b"]
        revision, changed, deleted = store.list_changes(5)
        assert (changed, deleted) == ([], [])
        revision, changed, deleted = store.list_changes(99)
        assert (changed, deleted) == ([], [])
        assert revision == 5
    finally:
        store.close()


def test_delete_calendar_tombstones_its_events():
    store = _store()
    try:
        store.create_calendar(CalendarRecord(id="gone", display_name="G"))
        store.put_event(_event(uid="x", calendar_id="gone"))
        store.put_event(_event(uid="y", calendar_id="gone"))
        revision = store.delete_calendar("gone")
        assert revision == 4
        _, changed, deleted = store.list_changes(0)
        assert changed == []
        assert deleted == ["x", "y"]
    finally:
        store.close()


def test_events_in_window_returns_overlaps_and_recurring():
    store = _store()
    try:
        store.create_calendar(CalendarRecord(id="l1", display_name="A"))
        store.create_calendar(CalendarRecord(id="l2", display_name="B"))
        store.put_event(_event(uid="inside", calendar_id="l1",
                               start=1000, end=2000))
        store.put_event(_event(uid="outside", calendar_id="l1",
                               start=9000, end=9500))
        store.put_event(_event(uid="rec", calendar_id="l1",
                               start=0, end=100, rrule="FREQ=DAILY"))
        store.put_event(_event(uid="other-cal", calendar_id="l2",
                               start=1100, end=1200))
        window = store.events_in_window(1000, 3000)
        assert {e.uid for e in window} == {"inside", "rec", "other-cal"}
        filtered = store.events_in_window(1000, 3000, ["l1"])
        assert {e.uid for e in filtered} == {"inside", "rec"}
        assert store.events_in_window(1000, 3000, []) == []
    finally:
        store.close()


def test_indexed_columns_round_trip():
    store = _store()
    try:
        store.create_calendar(CalendarRecord(id="local", display_name="L"))
        record = EventRecord(uid="full", calendar_id="local",
                             ics="BEGIN:VEVENT", dtstart=111, dtend=222,
                             tzid="America/New_York",
                             tzid_end="America/New_York", all_day=True,
                             rrule="FREQ=YEARLY", status="CONFIRMED")
        store.put_event(record)
        loaded = store.get_event("full")
        assert (loaded.dtstart, loaded.dtend) == (111, 222)
        assert loaded.tzid == "America/New_York"
        assert loaded.tzid_end == "America/New_York"
        assert loaded.all_day is True
        assert loaded.rrule == "FREQ=YEARLY"
        assert loaded.status == "CONFIRMED"
        assert loaded.revision == 2
    finally:
        store.close()


def test_concurrent_writes_stay_consistent():
    store = _store()
    try:
        store.create_calendar(CalendarRecord(id="local", display_name="L"))
        errors: list = []

        def write(n):
            try:
                for i in range(25):
                    store.put_event(_event(uid=f"t{n:d}-{i:d}",
                                           start=n * 100000 + i))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert store.count_events() == 100
        revision, changed, _ = store.list_changes(0)
        assert revision == 101
        assert len(changed) == 100
    finally:
        store.close()


def test_default_db_path_lives_under_share():
    assert default_db_path("/home/user") == \
        "/home/user/.local/share/xx-calendar/calendar.db"
