"""SQLite store mirroring the Android Room database v1.

Same 8 tables, columns and indexes as data/db/Entities.kt (schema
app/schemas/.../AudiobookDatabase/1.json): items, item_detail, progress,
pending_sync, downloads, download_files, bookmarks, history. One file
~/.local/share/xx-audiobook/audiobook.db, WAL mode, user_version = 1.

Blocking calls: run them on worker threads, never the GTK main loop.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from enum import Enum

from xx_audiobook import paths as paths_module

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT NOT NULL PRIMARY KEY,
  title TEXT NOT NULL,
  subtitle TEXT,
  authors TEXT NOT NULL DEFAULT '',
  narrators TEXT NOT NULL DEFAULT '',
  series_name TEXT,
  series_sequence TEXT,
  duration_s REAL,
  cover_url TEXT,
  thumb_url TEXT,
  added_at TEXT,
  last_played_at TEXT,
  rating_average REAL,
  rating_count INTEGER NOT NULL DEFAULT 0,
  missing INTEGER NOT NULL DEFAULT 0,
  fetched_at_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS index_items_title ON items (title);
CREATE INDEX IF NOT EXISTS index_items_authors ON items (authors);
CREATE INDEX IF NOT EXISTS index_items_added_at ON items (added_at);
CREATE TABLE IF NOT EXISTS item_detail (
  item_id TEXT NOT NULL PRIMARY KEY,
  json TEXT NOT NULL,
  fetched_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS progress (
  item_id TEXT NOT NULL PRIMARY KEY,
  position_s REAL NOT NULL,
  duration_s REAL,
  is_finished INTEGER NOT NULL DEFAULT 0,
  updated_at_ms INTEGER NOT NULL,
  speed REAL
);
CREATE TABLE IF NOT EXISTS pending_sync (
  item_id TEXT NOT NULL PRIMARY KEY,
  position_s REAL NOT NULL,
  duration_s REAL,
  is_finished INTEGER,
  updated_at_ms INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS downloads (
  item_id TEXT NOT NULL PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL,
  bytes_done INTEGER NOT NULL DEFAULT 0,
  bytes_total INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS download_files (
  item_id TEXT NOT NULL,
  file_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  mime TEXT NOT NULL DEFAULT '',
  local_path TEXT,
  bytes_done INTEGER NOT NULL DEFAULT 0,
  verified INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (item_id, file_id)
);
CREATE INDEX IF NOT EXISTS index_download_files_item_id
  ON download_files (item_id);
CREATE TABLE IF NOT EXISTS bookmarks (
  id TEXT NOT NULL PRIMARY KEY,
  item_id TEXT NOT NULL,
  position_s REAL NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT,
  updated_at TEXT,
  pending TEXT
);
CREATE INDEX IF NOT EXISTS index_bookmarks_item_id ON bookmarks (item_id);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
  item_id TEXT NOT NULL,
  event TEXT NOT NULL,
  position_s REAL NOT NULL,
  speed REAL NOT NULL,
  at_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS index_history_item_id ON history (item_id);
CREATE INDEX IF NOT EXISTS index_history_at_ms ON history (at_ms);
"""


class Sort(Enum):
    TITLE = "title"
    AUTHOR = "author"
    ADDED = "added"
    LAST_PLAYED = "last_played"
    RATING = "rating"


class Filter(Enum):
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"
    NOT_STARTED = "not_started"


class DownloadState(Enum):
    QUEUED = "Queued"
    RUNNING = "Running"
    DONE = "Done"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass
class LibraryQuery:
    q: str | None = None
    sort: Sort = Sort.TITLE
    descending: bool = False
    filter: Filter | None = None
    limit: int = 50
    offset: int = 0
    ids: list[str] | None = None


def _escape_like(text: str) -> str:
    return (text.replace("\\", "\\\\")
            .replace("%", "\\%").replace("_", "\\_"))


def build_library_sql(query: LibraryQuery) -> tuple[str, list]:
    """Pure SQL builder mirroring LibrarySql.kt; values are always bound."""
    args: list = []
    where: list[str] = []
    text = (query.q or "").strip()
    if text:
        where.append(
            "(items.title LIKE ? ESCAPE '\\' OR items.authors LIKE ? ESCAPE '\\'"
            " OR items.narrators LIKE ? ESCAPE '\\'"
            " OR items.series_name LIKE ? ESCAPE '\\')")
        like = "%" + _escape_like(text) + "%"
        args.extend([like] * 4)
    if query.ids is not None:
        if not query.ids:
            where.append("0")
        else:
            where.append(
                "items.id IN (" + ",".join("?" for _ in query.ids) + ")")
            args.extend(query.ids)
    if query.filter == Filter.IN_PROGRESS:
        where.append("(progress.is_finished = 0 AND progress.position_s > 0)")
    elif query.filter == Filter.FINISHED:
        where.append("(progress.is_finished = 1)")
    elif query.filter == Filter.NOT_STARTED:
        where.append("(progress.item_id IS NULL OR (progress.is_finished = 0"
                     " AND progress.position_s <= 0))")
    direction = "DESC" if query.descending else "ASC"
    if query.sort == Sort.TITLE:
        order = f"items.title COLLATE NOCASE {direction}"
    elif query.sort == Sort.AUTHOR:
        order = (f"items.authors COLLATE NOCASE {direction},"
                 " items.title COLLATE NOCASE ASC")
    elif query.sort == Sort.ADDED:
        order = f"items.added_at {direction}"
    elif query.sort == Sort.LAST_PLAYED:
        order = f"COALESCE(progress.updated_at_ms, 0) {direction}"
    else:
        order = (f"COALESCE(items.rating_average, 0) {direction},"
                 f" items.rating_count {direction}")
    sql = ("SELECT items.*, progress.position_s AS p_position_s,"
           " progress.is_finished AS p_is_finished,"
           " progress.updated_at_ms AS p_updated_at_ms"
           " FROM items LEFT JOIN progress ON progress.item_id = items.id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
    args.extend([query.limit, query.offset])
    return sql, args


@dataclass
class Item:
    id: str
    title: str
    subtitle: str | None = None
    authors: str = ""
    narrators: str = ""
    series_name: str | None = None
    series_sequence: str | None = None
    duration_s: float | None = None
    cover_url: str | None = None
    thumb_url: str | None = None
    added_at: str | None = None
    last_played_at: str | None = None
    rating_average: float | None = None
    rating_count: int = 0
    missing: bool = False
    fetched_at_ms: int = 0


@dataclass
class ItemDetail:
    item_id: str
    json: str
    fetched_at_ms: int


@dataclass
class Progress:
    item_id: str
    position_s: float
    duration_s: float | None = None
    is_finished: bool = False
    updated_at_ms: int = 0
    speed: float | None = None


@dataclass
class PendingSync:
    item_id: str
    position_s: float
    duration_s: float | None = None
    is_finished: bool | None = None
    updated_at_ms: int = 0
    attempts: int = 0


@dataclass
class Download:
    item_id: str
    title: str = ""
    state: str = DownloadState.QUEUED.value
    bytes_done: int = 0
    bytes_total: int = 0
    error: str | None = None
    created_at_ms: int = 0
    updated_at_ms: int = 0


@dataclass
class DownloadFile:
    item_id: str
    file_id: str
    idx: int = 0
    size: int = 0
    sha256: str = ""
    mime: str = ""
    local_path: str | None = None
    bytes_done: int = 0
    verified: bool = False


@dataclass
class Bookmark:
    id: str
    item_id: str
    position_s: float
    note: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    pending: str | None = None


@dataclass
class HistoryEvent:
    item_id: str
    event: str
    position_s: float
    speed: float = 1.0
    at_ms: int = 0
    id: int = 0


@dataclass
class ItemWithProgress:
    item: Item
    position_s: float | None = None
    is_finished: bool | None = None
    progress_updated_at_ms: int | None = None


def _bool(value) -> bool:
    return bool(value) if value is not None else False


def _opt_bool(value) -> bool | None:
    return None if value is None else bool(value)


def _row_to_item(row) -> Item:
    return Item(
        id=row["id"], title=row["title"], subtitle=row["subtitle"],
        authors=row["authors"] or "", narrators=row["narrators"] or "",
        series_name=row["series_name"],
        series_sequence=row["series_sequence"],
        duration_s=row["duration_s"], cover_url=row["cover_url"],
        thumb_url=row["thumb_url"], added_at=row["added_at"],
        last_played_at=row["last_played_at"],
        rating_average=row["rating_average"],
        rating_count=row["rating_count"] or 0,
        missing=_bool(row["missing"]),
        fetched_at_ms=row["fetched_at_ms"] or 0,
    )


def _row_to_progress(row) -> Progress:
    return Progress(
        item_id=row["item_id"], position_s=row["position_s"],
        duration_s=row["duration_s"], is_finished=_bool(row["is_finished"]),
        updated_at_ms=row["updated_at_ms"] or 0,
        speed=row["speed"],
    )


def _row_to_pending(row) -> PendingSync:
    return PendingSync(
        item_id=row["item_id"], position_s=row["position_s"],
        duration_s=row["duration_s"],
        is_finished=_opt_bool(row["is_finished"]),
        updated_at_ms=row["updated_at_ms"] or 0,
        attempts=row["attempts"] or 0,
    )


def _row_to_download(row) -> Download:
    return Download(
        item_id=row["item_id"], title=row["title"] or "",
        state=row["state"], bytes_done=row["bytes_done"] or 0,
        bytes_total=row["bytes_total"] or 0, error=row["error"],
        created_at_ms=row["created_at_ms"] or 0,
        updated_at_ms=row["updated_at_ms"] or 0,
    )


def _row_to_download_file(row) -> DownloadFile:
    return DownloadFile(
        item_id=row["item_id"], file_id=row["file_id"], idx=row["idx"] or 0,
        size=row["size"] or 0, sha256=row["sha256"] or "",
        mime=row["mime"] or "", local_path=row["local_path"],
        bytes_done=row["bytes_done"] or 0, verified=_bool(row["verified"]),
    )


def _row_to_bookmark(row) -> Bookmark:
    return Bookmark(
        id=row["id"], item_id=row["item_id"],
        position_s=row["position_s"], note=row["note"] or "",
        created_at=row["created_at"], updated_at=row["updated_at"],
        pending=row["pending"],
    )


def _row_to_history(row) -> HistoryEvent:
    return HistoryEvent(
        id=row["id"], item_id=row["item_id"], event=row["event"],
        position_s=row["position_s"], speed=row["speed"] or 1.0,
        at_ms=row["at_ms"] or 0,
    )


def _row_to_joined(row) -> ItemWithProgress:
    return ItemWithProgress(
        item=_row_to_item(row),
        position_s=row["p_position_s"],
        is_finished=_opt_bool(row["p_is_finished"]),
        progress_updated_at_ms=row["p_updated_at_ms"],
    )


class _Items:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, items: list[Item]) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO items (id, title, subtitle, authors,"
                " narrators, series_name, series_sequence, duration_s,"
                " cover_url, thumb_url, added_at, last_played_at,"
                " rating_average, rating_count, missing, fetched_at_ms)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(i.id, i.title, i.subtitle, i.authors, i.narrators,
                  i.series_name, i.series_sequence, i.duration_s,
                  i.cover_url, i.thumb_url, i.added_at, i.last_played_at,
                  i.rating_average, i.rating_count, int(i.missing),
                  i.fetched_at_ms) for i in items])

    def get(self, item_id: str) -> Item | None:
        row = self._db._conn.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return _row_to_item(row) if row is not None else None

    def count(self) -> int:
        return self._db._conn.execute(
            "SELECT COUNT(*) FROM items").fetchone()[0]

    def query(self, query: LibraryQuery) -> list[ItemWithProgress]:
        sql, args = build_library_sql(query)
        rows = self._db._conn.execute(sql, args).fetchall()
        return [_row_to_joined(r) for r in rows]

    def continue_listening(self, limit: int = 20) -> list[ItemWithProgress]:
        rows = self._db._conn.execute(
            "SELECT items.*, progress.position_s AS p_position_s,"
            " progress.is_finished AS p_is_finished,"
            " progress.updated_at_ms AS p_updated_at_ms"
            " FROM items JOIN progress ON progress.item_id = items.id"
            " WHERE progress.is_finished = 0 AND progress.position_s > 0"
            " ORDER BY progress.updated_at_ms DESC LIMIT ?",
            (limit,)).fetchall()
        return [_row_to_joined(r) for r in rows]

    def clear(self) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.execute("DELETE FROM items")


