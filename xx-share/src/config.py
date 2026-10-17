"""xx-share config: own file under ~/.config/xx-share/config.json.

Atomic replace, 0600, same durability rules as the shell config. The
``theme`` key is legacy (kept so old files still load); the active
theme comes from ``xx_ui`` which reads the shell config directly.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

VISIBILITY_AUTO = "auto-10min"
VISIBILITY_ALWAYS = "always"

AUTO_OFF_SECONDS = 10 * 60


DEFAULT_CONFIG = {
    "theme": "auto",
    "alias": "",
    "port": 53317,
    "visible": False,
    "visibility_mode": VISIBILITY_AUTO,
    "favorites": [],
    "auto_accept_favorites": False,
    "max_transfer_mb": 500,
    "require_pin": "",
}

class ShareConfig:
    def __init__(self, config_dir: str | Path | None = None) -> None:
        base = Path(config_dir) if config_dir else Path.home() / ".config" / "xx-share"
        self.config_dir = base
        self.config_path = base / "config.json"
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        if not self.config_path.exists():
            return
        try:
            loaded = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(loaded, dict):
            return
        for key, value in loaded.items():
            default = DEFAULT_CONFIG.get(key)
            if default is None:
                continue
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    continue
            elif isinstance(default, int):
                if isinstance(value, bool) or not isinstance(value, int):
                    continue
            elif isinstance(default, (list, dict)):
                if not isinstance(value, type(default)):
                    continue
            elif isinstance(default, str):
                if not isinstance(value, str):
                    continue
            self.data[key] = value

    def save(self) -> None:
        payload = json.dumps(self.data, indent=2) + "\n"
        try:
            if self.config_path.read_text(encoding="utf-8") == payload:
                if stat.S_IMODE(self.config_path.stat().st_mode) != 0o600:
                    os.chmod(self.config_path, 0o600)
                return
        except (OSError, ValueError):
            pass
        self.config_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.config_path.with_name(self.config_path.name + ".tmp")
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.config_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        os.chmod(self.config_path, 0o600)

    @property
    def max_transfer_bytes(self) -> int:
        return int(self.data.get("max_transfer_mb", 500)) * 1024 * 1024
