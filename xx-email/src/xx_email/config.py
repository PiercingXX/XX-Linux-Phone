"""App settings for XX Email.

Mirrors data/repo/SettingsRepository.kt keys and defaults; stored as JSON
in XDG_CONFIG_HOME/xx-email/config.json. Missing keys fall back to
defaults, unknown keys are ignored, and an invalid file loads as defaults
(the shell's config contract in ref/xx-wm docs/config.md). Stdlib only.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_DIR_NAME = "xx-email"
CONFIG_FILE_NAME = "config.json"
CONFIG_ENV = "XX_EMAIL_CONFIG"

UNDO_MIN = 5
UNDO_MAX = 30
UNDO_DEFAULT = 10
SYNC_MIN = 15
SYNC_DEFAULT = 15

SWIPE_ACTIONS = ("ARCHIVE", "DELETE", "MARK_READ", "STAR", "SNOOZE")
THEME_MODES = ("follow", "light", "dark")


@dataclass
class AppConfig:
    client_id: str = ""
    undo_seconds: int = UNDO_DEFAULT
    swipe_left: str = "ARCHIVE"
    swipe_right: str = "DELETE"
    theme_mode: str = "follow"
    notifications_enabled: bool = True
    sync_minutes: int = SYNC_DEFAULT
    remote_images: bool = False
    send_and_archive: bool = False
    last_account: str = ""


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR_NAME / CONFIG_FILE_NAME


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_DIR_NAME


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / APP_DIR_NAME


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / APP_DIR_NAME


def _coerce(config: AppConfig) -> AppConfig:
    if not isinstance(config.undo_seconds, int) or isinstance(config.undo_seconds, bool):
        config.undo_seconds = UNDO_DEFAULT
    config.undo_seconds = min(UNDO_MAX, max(UNDO_MIN, config.undo_seconds))
    if config.swipe_left not in SWIPE_ACTIONS:
        config.swipe_left = "ARCHIVE"
    if config.swipe_right not in SWIPE_ACTIONS:
        config.swipe_right = "DELETE"
    if config.theme_mode not in THEME_MODES:
        config.theme_mode = "follow"
    if not isinstance(config.sync_minutes, int) or isinstance(config.sync_minutes, bool):
        config.sync_minutes = SYNC_DEFAULT
    config.sync_minutes = max(SYNC_MIN, config.sync_minutes)
    for flag in ("notifications_enabled", "remote_images", "send_and_archive"):
        if not isinstance(getattr(config, flag), bool):
            setattr(config, flag, True if flag == "notifications_enabled" else False)
    if not isinstance(config.client_id, str):
        config.client_id = ""
    if not isinstance(config.last_account, str):
        config.last_account = ""
    return config


def from_dict(raw: object) -> AppConfig:
    """Build a config from parsed JSON; unknown keys ignored, defects defaulted."""
    if not isinstance(raw, dict):
        return AppConfig()
    known = {f.name for f in fields(AppConfig)}
    kept = {k: v for k, v in raw.items() if k in known}
    try:
        return _coerce(AppConfig(**kept))
    except TypeError:
        return AppConfig()


def to_dict(config: AppConfig) -> dict:
    return asdict(config)


def load(path: Path | None = None) -> AppConfig:
    target = path if path is not None else config_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return AppConfig()
    try:
        return from_dict(json.loads(raw))
    except ValueError:
        return AppConfig()


def save(config: AppConfig, path: Path | None = None) -> Path:
    """Atomically persist (tmp file + rename) so a crash keeps last-good."""
    import os

    target = path if path is not None else config_path()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    payload = json.dumps(to_dict(_coerce(config)), indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        Path(tmp_name).replace(target)
    except BaseException:
        try:
            Path(tmp_name).unlink()
        except OSError:
            pass
        raise
    return target
