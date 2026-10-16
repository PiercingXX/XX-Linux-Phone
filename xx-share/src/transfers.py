"""Outgoing transfer registry: ids, progress, cooperative cancel.

The GTK send flow runs on a worker thread; the Cancel button (or the
session-bus ``Cancel`` method) only flips a flag, and the worker checks
it between files and inside the upload body reader. GLib-free so tests
drive it without a display.
"""
from __future__ import annotations

import secrets
import threading


class TransferCancelled(Exception):
    pass


def new_transfer_id() -> str:
    return "tx-" + secrets.token_urlsafe(8)


class TransferManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled: set[str] = set()
        self._active: dict[str, dict] = {}
        self._progress: dict[str, dict] = {}

    def start(self, fingerprint: str, files: list[str],
              transfer_id: str | None = None) -> str:
        tid = transfer_id or new_transfer_id()
        with self._lock:
            self._active[tid] = {
                "fingerprint": fingerprint,
                "files": list(files),
            }
            self._cancelled.discard(tid)
        return tid

    def request_cancel(self, transfer_id: str) -> bool:
        with self._lock:
            if transfer_id not in self._active:
                return False
            self._cancelled.add(transfer_id)
            return True

    def is_cancelled(self, transfer_id: str) -> bool:
        with self._lock:
            return transfer_id in self._cancelled

    def check(self, transfer_id: str) -> None:
        if self.is_cancelled(transfer_id):
            raise TransferCancelled(f"transfer {transfer_id} was cancelled")

    def finish(self, transfer_id: str) -> None:
        with self._lock:
            self._active.pop(transfer_id, None)
            self._cancelled.discard(transfer_id)
            self._progress.pop(transfer_id, None)

    def record_progress(self, transfer_id: str, file_id: str,
                        sent: int, total: int) -> dict:
        with self._lock:
            state = self._progress.setdefault(transfer_id, {})
            state[file_id] = {"sent": int(sent), "total": int(total)}
            return dict(state)

    def describe(self, transfer_id: str) -> dict | None:
        with self._lock:
            info = self._active.get(transfer_id)
            return dict(info) if info else None
