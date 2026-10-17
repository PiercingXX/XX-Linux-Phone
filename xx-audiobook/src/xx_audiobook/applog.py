"""On-phone log ring: file under the state dir plus in-memory lines.

Secrets are redacted before any write or share. Mirrors log/AppLog.kt and
the xx-vitals applog shape (bounded ring, capped reads).
"""

from __future__ import annotations

import re
import threading
from collections import deque

RING_SIZE = 300

_lock = threading.Lock()
_ring: deque[str] = deque(maxlen=RING_SIZE)
_logger = None

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._\-~+/=]+")
_SECRET_RE = re.compile(
    r"((?:password|passwd|secret|token|otp|pin)\s*[=:]\s*)(\S+)",
    re.IGNORECASE,
)
_JSON_SECRET_RE = re.compile(
    r'("(?:password|passwd|secret|token|otp|pin)"\s*:\s*")([^"]*)"',
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact(line: str) -> str:
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", line)
    redacted = _SECRET_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _JSON_SECRET_RE.sub("\\g<1>[REDACTED]\"", redacted)
    if "otpauth://" in redacted:
        redacted = re.sub(r"otpauth://\S+", "otpauth://[REDACTED]", redacted)
    return _EMAIL_RE.sub("[REDACTED]", redacted)


class AppLog:
    def __init__(self) -> None:
        self._file = None

    def _log(self, level: str, message: str, *args) -> None:
        try:
            text = message % args if args else message
        except (TypeError, ValueError):
            text = message
        line = f"{level} {redact(text)}"
        with _lock:
            _ring.append(line)
        if self._file is not None:
            try:
                self._file.write(line + "\n")
                self._file.flush()
            except OSError:
                pass

    def info(self, message: str, *args) -> None:
        self._log("INFO", message, *args)

    def warning(self, message: str, *args) -> None:
        self._log("WARNING", message, *args)

    def error(self, message: str, *args) -> None:
        self._log("ERROR", message, *args)


def get_logger() -> AppLog:
    global _logger
    with _lock:
        if _logger is None:
            _logger = AppLog()
            try:
                from xx_audiobook import paths as paths_module
                paths_module.ensure_app_dirs()
                _logger._file = open(str(paths_module.log_file()),
                                     "a", encoding="utf-8")
            except OSError:
                _logger._file = None
        return _logger


def read_recent(limit: int = 300) -> list[str]:
    with _lock:
        lines = list(_ring)
    return lines[-limit:]


def reset() -> None:
    global _logger
    with _lock:
        _ring.clear()
        if _logger is not None and _logger._file is not None:
            try:
                _logger._file.close()
            except OSError:
                pass
        _logger = None
