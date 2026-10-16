"""On-device log for XX Email (stdlib only, no GTK).

Ports log/AppLog.kt: a process-wide ring (300 lines) plus a rotating
file under XDG_STATE_HOME/xx-email/xx-email.log (256 KiB cap, one
rotated copy). No network, no crash-reporting SDK — the logs page
copies the dump from the phone.

Callers must not log message bodies or attachment bytes; the
redaction filter below additionally scrubs tokens, auth headers,
OAuth codes, and addresses at emit time so a copied dump never
carries a grant. Redaction is idempotent, so sharing re-applies it.

Threading: emit() only appends to the ring and one small file write.
Call it from worker threads, never expecting the GTK loop to flush.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

RING_SIZE = 300
FILE_CAP_BYTES = 256 * 1024
LOG_NAME = "xx-email.log"
PREV_NAME = "xx-email.log.1"

_LEVELS = ("D", "I", "W", "E")

_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/=]+")
_TOKEN_RE = re.compile(r"(?i)\btoken\s+[A-Za-z0-9\-._~+/=]{8,}")
_AUTH_HEADER_RE = re.compile(r"(?im)^([A-Za-z-]*authorization\s*:\s*)\S.*$")
_SECRET_RE = re.compile(
    r"(?i)(refresh_token|access_token|id_token|client_secret)"
    r"(\"\s*:\s*\"|\s*[:=]\s*\"?)([^\"&,\s}]+)")
_CODE_RE = re.compile(r"([?&]code=)[^&\s]+")
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact(text: str) -> str:
    """Scrub grants and addresses from one log line (idempotent)."""
    if not text:
        return ""
    scrubbed = _BEARER_RE.sub("Bearer [REDACTED]", text)
    scrubbed = _TOKEN_RE.sub("token [REDACTED]", scrubbed)
    scrubbed = _AUTH_HEADER_RE.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = _SECRET_RE.sub(r"\1\2[REDACTED]", scrubbed)
    scrubbed = _CODE_RE.sub(r"\1[REDACTED]", scrubbed)

    def _mask(match: re.Match) -> str:
        address = match.group(0)
        _, _, domain = address.partition("@")
        return "***@" + domain

    return _EMAIL_RE.sub(_mask, scrubbed)


def _format_line(when_ms: int, level: str, tag: str,
                 message: str) -> str:
    stamp = time.strftime("%m-%d %H:%M:%S",
                          time.localtime(when_ms / 1000))
    return f"{stamp} {level} {tag} {message}"


class AppLog:
    """Ring + file logger; one instance per process via get()."""

    def __init__(self, state_dir: str | os.PathLike | None = None
                 ) -> None:
        self._lock = threading.Lock()
        self._ring: list[str] = []
        self._dir = Path(state_dir) if state_dir is not None else None

    def init(self, state_dir: str | os.PathLike) -> None:
        """Point the file side at a state dir (created mode 0700)."""
        target = Path(state_dir)
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass
        with self._lock:
            self._dir = target

    def reset_for_test(self) -> None:
        with self._lock:
            self._ring = []
            self._dir = None

    def emit(self, level: str, tag: str, message: str) -> None:
        """Record one redacted line in the ring and the file."""
        if level not in _LEVELS:
            level = "I"
        line = _format_line(int(time.time() * 1000), level,
                            (tag or "app").strip() or "app",
                            redact(message or ""))
        with self._lock:
            self._ring.append(line)
            while len(self._ring) > RING_SIZE:
                del self._ring[0]
            directory = self._dir
        if directory is not None:
            self._append_file(directory, line)

    def d(self, tag: str, message: str) -> None:
        self.emit("D", tag, message)

    def i(self, tag: str, message: str) -> None:
        self.emit("I", tag, message)

    def w(self, tag: str, message: str) -> None:
        self.emit("W", tag, message)

    def e(self, tag: str, message: str) -> None:
        self.emit("E", tag, message)

    def dump(self) -> str:
        """The ring, oldest first."""
        with self._lock:
            return "\n".join(self._ring)

    def tail(self, lines: int = 200) -> str:
        """Last `lines` of the log file ("" when never initialized)."""
        with self._lock:
            directory = self._dir
        if directory is None:
            return ""
        try:
            raw = (directory / LOG_NAME).read_text(encoding="utf-8",
                                                   errors="replace")
        except OSError:
            return ""
        return "\n".join(raw.splitlines()[-max(1, lines):])

    def share_text(self, tail_lines: int = 200) -> str:
        """Redacted ring plus file tail, ready to copy (never sent)."""
        parts = [redact(self.dump())]
        tail_text = redact(self.tail(tail_lines))
        if tail_text:
            parts += ["--- file ---", tail_text]
        return "\n".join(part for part in parts if part).strip()

    def log_path(self) -> Path | None:
        with self._lock:
            return self._dir / LOG_NAME if self._dir else None

    def _append_file(self, directory: Path, line: str) -> None:
        try:
            target = directory / LOG_NAME
            if target.is_file() and \
                    target.stat().st_size > FILE_CAP_BYTES:
                previous = directory / PREV_NAME
                try:
                    if previous.exists():
                        previous.unlink()
                    target.rename(previous)
                except OSError:
                    pass
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


_LOG = AppLog()


def get() -> AppLog:
    """The process-wide logger."""
    return _LOG


def default_log_path() -> Path:
    """State-dir log file, creating the dir mode 0700 first."""
    from xx_email import config as config_mod

    directory = config_mod.state_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory / LOG_NAME


def init_default() -> AppLog:
    """Point the shared logger at the XDG state dir (idempotent)."""
    _LOG.init(default_log_path().parent)
    return _LOG


def write_crash_marker(error: BaseException) -> None:
    """Persist one crash line through the shared logger."""
    try:
        _LOG.e("crash", f"{type(error).__name__}: {error}")
    except Exception:
        pass
