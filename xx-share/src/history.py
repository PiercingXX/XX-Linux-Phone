"""Received-file history: small atomic JSON log under the state dir.

Pure stdlib, no GTK/Gio: the share window renders these rows in a
Gtk.ListView and the service appends on delivery. Bounded (latest 100)
so a busy receiver cannot grow the file without limit.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

HISTORY_FILENAME = "history.json"
HISTORY_LIMIT = 100


def history_path(state_dir: str | Path | None = None) -> Path:
    base = Path(state_dir) if state_dir else (
        Path.home() / ".local" / "share" / "xx-share")
    return base / HISTORY_FILENAME


def _clean_text(value: object, limit: int = 200) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def make_entry(sender_alias: str, sender_fingerprint: str, file_name: str,
               size: int, dest: str | Path,
               clock: Callable[[], float] | None = None) -> dict:
    now = (clock or time.time)()
    return {
        "received_at": float(now),
        "sender_alias": _clean_text(sender_alias),
        "sender_fingerprint": _clean_text(sender_fingerprint, 128),
        "file_name": _clean_text(file_name) or "file",
        "size": max(0, int(size)),
        "path": str(dest),
    }


def load_entries(path: str | Path) -> list[dict]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    entries = [item for item in raw if isinstance(item, dict)]
    return entries[-HISTORY_LIMIT:]


def save_entries(path: str | Path, entries: list[dict]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entries[-HISTORY_LIMIT:], indent=2) + "\n"
    tmp_path = target.with_name(target.name + ".tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    finally:
        tmp_path.unlink(missing_ok=True)


class HistoryStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else history_path()
        self.entries: list[dict] = load_entries(self.path)

    def append(self, entry: dict) -> list[dict]:
        self.entries.append(dict(entry))
        self.entries = self.entries[-HISTORY_LIMIT:]
        save_entries(self.path, self.entries)
        return list(self.entries)

    def record(self, sender_alias: str, sender_fingerprint: str,
               file_name: str, size: int, dest: str | Path) -> dict:
        entry = make_entry(sender_alias, sender_fingerprint, file_name,
                           size, dest)
        self.append(entry)
        return entry

    def list(self) -> list[dict]:
        return list(reversed(self.entries))

    def clear(self) -> None:
        self.entries = []
        save_entries(self.path, self.entries)
