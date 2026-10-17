"""App config: JSON file, tolerant parse, stdlib only.

Lives at ~/.config/xx-dialer/config.json. Missing keys fall back to
defaults, unknown keys are ignored, a corrupt file yields defaults (the
bad file is backed up next to it) — the shell's own config rule.
"""
from __future__ import annotations

import json
from pathlib import Path

APP_DIR_NAME = "xx-dialer"
CONFIG_NAME = "config.json"

DEFAULTS = {
    "enforcement_mode": "observing",
    "observe_week_end": 0.0,
    "bypass_until": None,
    "bypass_duration_minutes": 120,
    "unknown_window": {"start": 540, "end": 1020, "days": 127},
    "business_window": {"start": 540, "end": 1140, "days": 127},
    "hidden_caller_policy": "unknown",
    "stir_action": "block",
    "repeat_caller_enabled": True,
    "silenced_notif_policy": "immediate",
    "answer_interaction": "tap",
    "group_recents": True,
    "ring_repeat": "once",
    "unknown_tone": "",
    "default_tone": "",
    "hidden_tabs": [],
    "voicemail_number": "",
    "default_region": "US",
}

_ENUMS = {
    "enforcement_mode": {"enforcing", "observing"},
    "hidden_caller_policy": {"unknown", "silence", "block"},
    "stir_action": {"block", "silence", "off"},
    "silenced_notif_policy": {"immediate", "daily", "never"},
    "answer_interaction": {"tap", "slider"},
    "ring_repeat": {"once", "twice", "until_vm"},
}


def config_path(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return base / ".config" / APP_DIR_NAME / CONFIG_NAME


def _valid_window(value: object, fallback: dict) -> dict:
    if not isinstance(value, dict):
        return dict(fallback)
    out = dict(fallback)
    for name in ("start", "end"):
        raw = value.get(name)
        if isinstance(raw, bool):
            continue
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= minutes < 24 * 60:
            out[name] = minutes
    days = value.get("days")
    try:
        mask = int(days)
    except (TypeError, ValueError):
        mask = out["days"]
    if isinstance(days, bool) or not 1 <= mask <= 127:
        mask = out["days"]
    out["days"] = mask
    return out


def load(home: Path | None = None) -> dict:
    """Load config with defaults; never raises on missing/corrupt input."""
    data = {k: (dict(v) if isinstance(v, dict) else
                list(v) if isinstance(v, list) else v)
            for k, v in DEFAULTS.items()}
    try:
        raw = config_path(home).read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, ValueError):
        return data
    if not isinstance(parsed, dict):
        return data
    for key, default in DEFAULTS.items():
        if key not in parsed:
            continue
        value = parsed[key]
        if key in _ENUMS:
            if value in _ENUMS[key]:
                data[key] = value
        elif key in ("unknown_window", "business_window"):
            data[key] = _valid_window(value, default)
        elif key in ("observe_week_end",):
            try:
                data[key] = float(value)
            except (TypeError, ValueError):
                pass
        elif key in ("bypass_duration_minutes",):
            try:
                data[key] = int(value)
            except (TypeError, ValueError):
                pass
        elif key in ("repeat_caller_enabled", "group_recents"):
            if isinstance(value, bool):
                data[key] = value
        elif key == "bypass_until":
            if value is None or isinstance(value, (str, int, float)):
                data[key] = value
        elif key == "hidden_tabs":
            if isinstance(value, list) and all(isinstance(t, str) for t in value):
                data[key] = list(value)
        elif isinstance(default, str):
            if isinstance(value, str):
                data[key] = value
    return data


def save(data: dict, home: Path | None = None) -> Path:
    """Write known keys only; creates parent dirs. Returns the file path."""
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: data.get(k, v) for k, v in DEFAULTS.items()}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