class _ItemDetails:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, detail: ItemDetail) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO item_detail"
                " (item_id, json, fetched_at_ms) VALUES (?,?,?)",
                (detail.item_id, detail.json, detail.fetched_at_ms))

    def get(self, item_id: str) -> ItemDetail | None:
        row = self._db._conn.execute(
            "SELECT * FROM item_detail WHERE item_id = ?",
            (item_id,)).fetchone()
        if row is None:
            return None
        return ItemDetail(item_id=row["item_id"], json=row["json"],
                          fetched_at_ms=row["fetched_at_ms"])


class _Progress:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, progress: Progress) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO progress (item_id, position_s,"
                " duration_s, is_finished, updated_at_ms, speed)"
                " VALUES (?,?,?,?,?,?)",
                (progress.item_id, progress.position_s,
                 progress.duration_s, int(progress.is_finished),
                 progress.updated_at_ms, progress.speed))

    def get(self, item_id: str) -> Progress | None:
        row = self._db._conn.execute(
            "SELECT * FROM progress WHERE item_id = ?", (item_id,)).fetchone()
        return _row_to_progress(row) if row is not None else None

    def all(self) -> list[Progress]:
        return [_row_to_progress(r) for r in self._db._conn.execute(
            "SELECT * FROM progress").fetchall()]

    def merge_newer(self, incoming: Progress) -> Progress:
        with self._db._lock:
            local = self.get(incoming.item_id)
            if (local is not None
                    and local.updated_at_ms > incoming.updated_at_ms):
                return local
            merged = Progress(
                item_id=incoming.item_id, position_s=incoming.position_s,
                duration_s=incoming.duration_s,
                is_finished=incoming.is_finished,
                updated_at_ms=incoming.updated_at_ms,
                speed=(incoming.speed if incoming.speed is not None
                       else (local.speed if local else None)))
            self.upsert(merged)
            return merged

    def set_speed(self, item_id: str, speed: float) -> int:
        with self._db._lock, self._db._conn as conn:
            cur = conn.execute(
                "UPDATE progress SET speed = ? WHERE item_id = ?",
                (speed, item_id))
            return cur.rowcount


