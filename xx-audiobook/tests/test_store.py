"""Room parity: every Android column is present with the right shape.

Expectations are hardcoded from data/db/Entities.kt and the Room schema
export (AudiobookDatabase 1.json), so the test runs with no reference
checkout and never skips. DAO behaviour mirrors AudiobookDaoTest.
"""

import sqlite3

from xx_audiobook.store import (
    Bookmark,
    Database,
    Download,
    DownloadFile,
    DownloadState,
    Filter,
    HistoryEvent,
    Item,
    ItemDetail,
    LibraryQuery,
    PendingSync,
    Progress,
    Sort,
)

# table -> [(column, affinity, not_null)] from the Room 1.json export.
EXPECTED = {
    "items": [
        ("id", "TEXT", True), ("title", "TEXT", True),
        ("subtitle", "TEXT", False), ("authors", "TEXT", True),
        ("narrators", "TEXT", True), ("series_name", "TEXT", False),
        ("series_sequence", "TEXT", False), ("duration_s", "REAL", False),
        ("cover_url", "TEXT", False), ("thumb_url", "TEXT", False),
        ("added_at", "TEXT", False), ("last_played_at", "TEXT", False),
        ("rating_average", "REAL", False),
        ("rating_count", "INTEGER", True), ("missing", "INTEGER", True),
        ("fetched_at_ms", "INTEGER", True),
    ],
    "item_detail": [
        ("item_id", "TEXT", True), ("json", "TEXT", True),
        ("fetched_at_ms", "INTEGER", True),
    ],
    "progress": [
        ("item_id", "TEXT", True), ("position_s", "REAL", True),
        ("duration_s", "REAL", False), ("is_finished", "INTEGER", True),
        ("updated_at_ms", "INTEGER", True), ("speed", "REAL", False),
    ],
    "pending_sync": [
        ("item_id", "TEXT", True), ("position_s", "REAL", True),
        ("duration_s", "REAL", False), ("is_finished", "INTEGER", False),
        ("updated_at_ms", "INTEGER", True), ("attempts", "INTEGER", True),
    ],
    "downloads": [
        ("item_id", "TEXT", True), ("title", "TEXT", True),
        ("state", "TEXT", True), ("bytes_done", "INTEGER", True),
        ("bytes_total", "INTEGER", True), ("error", "TEXT", False),
        ("created_at_ms", "INTEGER", True),
        ("updated_at_ms", "INTEGER", True),
    ],
    "download_files": [
        ("item_id", "TEXT", True), ("file_id", "TEXT", True),
        ("idx", "INTEGER", True), ("size", "INTEGER", True),
        ("sha256", "TEXT", True), ("mime", "TEXT", True),
        ("local_path", "TEXT", False), ("bytes_done", "INTEGER", True),
        ("verified", "INTEGER", True),
    ],
    "bookmarks": [
        ("id", "TEXT", True), ("item_id", "TEXT", True),
        ("position_s", "REAL", True), ("note", "TEXT", True),
        ("created_at", "TEXT", False), ("updated_at", "TEXT", False),
        ("pending", "TEXT", False),
    ],
    "history": [
        ("id", "INTEGER", True), ("item_id", "TEXT", True),
        ("event", "TEXT", True), ("position_s", "REAL", True),
        ("speed", "REAL", True), ("at_ms", "INTEGER", True),
    ],
}

EXPECTED_PK = {
    "items": ["id"],
    "item_detail": ["item_id"],
    "progress": ["item_id"],
    "pending_sync": ["item_id"],
    "downloads": ["item_id"],
    "download_files": ["item_id", "file_id"],
    "bookmarks": ["id"],
    "history": ["id"],
}

EXPECTED_INDEXES = {
    "items": {"index_items_title", "index_items_authors",
              "index_items_added_at"},
    "download_files": {"index_download_files_item_id"},
    "bookmarks": {"index_bookmarks_item_id"},
    "history": {"index_history_item_id", "index_history_at_ms"},
}


def _columns(conn, table):
    return conn.execute(f"PRAGMA table_info({table})").fetchall()


