"""Read the XX-WM shell theme so the app matches the shell.

Pure module: no GTK import, safe to use headless and in tests.
Preset values mirror ref/xx-wm launcher/src/config.py THEME_PRESETS /
FONT_FAMILIES; the shell config file is the live source when present.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


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
    danger: str = ""


THEME_PRESETS: dict[str, ThemePreset] = {
    "amoled": ThemePreset("amoled", "AMOLED", "#000000", "#111111", "#181818",
                           "#2f2f2f", "#f4f4f4", "#9a9a9a", "#d8d8d8",
                           "#f26d6d"),
    "graphite": ThemePreset("graphite", "Graphite", "#111827", "#1f2937",
                             "#2d3748", "#4a5568", "#f7fafc", "#a0aec0",
                             "#718096", "#f26d6d"),
    "forest": ThemePreset("forest", "Forest", "#10261B", "#1a3a2a", "#244e3a",
                           "#3d6b56", "#ecf4ee", "#a4b1a7", "#c9d8cc",
                           "#f28b82"),
    "ocean": ThemePreset("ocean", "Ocean", "#0F1C2E", "#182a42", "#203856",
                          "#34506e", "#edf4f7", "#9fb0bb", "#cad8df",
                          "#f28b82"),
    "paper": ThemePreset("paper", "Paper", "#F3EEE2", "#e0d8cb", "#ccc4b4",
                          "#b09c85", "#151515", "#585147", "#262626",
                          "#b91c1c"),
    "mist": ThemePreset("mist", "Mist", "#E6EDF5", "#d0d8e2", "#bac4cf",
                         "#9aa8ba", "#151a1f", "#55606c", "#2f3943",
                         "#b91c1c"),
    "aura": ThemePreset("aura", "Aura", "#0d0b14", "#14112a", "#1e1a3a",
                         "#3d3066", "#f0eeff", "#9080c0", "#a855f7",
                         "#f26d6d"),
    "burgundy": ThemePreset("burgundy", "Burgundy", "#2A1018", "#3b1a24",
                             "#4c2330", "#6e3a4c", "#f6edef", "#ab949c",
                             "#d9b3bf", "#f28b82"),
}

FONT_FAMILIES: dict[str, str] = {
    "system-light": "Sans Light",
    "space-mono": "Space Mono, Monospace",
    "jetbrains-mono": "JetBrains Mono, Monospace",
    "jetbrains-mono-nerd": "JetBrainsMono Nerd Font, JetBrains Mono, Monospace",
}

DEFAULT_PRESET = "amoled"
DEFAULT_FONT = "jetbrains-mono-nerd"


def load_shell_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_preset(data: dict) -> ThemePreset:
    key = data.get("theme", DEFAULT_PRESET)
    if not isinstance(key, str):
        key = DEFAULT_PRESET
    return THEME_PRESETS.get(key, THEME_PRESETS[DEFAULT_PRESET])


def resolve_font_family(data: dict) -> str:
    key = data.get("font", DEFAULT_FONT)
    if key == "custom":
        family = data.get("custom_font_family")
        if isinstance(family, str) and family.strip():
            return family.strip()
        return FONT_FAMILIES[DEFAULT_FONT]
    if not isinstance(key, str):
        return FONT_FAMILIES[DEFAULT_FONT]
    return FONT_FAMILIES.get(key, FONT_FAMILIES[DEFAULT_FONT])


def resolve_text_scale(data: dict) -> float:
    try:
        scale = float(data.get("text_size_scale", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return min(2.0, max(0.5, scale))


def is_dark(data: dict) -> bool:
    return bool(data.get("prefer_dark", True))


def to_css(preset: ThemePreset, font_family: str, scale: float = 1.0) -> str:
    base_pt = round(13 * scale)
    title_pt = round(27 * scale)
    return (
        "* { font-family: %s; }\n"
        "window, .background { background-color: %s; color: %s; }\n"
        ".xx-title { font-size: %dpt; color: %s; }\n"
        ".xx-body { font-size: %dpt; color: %s; }\n"
        ".xx-muted { color: %s; }\n"
        "list row { border-bottom: 1px solid %s; }\n"
        "listview { background-color: %s; color: %s; }\n"
        "listview row { background-color: %s; color: %s; "
        "border-bottom: 1px solid %s; }\n"
        "listview row:selected { background-color: %s; color: %s; }\n"
        ".xx-section-header { color: %s; background-color: %s; "
        "font-weight: bold; }\n"
        ".xx-row-detail { color: %s; }\n"
        ".destructive-action { color: %s; }\n"
        "button.destructive-action { color: %s; }\n"
        % (font_family, preset.background, preset.foreground,
           title_pt, preset.foreground, base_pt, preset.foreground,
           preset.muted, preset.border,
           preset.background, preset.foreground,
           preset.background, preset.foreground, preset.border,
           preset.surface_alt, preset.foreground,
           preset.muted, preset.background,
           preset.muted, preset.danger, preset.danger)
    )