class _PendingSync:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, row: PendingSync) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pending_sync (item_id, position_s,"
                " duration_s, is_finished, updated_at_ms, attempts)"
                " VALUES (?,?,?,?,?,?)",
                (row.item_id, row.position_s, row.duration_s,
                 (None if row.is_finished is None
                  else int(row.is_finished)),
                 row.updated_at_ms, row.attempts))

    def all(self) -> list[PendingSync]:
        return [_row_to_pending(r) for r in self._db._conn.execute(
            "SELECT * FROM pending_sync"
            " ORDER BY updated_at_ms ASC").fetchall()]

    def get(self, item_id: str) -> PendingSync | None:
        row = self._db._conn.execute(
            "SELECT * FROM pending_sync WHERE item_id = ?",
            (item_id,)).fetchone()
        return _row_to_pending(row) if row is not None else None

    def count(self) -> int:
        return self._db._conn.execute(
            "SELECT COUNT(*) FROM pending_sync").fetchone()[0]

    def ack(self, item_id: str, updated_at_ms: int) -> int:
        with self._db._lock, self._db._conn as conn:
            cur = conn.execute(
                "DELETE FROM pending_sync"
                " WHERE item_id = ? AND updated_at_ms <= ?",
                (item_id, updated_at_ms))
            return cur.rowcount

    def bump_attempts(self, item_id: str) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.execute(
                "UPDATE pending_sync SET attempts = attempts + 1"
                " WHERE item_id = ?", (item_id,))


class _Downloads:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, download: Download) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO downloads (item_id, title, state,"
                " bytes_done, bytes_total, error, created_at_ms,"
                " updated_at_ms) VALUES (?,?,?,?,?,?,?,?)",
                (download.item_id, download.title, download.state,
                 download.bytes_done, download.bytes_total, download.error,
                 download.created_at_ms, download.updated_at_ms))

    def upsert_files(self, files: list[DownloadFile]) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO download_files (item_id, file_id,"
                " idx, size, sha256, mime, local_path, bytes_done, verified)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                [(f.item_id, f.file_id, f.idx, f.size, f.sha256, f.mime,
                  f.local_path, f.bytes_done, int(f.verified))
                 for f in files])

    def all(self) -> list[Download]:
        return [_row_to_download(r) for r in self._db._conn.execute(
            "SELECT * FROM downloads"
            " ORDER BY created_at_ms DESC").fetchall()]

    def get(self, item_id: str) -> Download | None:
        row = self._db._conn.execute(
            "SELECT * FROM downloads WHERE item_id = ?",
            (item_id,)).fetchone()
        return _row_to_download(row) if row is not None else None

    def files(self, item_id: str) -> list[DownloadFile]:
        return [_row_to_download_file(r) for r in self._db._conn.execute(
            "SELECT * FROM download_files WHERE item_id = ? ORDER BY idx",
            (item_id,)).fetchall()]

    def set_state(self, item_id: str, state: str, error: str | None,
                  at_ms: int) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.execute(
                "UPDATE downloads SET state = ?, error = ?,"
                " updated_at_ms = ? WHERE item_id = ?",
                (state, error, at_ms, item_id))

    def delete(self, item_id: str) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.execute("DELETE FROM download_files WHERE item_id = ?",
                         (item_id,))
            conn.execute("DELETE FROM downloads WHERE item_id = ?",
                         (item_id,))

    def bytes_used(self) -> int:
        row = self._db._conn.execute(
            "SELECT COALESCE(SUM(bytes_done), 0) FROM download_files"
        ).fetchone()
        return row[0] or 0


