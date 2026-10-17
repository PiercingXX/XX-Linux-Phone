"""XDG directories for one app built on xx_music.

Every path an app writes lives under these. Directory names come from the
app spec, so two apps on the same core never share a cache, a queue or a
database. Leaf directories are created 0700; existing parents keep their
own permissions.
"""

from __future__ import annotations

import os
from pathlib import Path

_LEAF_MODE = 0o700


def _xdg(var: str, *fallback: str) -> Path:
    value = os.environ.get(var, "").strip()
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home().joinpath(*fallback)


class AppPaths:
    def __init__(self, dir_name: str) -> None:
        if not dir_name or "/" in dir_name or dir_name.startswith("."):
            raise ValueError(f"bad app dir name: {dir_name!r}")
        self.dir_name = dir_name

    def data_dir(self) -> Path:
        return _xdg("XDG_DATA_HOME", ".local", "share") / self.dir_name

    def config_dir(self) -> Path:
        return _xdg("XDG_CONFIG_HOME", ".config") / self.dir_name

    def cache_dir(self) -> Path:
        return _xdg("XDG_CACHE_HOME", ".cache") / self.dir_name

    def state_dir(self) -> Path:
        return _xdg("XDG_STATE_HOME", ".local", "state") / self.dir_name

    def runtime_dir(self) -> Path:
        """tmpfs, per login: short-lived secrets such as a cookie jar."""
        base = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        if base and os.path.isabs(base):
            return Path(base) / self.dir_name
        return self.cache_dir() / "run"

    # files and leaf dirs
    def db_path(self) -> Path:
        return self.data_dir() / "music.db"

    def config_path(self) -> Path:
        return self.config_dir() / "config.json"

    def queue_path(self) -> Path:
        return self.state_dir() / "queue.json"

    def log_file(self) -> Path:
        return self.state_dir() / f"{self.dir_name}.log"

    def offline_dir(self) -> Path:
        """Downloads the person asked for (pinned, never evicted)."""
        return self.data_dir() / "offline"

    def audio_cache_dir(self) -> Path:
        """Prefetched audio (evictable, under the storage cap)."""
        return self.cache_dir() / "audio"

    def art_cache_dir(self) -> Path:
        return self.cache_dir() / "art"

    def engine_dir(self) -> Path:
        """Engine-private tools (updated libraries, helpers)."""
        return self.data_dir() / "engine"

    def app_dirs(self) -> dict[str, Path]:
        return {
            "data": self.data_dir(),
            "config": self.config_dir(),
            "cache": self.cache_dir(),
            "state": self.state_dir(),
        }

    def ensure_app_dirs(self) -> dict[str, Path]:
        dirs = self.app_dirs()
        for path in dirs.values():
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, _LEAF_MODE)
        return dirs

    @staticmethod
    def ensure_parent_for(path: Path | str) -> Path:
        resolved = Path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved
