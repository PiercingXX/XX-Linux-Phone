"""XX-WM theme presets, reused verbatim so the app matches the shell.

Values are copied from ref/xx-wm/launcher/src/config.py THEME_PRESETS and
FONT_FAMILIES. This module is pure stdlib so tests run headless; only
main.py touches Gtk/Adw.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

XX_WM_CONFIG_PATH = Path.home() / ".config/xx-wm/config.json"


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
    "amoled": ThemePreset("amoled", "AMOLED", "#000000", "#111111", "#181818",
                          "#2f2f2f", "#f4f4f4", "#9a9a9a", "#d8d8d8"),
    "graphite": ThemePreset("graphite", "Graphite", "#111827", "#1f2937",
                            "#2d3748", "#4a5568", "#f7fafc", "#a0aec0",
                            "#718096"),
    "forest": ThemePreset("forest", "Forest", "#10261B", "#1a3a2a",
                          "#244e3a", "#3d6b56", "#ecf4ee", "#a4b1a7",
                          "#c9d8cc"),
    "ocean": ThemePreset("ocean", "Ocean", "#0F1C2E", "#182a42", "#203856",
                         "#34506e", "#edf4f7", "#9fb0bb", "#cad8df"),
    "paper": ThemePreset("paper", "Paper", "#F3EEE2", "#e0d8cb", "#ccc4b4",
                         "#b09c85", "#151515", "#585147", "#262626"),
    "mist": ThemePreset("mist", "Mist", "#E6EDF5", "#d0d8e2", "#bac4cf",
                        "#9aa8ba", "#151a1f", "#55606c", "#2f3943"),
    "aura": ThemePreset("aura", "Aura", "#0d0b14", "#14112a", "#1e1a3a",
                        "#3d3066", "#f0eeff", "#9080c0", "#a855f7"),
    "burgundy": ThemePreset("burgundy", "Burgundy", "#2A1018", "#3b1a24",
                            "#4c2330", "#6e3a4c", "#f6edef", "#ab949c",
                            "#d9b3bf"),
}

DEFAULT_THEME_KEY = "amoled"

FONT_FAMILIES = {
    "system-light": "Sans Light",
    "space-mono": "Space Mono, Monospace",
    "jetbrains-mono": "JetBrains Mono, Monospace",
    "jetbrains-mono-nerd": "JetBrainsMono Nerd Font, JetBrains Mono, Monospace",
}

DEFAULT_FONT_KEY = "jetbrains-mono-nerd"


def load_xx_wm_config(path: Path | str = XX_WM_CONFIG_PATH) -> dict:
    """Read the shell config file, returning {} when missing or invalid."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def resolve_preset(data: dict) -> ThemePreset:
    """Pick the ThemePreset named by shell config data, defaulting to amoled."""
    key = data.get("theme", DEFAULT_THEME_KEY)
    preset = THEME_PRESETS.get(str(key))
    if preset is not None:
        return preset
    return THEME_PRESETS[DEFAULT_THEME_KEY]


def resolve_font_family(data: dict) -> str:
    """Pick the font family named by shell config data, with default fallback."""
    key = str(data.get("font", DEFAULT_FONT_KEY))
    if key == "custom":
        family = data.get("custom_font_family")
        if family:
            return str(family)
    return FONT_FAMILIES.get(key, FONT_FAMILIES[DEFAULT_FONT_KEY])


def preset_css(preset: ThemePreset) -> str:
    """Render the preset as a small GTK CSS fragment for the placeholder page."""
    return (
        "window { background-color: " + preset.background + "; color: "
        + preset.foreground + "; }\n"
        "listview, list { background-color: " + preset.surface + "; color: "
        + preset.foreground + "; }\n"
        "listview row:selected { background-color: " + preset.surface_alt + "; }\n"
        ".vitals-muted { color: " + preset.muted + "; }\n"
        ".vitals-accent { color: " + preset.accent + "; }\n"
    )
