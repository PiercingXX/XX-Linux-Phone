"""Caption palette resolved from the XX-WM shell theme.

The overlay never invents colors: it reads the shell's
~/.config/xx-wm/config.json `theme` key and renders with that preset's
palette. Unknown keys fall back to `amoled`, mirroring ShellConfig.theme.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

FALLBACK_THEME = "amoled"


@dataclass(frozen=True)
class CaptionPalette:
    key: str
    background: str
    surface: str
    surface_alt: str
    border: str
    foreground: str
    muted: str
    accent: str


PALETTES = {
    "amoled": CaptionPalette("amoled", "#000000", "#111111", "#181818",
                             "#2f2f2f", "#f4f4f4", "#9a9a9a", "#d8d8d8"),
    "graphite": CaptionPalette("graphite", "#111827", "#1f2937", "#2d3748",
                               "#4a5568", "#f7fafc", "#a0aec0", "#718096"),
    "forest": CaptionPalette("forest", "#10261B", "#1a3a2a", "#244e3a",
                             "#3d6b56", "#ecf4ee", "#a4b1a7", "#c9d8cc"),
    "ocean": CaptionPalette("ocean", "#0F1C2E", "#182a42", "#203856",
                            "#34506e", "#edf4f7", "#9fb0bb", "#cad8df"),
    "paper": CaptionPalette("paper", "#F3EEE2", "#e0d8cb", "#ccc4b4",
                            "#b09c85", "#151515", "#585147", "#262626"),
    "mist": CaptionPalette("mist", "#E6EDF5", "#d0d8e2", "#bac4cf",
                           "#9aa8ba", "#151a1f", "#55606c", "#2f3943"),
    "aura": CaptionPalette("aura", "#0d0b14", "#14112a", "#1e1a3a",
                           "#3d3066", "#f0eeff", "#9080c0", "#a855f7"),
    "burgundy": CaptionPalette("burgundy", "#2A1018", "#3b1a24", "#4c2330",
                               "#6e3a4c", "#f6edef", "#ab949c", "#d9b3bf"),
}


def preset_for_key(key: object) -> CaptionPalette:
    if isinstance(key, str) and key in PALETTES:
        return PALETTES[key]
    return PALETTES[FALLBACK_THEME]


def read_xx_wm_theme_key(home: Path | None = None) -> str:
    """Theme key from the shell config; FALLBACK_THEME on any failure."""
    path = (home or Path.home()) / ".config" / "xx-wm" / "config.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return FALLBACK_THEME
    if not isinstance(raw, dict):
        return FALLBACK_THEME
    theme = raw.get("theme")
    return theme if isinstance(theme, str) and theme in PALETTES else FALLBACK_THEME


def resolve_palette(home: Path | None = None) -> CaptionPalette:
    return preset_for_key(read_xx_wm_theme_key(home))


def caption_css(palette: CaptionPalette) -> str:
    return f"""
.caption-root {{
    background: alpha({palette.surface}, 0.92);
    color: {palette.foreground};
    border-radius: 12px;
    border: 1px solid alpha({palette.foreground}, 0.18);
    padding: 8px 12px;
}}
.caption-line {{
    font-size: 13pt;
    color: {palette.foreground};
}}
.caption-line-partial {{
    color: {palette.muted};
}}
.caption-rec {{
    color: {palette.accent};
    font-size: 9pt;
    letter-spacing: 0.1em;
}}
"""
