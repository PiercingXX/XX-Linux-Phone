"""App settings for xx-calendar: XDG JSON store with total-reader semantics.

Key names and defaults mirror the Android SettingsStore
(app/src/main/.../settings/SettingsStore.kt) so an Android BackupJson file
restores here unchanged. Unknown keys are ignored, bad values fall back to
defaults, and a corrupt file yields defaults instead of crashing launch.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from xx_calendar.store import (
    ensure_private_dir,
    fix_file_mode,
    private_umask,
)

APP_DIR_NAME = "xx-calendar"
SETTINGS_FILE_NAME = "settings.json"

DEFAULT_VIEW_CHOICES = ("SCHEDULE", "DAY", "WEEK", "MONTH")
START_DAY_CHOICES = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY",
                     "FRIDAY", "SATURDAY", "SUNDAY")
DENSITY_CHOICES = ("COMFORTABLE", "COMPACT")
FILTER_MODE_CHOICES = ("OFF", "METADATA", "CALENDAR")

DEFAULTS: dict = {
    "default_view": "SCHEDULE",
    "start_day_of_week": "MONDAY",
    "week_numbers": False,
    "show_declined": False,
    "dim_past": True,
    "density": "COMFORTABLE",
    "default_duration_min": 30,
    "default_notification_min": 10,
    "all_day_notify_hour": 18,
    "all_day_notify_days_before": 1,
    "lock_screen_title": False,
    "daily_agenda": False,
    "heads_up": False,
    "hide_auto_added": True,
    "background": "AMOLED_NIGHT",
    "font": "JETBRAINS_MONO",
    "text_size_scale": 1.0,
    "auto_added_filter_mode": "METADATA",
}

_ENUM_KEYS = {
    "default_view": DEFAULT_VIEW_CHOICES,
    "start_day_of_week": START_DAY_CHOICES,
    "density": DENSITY_CHOICES,
    "auto_added_filter_mode": FILTER_MODE_CHOICES,
}

_BOOL_KEYS = ("week_numbers", "show_declined", "dim_past",
              "lock_screen_title", "daily_agenda", "heads_up",
              "hide_auto_added")

_INT_KEYS = ("default_duration_min", "default_notification_min",
             "all_day_notify_hour", "all_day_notify_days_before")


def _coerce_bool(value: object, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def _coerce_int(value: object, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return fallback


def _coerce_float(value: object, fallback: float) -> float:
    if isinstance(value, bool):
        return fallback
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _coerce_enum(value: object, choices: tuple, fallback: str) -> str:
    if isinstance(value, str) and value in choices:
        return value
    return fallback


def parse_settings(data: object) -> dict:
    """Coerce a decoded JSON value to a full settings dict with defaults."""
    raw = data if isinstance(data, dict) else {}
    out = dict(DEFAULTS)
    for key, choices in _ENUM_KEYS.items():
        out[key] = _coerce_enum(raw.get(key), choices, DEFAULTS[key])
    for key in _BOOL_KEYS:
        out[key] = _coerce_bool(raw.get(key), DEFAULTS[key])
    for key in _INT_KEYS:
        out[key] = _coerce_int(raw.get(key), DEFAULTS[key])
    out["text_size_scale"] = _coerce_float(raw.get("text_size_scale"),
                                           DEFAULTS["text_size_scale"])
    for key in ("background", "font"):
        value = raw.get(key)
        out[key] = value if isinstance(value, str) and value else DEFAULTS[key]
    return out


def default_settings() -> dict:
    """Fresh copy of the shipped quiet defaults."""
    return dict(DEFAULTS)


def settings_path(config_home: Path | None = None) -> Path:
    """XDG config path for settings.json (~/.config/xx-calendar)."""
    base = config_home if config_home is not None else Path.home() / ".config"
    return Path(base) / APP_DIR_NAME / SETTINGS_FILE_NAME


def load_settings(path: Path | None = None) -> dict:
    """Load settings from disk; defaults when missing or corrupt."""
    candidate = path if path is not None else settings_path()
    try:
        raw = Path(candidate).read_text(encoding="utf-8")
    except OSError:
        return default_settings()
    try:
        return parse_settings(json.loads(raw))
    except ValueError:
        return default_settings()


def dump_settings(settings: dict) -> str:
    """Serialize settings deterministically (sorted keys, indent 2)."""
    full = parse_settings(settings)
    return json.dumps(full, indent=2, sort_keys=True) + "\n"


def save_settings(settings: dict, path: Path | None = None) -> Path:
    """Atomically write settings at mode 0600 inside a 0700 dir.

    Editors replace files atomically, so readers must tolerate a
    mid-write replace; writers use tmp-file + fsync + rename for the
    same reason.
    """
    candidate = path if path is not None else settings_path()
    candidate = Path(candidate)
    ensure_private_dir(str(candidate.parent))
    text = dump_settings(settings)
    tmp = str(candidate) + ".tmp"
    with private_umask():
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    fix_file_mode(tmp)
    os.replace(tmp, str(candidate))
    fix_file_mode(str(candidate))
    return candidate