def test_every_android_column_present():
    db = Database.in_memory()
    try:
        tables = {r[0] for r in db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert set(EXPECTED) <= tables
        for table, columns in EXPECTED.items():
            info = {r[1]: r for r in _columns(db._conn, table)}
            assert [c for c, _t, _n in columns] == [r[1] for r in
                                                    _columns(db._conn,
                                                             table)], table
            for name, affinity, not_null in columns:
                row = info[name]
                assert row[2] == affinity, (table, name)
                if row[5] == 0:
                    assert bool(row[3]) == not_null, (table, name)
            pk = sorted(r[1] for r in _columns(db._conn, table)
                        if r[5] > 0)
            assert pk == sorted(EXPECTED_PK[table]), table
    finally:
        db.close()


def test_android_indexes_present():
    db = Database.in_memory()
    try:
        for table, names in EXPECTED_INDEXES.items():
            have = {r[1] for r in db._conn.execute(
                f"PRAGMA index_list({table})").fetchall()}
            assert names <= have, (table, have)
    finally:
        db.close()


def test_file_db_is_wal_version_1(tmp_path):
    db = Database(tmp_path / "audiobook.db")
    try:
        assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db._conn.execute(
            "PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        db.close()


def _item(item_id, title, authors="", added=None, rating=None):
    return Item(id=item_id, title=title, authors=authors, added_at=added,
                rating_average=rating, duration_s=100.0)


def test_items_search_sort_filter_paging():
    db = Database.in_memory()
    try:
        db.items().upsert([
            _item("a", "Alpha", "Zed", "2026-01-01", 3.0),
            _item("b", "beta", "Ann", "2026-03-01", 5.0),
            _item("c", "Gamma 100%", "Moe", "2026-02-01"),
        ])
        db.progress().upsert(Progress("a", 50.0, 100.0, False, 10))
        db.progress().upsert(Progress("b", 100.0, 100.0, True, 20))

        def ids(query):
            return [r.item.id for r in db.items().query(query)]

        assert ids(LibraryQuery()) == ["a", "b", "c"]
        assert ids(LibraryQuery(sort=Sort.AUTHOR)) == ["b", "c", "a"]
        assert ids(LibraryQuery(sort=Sort.ADDED, descending=True)) == [
            "b", "c", "a"]
        assert ids(LibraryQuery(sort=Sort.RATING, descending=True)) == [
            "b", "a", "c"]
        assert ids(LibraryQuery(sort=Sort.LAST_PLAYED, descending=True)) == [
            "b", "a", "c"]
        assert ids(LibraryQuery(filter=Filter.IN_PROGRESS)) == ["a"]
        assert ids(LibraryQuery(filter=Filter.FINISHED)) == ["b"]
        assert ids(LibraryQuery(filter=Filter.NOT_STARTED)) == ["c"]
        assert ids(LibraryQuery(q="ann")) == ["b"]
        assert ids(LibraryQuery(q="100%")) == ["c"]
        assert ids(LibraryQuery(q="_")) == []
        assert ids(LibraryQuery(limit=1, offset=1)) == ["b"]
        assert ids(LibraryQuery(ids=[])) == []
        assert ids(LibraryQuery(ids=["c", "a"])) == ["a", "c"]

        row = db.items().query(LibraryQuery(q="alpha"))[0]
        assert row.position_s == 50.0
        assert [r.item.id for r in db.items().continue_listening()] == ["a"]
    finally:
        db.close()


def test_item_detail_cache():
    db = Database.in_memory()
    try:
        db.details().upsert(ItemDetail("a", '{"id":"a"}', 1))
        assert db.details().get("a").json == '{"id":"a"}'
        assert db.details().get("a").fetched_at_ms == 1
        assert db.details().get("missing") is None
    finally:
        db.close()


def test_progress_merge_is_lww_and_keeps_speed():
    db = Database.in_memory()
    try:
        db.progress().upsert(Progress("a", 10.0, 100.0, False, 2000, 1.5))
        older = db.progress().merge_newer(
            Progress("a", 99.0, 100.0, False, 1000))
        assert older.position_s == 10.0
        newer = db.progress().merge_newer(
            Progress("a", 42.0, 100.0, False, 3000))
        assert newer.position_s == 42.0
        assert db.progress().get("a").speed == 1.5
        assert db.progress().set_speed("a", 2.0) == 1
        assert db.progress().get("a").speed == 2.0
        assert db.progress().all()[0].item_id == "a"
    finally:
        db.close()


def test_pending_sync_ack_only_what_was_sent():
    db = Database.in_memory()
    try:
        db.pending().upsert(PendingSync("a", 10.0, None, None, 1000))
        db.pending().upsert(PendingSync("a", 20.0, None, None, 2000))
        db.pending().upsert(
            PendingSync("b", 5.0, None, True, 1500))
        assert len(db.pending().all()) == 2
        assert db.pending().count() == 2
        assert db.pending().ack("a", 1000) == 0
        assert db.pending().ack("a", 2000) == 1
        db.pending().bump_attempts("b")
        assert db.pending().all()[0].attempts == 1
        assert db.pending().get("b").is_finished is True
    finally:
        db.close()


def test_downloads_and_files():
    db = Database.in_memory()
    try:
        db.downloads().upsert(Download("a", "Alpha", "Queued", 0, 300,
                                       None, 1, 1))
        db.downloads().upsert_files([
            DownloadFile("a", "f2", 1, 200, "bb", bytes_done=50),
            DownloadFile("a", "f1", 0, 100, "aa", bytes_done=100,
                         verified=True, local_path="/x"),
        ])
        assert [f.file_id for f in db.downloads().files("a")] == [
            "f1", "f2"]
        assert db.downloads().bytes_used() == 150
        db.downloads().set_state("a", DownloadState.FAILED.value,
                                 "disk full", 2)
        assert db.downloads().get("a").error == "disk full"
        assert len(db.downloads().all()) == 1
        db.downloads().delete("a")
        assert db.downloads().get("a") is None
        assert db.downloads().files("a") == []
        assert db.downloads().all() == []
    finally:
        db.close()


def test_bookmarks_keep_pending_across_refresh():
    db = Database.in_memory()
    try:
        db.bookmarks().upsert([
            Bookmark("s1", "a", 10.0),
            Bookmark("local", "a", 5.0, pending="create"),
            Bookmark("gone", "a", 7.0, pending="delete"),
        ])
        db.bookmarks().replace_synced("a", [Bookmark("s2", "a", 20.0)])
        assert [b.id for b in db.bookmarks().for_item("a")] == [
            "local", "s2"]
        assert {b.id for b in db.bookmarks().pending()} == {
            "local", "gone"}
        db.bookmarks().delete("gone")
        assert len(db.bookmarks().pending()) == 1
    finally:
        db.close()


def test_history():
    db = Database.in_memory()
    try:
        db.history().insert(
            HistoryEvent(item_id="a", event="play", position_s=1.0, at_ms=1))
        db.history().insert(
            HistoryEvent(item_id="a", event="pause", position_s=9.0,
                         speed=1.25, at_ms=2))
        assert db.history().last("a").event == "pause"
        assert len(db.history().recent()) == 2
        assert db.history().last("missing") is None
    finally:
        db.close()


def test_clear_all_wipes_every_table():
    db = Database.in_memory()
    try:
        db.items().upsert([_item("a", "Alpha")])
        db.progress().upsert(Progress("a", 1.0, None, False, 1))
        db.clear_all()
        assert db.items().count() == 0
        assert db.progress().all() == []
    finally:
        db.close()


def test_sqlite_roundtrip_through_file(tmp_path):
    path = tmp_path / "sub" / "audiobook.db"
    db = Database(path)
    try:
        db.items().upsert([_item("a", "Alpha")])
        assert path.is_file()
    finally:
        db.close()
    reopened = sqlite3.connect(str(path))
    try:
        assert reopened.execute(
            "SELECT title FROM items WHERE id='a'").fetchone()[0] == "Alpha"
    finally:
        reopened.close()
