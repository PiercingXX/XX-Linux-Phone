"""Per-call transcript store: SQLite under a 0700 dir, file kept 0600.

Only call sessions with the opt-in setting write here; playback and
mic sessions never persist. Readers (ListTranscripts/GetTranscript)
run on the D-Bus main thread while the worker thread appends finals,
so every connection is single-file with its own lock.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from xx_captions import config

DB_NAME = "transcripts.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
  id TEXT PRIMARY KEY,
  peer TEXT NOT NULL,
  started INTEGER NOT NULL,
  ended INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lines (
  transcript_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  text TEXT NOT NULL,
  offset_ms INTEGER NOT NULL,
  PRIMARY KEY (transcript_id, idx)
);
"""


class NotFoundError(LookupError):
    pass


def _now_unix() -> int:
    return int(time.time())


class TranscriptStore:
    """SQLite transcript log with strict file modes.

    The database file is chmod 0600 after every open so transcripts
    stay per-user even when umask or a restore loosened the mode.
    """

    def __init__(self, home: Path | None = None,
                 path: Path | None = None) -> None:
        if path is None:
            config.ensure_dirs(home)
            path = config.transcripts_dir(home) / DB_NAME
        else:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(path.parent, 0o700)
            except OSError:
                pass
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    @property
    def path(self) -> Path:
        return self._path

    def begin_call(self, peer: str = "",
                   started: int | None = None) -> str:
        """Open a transcript row; returns its id."""
        call_id = uuid.uuid4().hex
        at = int(started) if started is not None else _now_unix()
        with self._lock:
            self._conn.execute(
                "INSERT INTO transcripts (id, peer, started, ended)"
                " VALUES (?, ?, ?, 0)",
                (call_id, str(peer), at))
            self._conn.commit()
        return call_id

    def add_line(self, call_id: str, text: str,
                 offset_ms: int = 0) -> int:
        """Append one final line; returns its index. Unknown id fails."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("transcript lines must be non-empty text")
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM transcripts WHERE id = ?",
                (call_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"unknown transcript {call_id!r}")
            idx = self._conn.execute(
                "SELECT COALESCE(MAX(idx), -1) FROM lines"
                " WHERE transcript_id = ?",
                (call_id,)).fetchone()[0] + 1
            self._conn.execute(
                "INSERT INTO lines (transcript_id, idx, text, offset_ms)"
                " VALUES (?, ?, ?, ?)",
                (call_id, idx, text, int(offset_ms)))
            self._conn.commit()
            return idx

    def end_call(self, call_id: str, ended: int | None = None) -> None:
        """Stamp the call end; unknown id fails."""
        at = int(ended) if ended is not None else _now_unix()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE transcripts SET ended = ? WHERE id = ?",
                (at, call_id))
            self._conn.commit()
            if cursor.rowcount == 0:
                raise NotFoundError(f"unknown transcript {call_id!r}")

    def list_transcripts(self) -> list[tuple[str, str, int, int]]:
        """(id, peer, started, ended) newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, peer, started, ended FROM transcripts"
                " ORDER BY started DESC, id DESC").fetchall()
        return [(row[0], row[1], int(row[2]), int(row[3])) for row in rows]

    def get_transcript(
            self, call_id: str) -> tuple[str, int, int,
                                        list[tuple[str, int]]]:
        """(peer, started, ended, [(text, offset_ms)]). Unknown id fails."""
        with self._lock:
            row = self._conn.execute(
                "SELECT peer, started, ended FROM transcripts WHERE id = ?",
                (call_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"unknown transcript {call_id!r}")
            lines = self._conn.execute(
                "SELECT text, offset_ms FROM lines WHERE transcript_id = ?"
                " ORDER BY idx ASC",
                (call_id,)).fetchall()
        return (row[0], int(row[1]), int(row[2]),
                [(text, int(offset)) for text, offset in lines])

    def delete_transcript(self, call_id: str) -> bool:
        """Remove one transcript and its lines; True when one existed."""
        with self._lock:
            self._conn.execute("DELETE FROM lines WHERE transcript_id = ?",
                               (call_id,))
            cursor = self._conn.execute(
                "DELETE FROM transcripts WHERE id = ?", (call_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass


__all__ = ["DB_NAME", "NotFoundError", "TranscriptStore"]
