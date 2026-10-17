"""App settings: XDG JSON store with total-reader semantics.

Keys and defaults mirror the Android Settings table (design "Settings"):
auto_launch off, every surface on except Calls, keep_screen_on /
follow_rotation / always_ink on. Unknown keys are ignored, bad values
fall back to defaults, a corrupt file yields defaults. Writes are
atomic (tmp + rename) at mode 0600.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

APP_DIR_NAME = "xx-auto"
SETTINGS_FILE_NAME = "settings.json"

DEFAULTS: dict = {
    "auto_launch": False,
    "auto_launch_device": None,
    "surface_now_playing": True,
    "surface_quick_pick": True,
    "surface_nav": True,
    "surface_calls": False,
    "keep_screen_on": True,
    "follow_rotation": True,
    "always_ink": True,
}

_BOOL_KEYS = ("auto_launch", "surface_now_playing", "surface_quick_pick",
              "surface_nav", "surface_calls", "keep_screen_on",
              "follow_rotation", "always_ink")


@dataclass
class Settings:
    auto_launch: bool = False
    auto_launch_device: str | None = None
    surface_now_playing: bool = True
    surface_quick_pick: bool = True
    surface_nav: bool = True
    surface_calls: bool = False
    keep_screen_on: bool = True
    follow_rotation: bool = True
    always_ink: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        raw = data if isinstance(data, dict) else {}
        device = raw.get("auto_launch_device")
        return cls(
            auto_launch=raw.get("auto_launch") is True,
            auto_launch_device=device if isinstance(device, str) and device else None,
            surface_now_playing=_coerce_bool(raw.get("surface_now_playing"), True),
            surface_quick_pick=_coerce_bool(raw.get("surface_quick_pick"), True),
            surface_nav=_coerce_bool(raw.get("surface_nav"), True),
            surface_calls=_coerce_bool(raw.get("surface_calls"), False),
            keep_screen_on=_coerce_bool(raw.get("keep_screen_on"), True),
            follow_rotation=_coerce_bool(raw.get("follow_rotation"), True),
            always_ink=_coerce_bool(raw.get("always_ink"), True),
        )

    def to_dict(self) -> dict:
        out = asdict(self)
        if out.get("auto_launch_device") is None:
            out.pop("auto_launch_device", None)
        return out


def _coerce_bool(value: object, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def parse_settings(data: object) -> Settings:
    """Coerce a decoded JSON value to Settings with defaults."""
    raw = data if isinstance(data, dict) else {}
    fixed = dict(raw)
    for key in _BOOL_KEYS:
        if key in fixed and not isinstance(fixed[key], bool):
            fixed.pop(key)
    device = fixed.get("auto_launch_device")
    if device is not None and not (isinstance(device, str) and device):
        fixed.pop("auto_launch_device")
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in fixed.items() if k in DEFAULTS})
    return Settings.from_dict(merged)


def settings_path(home: str | None = None) -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_DIR_NAME / SETTINGS_FILE_NAME
    root = Path(home).expanduser() if home else Path.home()
    return root / ".config" / APP_DIR_NAME / SETTINGS_FILE_NAME


def load_settings(path: Path | None = None) -> Settings:
    try:
        text = (path or settings_path()).read_text(encoding="utf-8")
    except OSError:
        return Settings()
    try:
        return parse_settings(json.loads(text))
    except ValueError:
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    dest = path or settings_path()
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(dest.parent, 0o700)
    except OSError:
        pass
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(json.dumps(settings.to_dict(), indent=2) + "\n",
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, dest)
    return dest
