"""xx-share config: own file plus the XX-WM shell theme fallback.

Own keys live in ~/.config/xx-share/config.json (0600, atomic replace,
same durability rules as the shell config). The theme preset table
mirrors the shell's canonical presets; when our theme is "auto" the
shell's theme (~/.config/xx-wm/config.json) wins, so the app follows
the phone like the other ports.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

VISIBILITY_AUTO = "auto-10min"
VISIBILITY_ALWAYS = "always"

AUTO_OFF_SECONDS = 10 * 60


@dataclass(frozen=True)
class ThemePreset:
    key: str
    name: str
    background: str
    surface: str
    surface_alt: str
    border: str
    foreground: str
    muted: str
    accent: str


THEME_PRESETS = {
    "amoled": ThemePreset("amoled", "AMOLED", "#000000", "#111111",
                          "#181818", "#2f2f2f", "#f4f4f4", "#9a9a9a",
                          "#d8d8d8"),
    "graphite": ThemePreset("graphite", "Graphite", "#111827", "#1f2937",
                            "#2d3748", "#4a5568", "#f7fafc", "#a0aec0",
                            "#718096"),
    "forest": ThemePreset("forest", "Forest", "#10261B", "#1a3a2a",
                          "#244e3a", "#3d6b56", "#ecf4ee", "#a4b1a7",
                          "#c9d8cc"),
    "ocean": ThemePreset("ocean", "Ocean", "#0F1C2E", "#182a42",
                         "#203856", "#34506e", "#edf4f7", "#9fb0bb",
                         "#cad8df"),
    "paper": ThemePreset("paper", "Paper", "#F3EEE2", "#e0d8cb",
                         "#ccc4b4", "#b09c85", "#151515", "#585147",
                         "#262626"),
    "mist": ThemePreset("mist", "Mist", "#E6EDF5", "#d0d8e2",
                        "#bac4cf", "#9aa8ba", "#151a1f", "#55606c",
                        "#2f3943"),
    "aura": ThemePreset("aura", "Aura", "#0d0b14", "#14112a",
                        "#1e1a3a", "#3d3066", "#f0eeff", "#9080c0",
                        "#a855f7"),
    "burgundy": ThemePreset("burgundy", "Burgundy", "#2A1018", "#3b1a24",
                            "#4c2330", "#6e3a4c", "#f6edef", "#ab949c",
                            "#d9b3bf"),
}

DANGER_RED = "#ff6b6b"
ON_DANGER_FG = "#ffffff"

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

SHELL_CONFIG_PATH = Path.home() / ".config" / "xx-wm" / "config.json"


def shell_theme_key(config_path: str | Path = SHELL_CONFIG_PATH) -> str:
    try:
        raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "amoled"
    if not isinstance(raw, dict):
        return "amoled"
    key = raw.get("theme", "amoled")
    return key if key in THEME_PRESETS else "amoled"


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

    def effective_theme(self) -> ThemePreset:
        key = str(self.data.get("theme", "auto"))
        if key == "auto":
            key = shell_theme_key()
        return THEME_PRESETS.get(key, THEME_PRESETS["amoled"])

    @property
    def max_transfer_bytes(self) -> int:
        return int(self.data.get("max_transfer_mb", 500)) * 1024 * 1024
