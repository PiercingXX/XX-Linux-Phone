"""On-phone log: stdlib logging to the state file plus an in-memory ring.

Nothing is uploaded; Profile copies recent lines to the clipboard and shows
the log file path as text.
"""

from __future__ import annotations

import logging
import re
from collections import deque

from xx_vitals.paths import ensure_parent_for, log_file

RING_SIZE = 500

_ring: deque[str] = deque(maxlen=RING_SIZE)
_configured = False

_REDACTIONS = (
    (re.compile(r"Bearer\s+[^\s\"']+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)(authorization\s*:\s*)(?:Bearer\s+)?[^\s\"']+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(password\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\"password\"\s*:\s*)\"[^\"]*\""), r'\1"[REDACTED]"'),
    (re.compile(r"(?i)((?:access[_-]?token|session[_-]?token|fabric[_-]?token|secret)\s*[=:]\s*)"
                r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"), r"\1[REDACTED]"),
)


def redact(text: str) -> str:
    """Scrub credential material so secrets never reach logs."""
    scrubbed = str(text)
    for pattern, replacement in _REDACTIONS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _ring.append(self.format(record))


def get_logger(name: str = "xx-vitals") -> logging.Logger:
    """Return the app logger, writing to the state file and the ring."""
    global _configured
    logger = logging.getLogger(name)
    if _configured:
        return logger
    ensure_parent_for(log_file())
    file_handler = logging.FileHandler(str(log_file()), encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    ring_handler = _RingHandler()
    ring_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    logger.addHandler(ring_handler)
    logger.addFilter(_RedactingFilter())
    logger.setLevel(logging.INFO)
    _configured = True
    return logger


def read_recent(limit: int = 200) -> list[str]:
    """Newest-last ring lines, capped at limit."""
    if limit <= 0:
        return []
    return list(_ring)[-limit:]


def log_view_lines(limit: int = 200) -> list[str]:
    """Newest-last lines for the logs view, app and collector alike.

    Both processes append to the same state file, so its tail carries
    every recent line while this process's ring only has its own.
    Falls back to the ring when the file is missing or unreadable.
    """
    if limit <= 0:
        return []
    try:
        raw = log_file().read_bytes()
    except OSError:
        return read_recent(limit)
    if len(raw) > 131072:
        raw = raw[-131072:]
    lines = raw.decode("utf-8", "replace").splitlines()
    return lines[-limit:]


def reset() -> None:
    """Drop handlers and clear the ring. Tests only."""
    global _configured
    logger = logging.getLogger("xx-vitals")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass
    _ring.clear()
    _configured = False
