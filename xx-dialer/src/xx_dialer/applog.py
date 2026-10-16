"""App diagnostics log: rotated text logs plus redacted reads.

Writes to ``~/.local/share/xx-dialer/logs/xx-dialer.log`` (stdlib
only, never raises — diagnostics must not break the app). The Logs
page reads through :func:`redact`, which masks long digit runs so
phone numbers never reach the screen.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

APP_DIR_NAME = "xx-dialer"
LOG_NAME = "xx-dialer.log"
MAX_BYTES = 64 * 1024
KEEP_ROTATED = 3

_DIGITS = re.compile(r"\d{7,}")


def log_dir(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return base / ".local" / "share" / APP_DIR_NAME / "logs"


def redact(text: str) -> str:
    """Mask digit runs of 7+ (phone numbers), keeping the last 2.

    Deterministic and total: every long run is masked wherever it
    appears — bodies, ids, paths — so the Logs page can show raw log
    text without leaking numbers. Short runs (counts, years, ids)
    pass through untouched.
    """
    def _mask(match: re.Match) -> str:
        run = match.group(0)
        return "*" * (len(run) - 2) + run[-2:]

    return _DIGITS.sub(_mask, text or "")


def write(tag: str, message: str, home: Path | None = None) -> None:
    """Append one timestamped line; rotate past the size cap."""
    try:
        directory = log_dir(home)
        directory.mkdir(mode=0x1C0, parents=True, exist_ok=True)
        target = directory / LOG_NAME
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        with target.open("a", encoding="utf-8") as handle:
            handle.write("%s %s %s\n" % (stamp, tag, message))
        if target.stat().st_size > MAX_BYTES:
            _rotate(directory)
    except Exception:
        pass


def _rotate(directory: Path) -> None:
    try:
        oldest = directory / ("%s.%d" % (LOG_NAME, KEEP_ROTATED))
        try:
            oldest.unlink()
        except OSError:
            pass
        for index in range(KEEP_ROTATED - 1, 0, -1):
            src = directory / ("%s.%d" % (LOG_NAME, index))
            dst = directory / ("%s.%d" % (LOG_NAME, index + 1))
            try:
                src.rename(dst)
            except OSError:
                pass
        (directory / LOG_NAME).rename(directory / (LOG_NAME + ".1"))
    except Exception:
        pass


def list_logs(home: Path | None = None) -> list[tuple[str, int, float]]:
    """(name, size, mtime) for each log file, newest first; never raises."""
    try:
        directory = log_dir(home)
        rows = []
        for path in sorted(directory.glob(LOG_NAME + "*")):
            try:
                stat = path.stat()
            except OSError:
                continue
            if path.is_file():
                rows.append((path.name, stat.st_size, stat.st_mtime))
        rows.sort(key=lambda row: row[2], reverse=True)
        return rows
    except Exception:
        return []


def read_log(name: str, home: Path | None = None,
             limit_bytes: int = MAX_BYTES) -> str:
    """Newest ``limit_bytes`` of one log file, redacted; never raises."""
    try:
        target = log_dir(home) / Path(name).name
        if target.parent != log_dir(home):
            return ""
        with target.open("rb") as handle:
            try:
                handle.seek(-limit_bytes, 2)
            except OSError:
                pass
            raw = handle.read(limit_bytes + 1)
        text = raw.decode("utf-8", errors="replace")
        if len(raw) > limit_bytes:
            cut = text.find("\n")
            text = text[cut + 1:] if cut >= 0 else text[-limit_bytes:]
        return redact(text)
    except Exception:
        return ""


def clear_logs(home: Path | None = None) -> None:
    """Remove all rotated logs; never raises."""
    try:
        for name, _size, _mtime in list_logs(home):
            try:
                (log_dir(home) / name).unlink()
            except OSError:
                pass
    except Exception:
        pass
