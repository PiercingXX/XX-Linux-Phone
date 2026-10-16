"""XX-WM family theme sharing for XX Email.

Reads the user's shell theme from ~/.config/xx-wm/config.json (keys
``theme``, ``custom_background``, ``prefer_dark``, ``font``,
``custom_font_family``, ``text_size_scale`` — see ref/xx-wm
launcher/src/config.py THEME_PRESETS / FONT_FAMILIES) and turns it into GTK
CSS. Stdlib only, so the headless pytest gate can exercise it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

APP_ID = "io.piercingxx.XXEmail"

SHELL_CONFIG_ENV = "XX_WM_CONFIG"
SHELL_CONFIG_DEFAULT = Path.home() / ".config" / "xx-wm" / "config.json"


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


THEME_PRESETS: dict[str, ThemePreset] = {
    "amoled": ThemePreset("amoled", "AMOLED", "#000000", "#111111",
                          "#181818", "#2f2f2f", "#f4f4f4", "#9a9a9a",
                          "#d8d8d8"),
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

FONT_FAMILIES: dict[str, str] = {
    "system-light": "Sans Light",
    "space-mono": "Space Mono, Monospace",
    "jetbrains-mono": "JetBrains Mono, Monospace",
    "jetbrains-mono-nerd": "JetBrainsMono Nerd Font, JetBrains Mono, Monospace",
}

DEFAULT_THEME = "amoled"
DEFAULT_FONT = "jetbrains-mono-nerd"

# Android display name (theme/FamilyTheme.kt, matching the launcher
# broadcaster verbatim) -> Linux preset key. Aura is Linux-only.
ANDROID_PRESET_MAP: dict[str, str] = {
    "AMOLED Night": "amoled",
    "Graphite": "graphite",
    "Forest Night": "forest",
    "Ocean Drift": "ocean",
    "Burgundy": "burgundy",
    "Paper": "paper",
    "Mist": "mist",
    "Custom": "custom",
}

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


def shell_config_path() -> Path:
    override = os.environ.get(SHELL_CONFIG_ENV)
    if override:
        return Path(override)
    return SHELL_CONFIG_DEFAULT


def load_shell_config(path: Path | None = None) -> dict:
    """Shell config as a dict; missing/invalid files yield {} (last-good wins)."""
    target = path if path is not None else shell_config_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_hex_color(value: object) -> bool:
    return isinstance(value, str) and _HEX_COLOR.match(value) is not None


def resolve_theme(shell_config: dict) -> ThemePreset:
    """Map shell ``theme`` (+ ``custom_background``) onto a preset.

    Unknown keys and malformed custom colors fall back to the default
    preset; a custom color is only honored when it is a valid #RRGGBB.
    """
    key = shell_config.get("theme", DEFAULT_THEME)
    if key == "custom":
        background = shell_config.get("custom_background")
        if _is_hex_color(background):
            base = THEME_PRESETS[DEFAULT_THEME]
            return ThemePreset("custom", "Custom", background, base.surface,
                               base.surface_alt, base.border, base.foreground,
                               base.muted, base.accent)
        return THEME_PRESETS[DEFAULT_THEME]
    if isinstance(key, str) and key in THEME_PRESETS:
        return THEME_PRESETS[key]
    return THEME_PRESETS[DEFAULT_THEME]


def resolve_font(shell_config: dict) -> str:
    """CSS font-family for the shell ``font`` key (custom via family name)."""
    key = shell_config.get("font", DEFAULT_FONT)
    if key == "custom":
        family = shell_config.get("custom_font_family")
        if isinstance(family, str) and family.strip():
            return family.strip()
        return FONT_FAMILIES[DEFAULT_FONT]
    if isinstance(key, str) and key in FONT_FAMILIES:
        return FONT_FAMILIES[key]
    return FONT_FAMILIES[DEFAULT_FONT]


def text_scale(shell_config: dict) -> float:
    """Launcher-wide type scale, clamped 0.5–2.0 like the shell."""
    try:
        scale = float(shell_config.get("text_size_scale", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return min(2.0, max(0.5, scale))


def prefer_dark(shell_config: dict) -> bool:
    return bool(shell_config.get("prefer_dark", True))


def theme_css(preset: ThemePreset, font_family: str, scale: float = 1.0) -> str:
    """GTK CSS using only the preset's colors — no hardcoded hexes here."""
    size = 17.0 * scale
    small = 13.0 * scale
    return (
        "@define-color email_bg " + preset.background + ";\n"
        "@define-color email_surface " + preset.surface + ";\n"
        "@define-color email_surface_alt " + preset.surface_alt + ";\n"
        "@define-color email_border " + preset.border + ";\n"
        "@define-color email_fg " + preset.foreground + ";\n"
        "@define-color email_muted " + preset.muted + ";\n"
        "@define-color email_accent " + preset.accent + ";\n"
        "window { background-color: @email_bg; color: @email_fg; }\n"
        "window label { color: @email_fg; font-family: " + font_family + "; }\n"
        ".email-title { font-size: " + ("%.1f" % (size + 8.0)) + "pt; "
        "font-weight: bold; }\n"
        ".email-body { font-size: " + ("%.1f" % size) + "pt; }\n"
        ".email-muted { color: @email_muted; font-size: "
        + ("%.1f" % small) + "pt; }\n"
        ".email-row { background-color: @email_surface; "
        "border-bottom: 1px solid @email_border; }\n"
        ".email-unread { font-weight: bold; }\n"
        "listview.email-listview, listview.email-listview row, "
        "scrolledwindow.email-listview, "
        "scrolledwindow.email-listview viewport { "
        "background-color: @email_bg; color: @email_fg; }\n"
        "listview.email-listview row:selected { "
        "background-color: @email_surface_alt; }\n"
        ".email-banner { background-color: @email_surface_alt; "
        "border: 1px solid @email_border; border-radius: 8px; "
        "padding: 8px; }\n"
        ".email-banner label { color: @email_fg; }\n"
        "entry { background-color: @email_surface; "
        "color: @email_fg; border-color: @email_border; }\n"
    )
