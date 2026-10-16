"""XX-WM theme reuse: pure stdlib, no GTK imports.

Reads the user's XX-WM shell theme from ~/.config/xx-wm/config.json (same
keys as the shell's docs/config.md) so the app matches the shell, and
renders monochrome-per-theme CSS for the GTK layer. Falls back to
libadwaita dark/light when no shell config is present.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

THEME_PRESETS = {
    "amoled": {"name": "AMOLED", "background": "#000000",
               "foreground": "#f4f4f4", "muted": "#9a9a9a"},
    "graphite": {"name": "Graphite", "background": "#111827",
                 "foreground": "#f7fafc", "muted": "#a0aec0"},
    "forest": {"name": "Forest", "background": "#10261B",
               "foreground": "#ecf4ee", "muted": "#a4b1a7"},
    "ocean": {"name": "Ocean", "background": "#0F1C2E",
              "foreground": "#edf4f7", "muted": "#9fb0bb"},
    "paper": {"name": "Paper", "background": "#F3EEE2",
              "foreground": "#151515", "muted": "#585147"},
    "mist": {"name": "Mist", "background": "#E6EDF5",
             "foreground": "#151a1f", "muted": "#55606c"},
    "aura": {"name": "Aura", "background": "#0d0b14",
             "foreground": "#f0eeff", "muted": "#9080c0"},
    "burgundy": {"name": "Burgundy", "background": "#2A1018",
                 "foreground": "#f6edef", "muted": "#ab949c"},
}

DEFAULT_PRESET = "amoled"

DARK_PRESETS = frozenset(
    {"amoled", "graphite", "forest", "ocean", "aura", "burgundy"})

FONT_FAMILIES = {
    "system-light": "Sans Light",
    "space-mono": "Space Mono, Monospace",
    "jetbrains-mono": "JetBrains Mono, Monospace",
    "jetbrains-mono-nerd":
        "JetBrainsMono Nerd Font, JetBrains Mono, Monospace",
}

DEFAULT_FONT = "jetbrains-mono-nerd"

DANGER_RED = "#ff6b6b"
ON_DANGER_FG = "#ffffff"


@dataclass
class ShellTheme:
    preset: str
    background: str
    foreground: str
    muted: str
    dark: bool
    font_family: str
    text_scale: float


def xx_wm_config_path(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return base / ".config" / "xx-wm" / "config.json"


def _is_hex_color(value: object) -> bool:
    return isinstance(value, str) and len(value) == 7 \
        and value.startswith("#") \
        and all(c in "0123456789abcdefABCDEF" for c in value[1:])


def load_shell_theme(home: Path | None = None) -> ShellTheme:
    """Resolve the active shell theme; never raises on bad input."""
    data: dict = {}
    try:
        raw = xx_wm_config_path(home).read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            data = parsed
    except (OSError, ValueError):
        data = {}

    key = data.get("theme", DEFAULT_PRESET)
    custom = key == "custom"
    if key not in THEME_PRESETS and not custom:
        key = DEFAULT_PRESET
    preset = THEME_PRESETS.get(key, THEME_PRESETS[DEFAULT_PRESET])

    background = preset["background"]
    if custom and _is_hex_color(data.get("custom_background")):
        background = data.get("custom_background")

    font_key = data.get("font", DEFAULT_FONT)
    if font_key == "custom":
        family = data.get("custom_font_family")
        font_family = str(family) if family else FONT_FAMILIES[DEFAULT_FONT]
    else:
        font_family = FONT_FAMILIES.get(str(font_key), FONT_FAMILIES[DEFAULT_FONT])

    try:
        scale = float(data.get("text_size_scale", 1.0))
    except (TypeError, ValueError):
        scale = 1.0
    scale = min(2.0, max(0.5, scale))

    prefer_dark = data.get("prefer_dark", key in DARK_PRESETS)
    dark = bool(prefer_dark) if isinstance(prefer_dark, bool) else key in DARK_PRESETS

    return ShellTheme(
        preset=key,
        background=background,
        foreground=preset["foreground"],
        muted=preset["muted"],
        dark=dark,
        font_family=font_family,
        text_scale=scale,
    )


def theme_css(theme: ShellTheme) -> str:
    """Monochrome-per-theme CSS: every color comes from the preset."""
    size = 16 * theme.text_scale
    return (
        f"window {{ background-color: {theme.background}; }}\n"
        f".xx-title {{ color: {theme.foreground}; "
        f"font-family: {theme.font_family}; "
        f"font-size: {2 * size:.1f}pt; font-weight: bold; }}\n"
        f".xx-body {{ color: {theme.foreground}; "
        f"font-family: {theme.font_family}; font-size: {size:.1f}pt; }}\n"
        f".xx-muted {{ color: {theme.muted}; "
        f"font-family: {theme.font_family}; font-size: {size:.1f}pt; }}\n"
    )