class _Bookmarks:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, bookmarks: list[Bookmark]) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO bookmarks (id, item_id, position_s,"
                " note, created_at, updated_at, pending)"
                " VALUES (?,?,?,?,?,?,?)",
                [(b.id, b.item_id, b.position_s, b.note, b.created_at,
                  b.updated_at, b.pending) for b in bookmarks])

    def for_item(self, item_id: str) -> list[Bookmark]:
        return [_row_to_bookmark(r) for r in self._db._conn.execute(
            "SELECT * FROM bookmarks WHERE item_id = ?"
            " AND (pending IS NULL OR pending != 'delete')"
            " ORDER BY position_s", (item_id,)).fetchall()]

    def pending(self) -> list[Bookmark]:
        return [_row_to_bookmark(r) for r in self._db._conn.execute(
            "SELECT * FROM bookmarks WHERE pending IS NOT NULL").fetchall()]

    def delete(self, bookmark_id: str) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.execute("DELETE FROM bookmarks WHERE id = ?",
                         (bookmark_id,))

    def replace_synced(self, item_id: str, rows: list[Bookmark]) -> None:
        with self._db._lock, self._db._conn as conn:
            conn.execute(
                "DELETE FROM bookmarks"
                " WHERE item_id = ? AND pending IS NULL", (item_id,))
            conn.executemany(
                "INSERT OR REPLACE INTO bookmarks (id, item_id, position_s,"
                " note, created_at, updated_at, pending)"
                " VALUES (?,?,?,?,?,?,?)",
                [(b.id, b.item_id, b.position_s, b.note, b.created_at,
                  b.updated_at, b.pending) for b in rows])


class _History:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, event: HistoryEvent) -> int:
        with self._db._lock, self._db._conn as conn:
            cur = conn.execute(
                "INSERT INTO history (item_id, event, position_s, speed,"
                " at_ms) VALUES (?,?,?,?,?)",
                (event.item_id, event.event, event.position_s, event.speed,
                 event.at_ms))
            return cur.lastrowid or 0

    def recent(self, limit: int = 500) -> list[HistoryEvent]:
        return [_row_to_history(r) for r in self._db._conn.execute(
            "SELECT * FROM history ORDER BY at_ms DESC LIMIT ?",
            (limit,)).fetchall()]

    def last(self, item_id: str) -> HistoryEvent | None:
        row = self._db._conn.execute(
            "SELECT * FROM history WHERE item_id = ?"
            " ORDER BY at_ms DESC LIMIT 1", (item_id,)).fetchone()
        return _row_to_history(row) if row is not None else None


class Database:
    """One sqlite3 database; mirrors AudiobookDatabase (Room v1)."""

    def __init__(self, path=None) -> None:
        target = (paths_module.ensure_parent_for(
            paths_module.db_path()) if path is None else
            paths_module.ensure_parent_for(path))
        self._path = str(target)
        self._lock = threading.RLock()
        # Shared with the player service worker thread (single writer);
        # writes already serialize on _lock.
        self._conn = sqlite3.connect(
            self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self._items = _Items(self)
        self._details = _ItemDetails(self)
        self._progress = _Progress(self)
        self._pending = _PendingSync(self)
        self._downloads = _Downloads(self)
        self._bookmarks = _Bookmarks(self)
        self._history = _History(self)

    @classmethod
    def in_memory(cls) -> Database:
        db = cls.__new__(cls)
        db._path = ":memory:"
        db._lock = threading.RLock()
        db._conn = sqlite3.connect(":memory:", check_same_thread=False)
        db._conn.row_factory = sqlite3.Row
        with db._lock, db._conn as conn:
            conn.execute("PRAGMA journal_mode=MEMORY")
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db._items = _Items(db)
        db._details = _ItemDetails(db)
        db._progress = _Progress(db)
        db._pending = _PendingSync(db)
        db._downloads = _Downloads(db)
        db._bookmarks = _Bookmarks(db)
        db._history = _History(db)
        return db

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def clear_all(self) -> None:
        with self._lock, self._conn as conn:
            for table in ("items", "item_detail", "progress",
                          "pending_sync", "downloads", "download_files",
                          "bookmarks", "history"):
                conn.execute(f"DELETE FROM {table}")

    def items(self) -> _Items:
        return self._items

    def details(self) -> _ItemDetails:
        return self._details

    def progress(self) -> _Progress:
        return self._progress

    def pending(self) -> _PendingSync:
        return self._pending

    def downloads(self) -> _Downloads:
        return self._downloads

    def bookmarks(self) -> _Bookmarks:
        return self._bookmarks

    def history(self) -> _History:
        return self._history
