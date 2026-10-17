"""Central XDG runtime directories for the app.

Every path the app writes lives under one of these. Call ensure_app_dirs()
once at startup and ensure_parent_for() before opening any single file, so
a clean device never fails with "unable to open database file". Only the
app leaf directories are created with mode 0700; pre-existing parents keep
their own permissions.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "xx-audiobook"
_LEAF_MODE = 0o700


def data_dir() -> Path:
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def config_dir() -> Path:
    return Path.home() / ".config" / APP_DIR_NAME


def cache_dir() -> Path:
    return Path.home() / ".cache" / APP_DIR_NAME


def state_dir() -> Path:
    return Path.home() / ".local" / "state" / APP_DIR_NAME


def db_path() -> Path:
    return data_dir() / "audiobook.db"


def config_path() -> Path:
    return config_dir() / "config.json"


def log_file() -> Path:
    return state_dir() / "xx-audiobook.log"


def books_dir() -> Path:
    return data_dir() / "books"


def covers_dir() -> Path:
    return data_dir() / "covers"


def app_dirs() -> dict[str, Path]:
    return {
        "data": data_dir(),
        "config": config_dir(),
        "cache": cache_dir(),
        "state": state_dir(),
    }


def ensure_app_dirs() -> dict[str, Path]:
    """Create every XDG directory the app uses (leaf mode 0700)."""
    dirs = app_dirs()
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, _LEAF_MODE)
    return dirs


def ensure_parent_for(path: Path | str) -> Path:
    """Create the parent directory of a file the app is about to open."""
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved
