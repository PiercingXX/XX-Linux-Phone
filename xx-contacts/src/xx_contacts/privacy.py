"""Private-file helpers: contact data is readable only by its owner.

Directories the app owns are mode 0700 (created or repaired on open);
files the app writes are mode 0600. SQLite creates its -wal/-shm sidecars
under the process umask, so the store opens the database under a temporary
``os.umask(0o077)`` and re-chmods sidecars that already exist.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager

DIR_MODE = 0o700
FILE_MODE = 0o600


def ensure_private_dir(path: str) -> str:
    """Create *path* with mode 0700; repair an existing dir to 0700."""
    os.makedirs(path, mode=DIR_MODE, exist_ok=True)
    try:
        if stat.S_IMODE(os.stat(path).st_mode) != DIR_MODE:
            os.chmod(path, DIR_MODE)
    except OSError:
        pass
    return path


def fix_file_mode(path: str, mode: int = FILE_MODE) -> None:
    """Repair *path* to *mode* when it exists with another mode."""
    try:
        if stat.S_IMODE(os.stat(path).st_mode) != mode:
            os.chmod(path, mode)
    except OSError:
        pass


@contextmanager
def private_umask() -> Iterator[None]:
    """Run a creation block under ``umask 077``; restore it after."""
    old = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(old)


def secure_write_text(path: str, text: str) -> None:
    """Atomically replace *path* with *text* at mode 0600 in a 0700 dir."""
    ensure_private_dir(os.path.dirname(os.path.abspath(path)))
    tmp = path + ".tmp"
    with private_umask():
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    fix_file_mode(tmp)
    os.replace(tmp, path)
    fix_file_mode(path)
