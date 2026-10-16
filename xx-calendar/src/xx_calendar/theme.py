"""Theme data for xx-calendar: preset tables and shell-theme resolution.

Pure Python, no GTK imports, so the unit tests run headless. The tables
mirror ref/xx-wm/launcher/src/config.py THEME_PRESETS / FONT_FAMILIES so
the app matches the shell exactly. Shell-theme helpers mirror
ref/xx-contacts shell_theme where they fit.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

APP_ID = "io.piercingxx.XXCalendar"

DEFAULT_PRESET_KEY = "amoled"
DEFAULT_FONT_KEY = "jetbrains-mono-nerd"
CUSTOM_KEY = "custom"


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

FONT_FAMILIES: dict[str, str] = {
    "system-light": "Sans Light",
    "space-mono": "Space Mono, Monospace",
    "jetbrains-mono": "JetBrains Mono, Monospace",
    "jetbrains-mono-nerd": "JetBrainsMono Nerd Font, JetBrains Mono, Monospace",
}

XXWM_CONFIG_PATH = Path.home() / ".config" / "xx-wm" / "config.json"

_CUSTOM_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


def _derive_shades(bg: str) -> tuple[str, str, str]:
    """Derive surface/surface_alt/border from a background, like the shell."""
    match = re.match(r"#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})", bg)
    if not match:
        return ("#111111", "#181818", "#2f2f2f")
    rgb = tuple(int(match.group(i), 16) for i in (1, 2, 3))
    if max(rgb) <= 0x18:
        return ("#111111", "#181818", "#2f2f2f")

    def scale(factor: float) -> str:
        return "#" + "".join(f"{max(0, int(v * factor)):02x}" for v in rgb)

    return (scale(0.82), scale(0.72), scale(0.55))


def derive_custom_preset(color: object) -> ThemePreset | None:
    """Build the ``custom`` preset from a #RRGGBB background, else None."""
    if not isinstance(color, str) or not _CUSTOM_HEX_RE.fullmatch(color):
        return None
    surface, surface_alt, border = _derive_shades(color)
    red = int(color[1:3], 16)
    green = int(color[3:5], 16)
    blue = int(color[5:7], 16)
    luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255
    text = THEME_PRESETS["amoled"] if luminance < 0.5 else THEME_PRESETS["paper"]
    return ThemePreset(
        key=CUSTOM_KEY, name="Custom", background=color,
        surface=surface, surface_alt=surface_alt, border=border,
        foreground=text.foreground, muted=text.muted, accent=text.accent,
    )


def resolve_preset_key(key: object) -> str:
    """Map a stored theme key to a known preset, falling back to AMOLED."""
    if isinstance(key, str) and key in THEME_PRESETS:
        return key
    if key == CUSTOM_KEY:
        return CUSTOM_KEY
    return DEFAULT_PRESET_KEY


def resolve_preset(data: dict) -> ThemePreset:
    """Active preset for shell config *data*; custom derives its palette."""
    if not isinstance(data, dict):
        return THEME_PRESETS[DEFAULT_PRESET_KEY]
    if data.get("theme") == CUSTOM_KEY:
        derived = derive_custom_preset(data.get("custom_background"))
        if derived is not None:
            return derived
        return THEME_PRESETS[DEFAULT_PRESET_KEY]
    key = data.get("theme", DEFAULT_PRESET_KEY)
    if not isinstance(key, str):
        key = DEFAULT_PRESET_KEY
    return THEME_PRESETS.get(key, THEME_PRESETS[DEFAULT_PRESET_KEY])


def resolve_font_css(key: object, custom_family: object = None) -> str:
    """Map a stored font key to a CSS font-family value."""
    if isinstance(key, str) and key in FONT_FAMILIES:
        return FONT_FAMILIES[key]
    if key == "custom" and isinstance(custom_family, str) and custom_family.strip():
        return custom_family.strip()
    return FONT_FAMILIES[DEFAULT_FONT_KEY]


def resolve_font_family(data: dict) -> str:
    """Shell-theme mirror: font family for shell config *data*."""
    if not isinstance(data, dict):
        return FONT_FAMILIES[DEFAULT_FONT_KEY]
    return resolve_font_css(data.get("font"), data.get("custom_font_family"))


def clamp_text_scale(value: object) -> float:
    """Coerce a stored text scale to the shell's 0.5-2.0 range."""
    try:
        scale = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    return min(2.0, max(0.5, scale))


def resolve_text_scale(data: dict) -> float:
    """Shell-theme mirror: text scale for shell config *data*."""
    if not isinstance(data, dict):
        return 1.0
    return clamp_text_scale(data.get("text_size_scale", 1.0))


def is_dark(data: dict) -> bool:
    """Shell-theme mirror: True when the shell config prefers dark."""
    if not isinstance(data, dict):
        return True
    return bool(data.get("prefer_dark", True))


def load_xxwm_config(path: Path | None = None) -> dict | None:
    """Read the XX-WM shell config; None when absent or corrupt.

    A corrupt shell config must never break this app's launch: the caller
    falls back to libadwaita dark/light instead.
    """
    candidate = path if path is not None else XXWM_CONFIG_PATH
    try:
        raw = Path(candidate).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def load_shell_config(path: object = None) -> dict:
    """Shell-theme mirror: shell config dict, {} when absent or corrupt."""
    if path is None:
        data = load_xxwm_config()
    else:
        data = load_xxwm_config(Path(str(path)))
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class ResolvedTheme:
    preset: ThemePreset
    font_css: str
    text_scale: float
    prefer_dark: bool


def fallback_theme() -> ResolvedTheme:
    """Libadwaita fallback when the shell config is absent or corrupt."""
    preset = THEME_PRESETS[DEFAULT_PRESET_KEY]
    return ResolvedTheme(
        preset=preset,
        font_css=FONT_FAMILIES[DEFAULT_FONT_KEY],
        text_scale=1.0,
        prefer_dark=True,
    )


def theme_from_xxwm_config(data: dict | None) -> ResolvedTheme:
    """Resolve the effective theme from shell config (or the fallback)."""
    if not isinstance(data, dict):
        return fallback_theme()
    return ResolvedTheme(
        preset=resolve_preset(data),
        font_css=resolve_font_css(data.get("font"), data.get("custom_font_family")),
        text_scale=clamp_text_scale(data.get("text_size_scale", 1.0)),
        prefer_dark=is_dark(data),
    )


def adw_color_scheme_name(resolved: ResolvedTheme) -> str:
    """Libadwaita color-scheme name for *resolved* without importing GTK."""
    return "dark" if resolved.prefer_dark else "light"


def css_for_theme(resolved: ResolvedTheme) -> str:
    """Build the app CSS from the resolved theme.

    Every color comes from the preset; lists, rows, entries, and buttons
    follow the theme so no default white surface survives in dark presets.
    """
    preset = resolved.preset
    base_px = 15 * resolved.text_scale
    return (
        ".xxc-root { background-color: %s; color: %s; }\n"
        ".xxc-title { font-family: %s; font-size: %.1fpx; font-weight: bold; }\n"
        ".xxc-body { font-family: %s; font-size: %.1fpx; color: %s; }\n"
        ".xxc-rule { background-color: %s; min-height: 4px; min-width: 56px; }\n"
        "listview { background-color: %s; color: %s; }\n"
        "listview row { background-color: %s; color: %s; "
        "border-bottom: 1px solid %s; }\n"
        "listview row:selected { background-color: %s; color: %s; }\n"
        "entry { background-color: %s; color: %s; }\n"
        "button { background-color: %s; color: %s; }\n"
        % (preset.background, preset.foreground,
           resolved.font_css, base_px + 12.0,
           resolved.font_css, base_px, preset.muted,
           preset.foreground,
           preset.background, preset.foreground,
           preset.background, preset.foreground, preset.border,
           preset.surface_alt, preset.foreground,
           preset.surface, preset.foreground,
           preset.surface, preset.foreground)
    )


def to_css(preset: ThemePreset, font_family: str, scale: float = 1.0) -> str:
    """Shell-theme mirror: CSS for an explicit preset, font, and scale."""
    return css_for_theme(ResolvedTheme(
        preset=preset, font_css=font_family,
        text_scale=clamp_text_scale(scale), prefer_dark=True))


class ThemeWatcher:
    """Hot-reload the shell theme without polling the file itself.

    Editors replace config.json atomically (tmp-file + rename), so a
    monitor on the FILE misses the replace; this watches the parent
    DIRECTORY and refires only for config.json. Headless callers without
    Gio use :meth:`poll`, which compares mtime and content.
    """

    def __init__(self, path: Path | None = None, on_change=None) -> None:
        self._path = Path(path) if path is not None else Path(XXWM_CONFIG_PATH)
        self._on_change = on_change
        self._monitor = None
        self._changed_id = 0
        self._mtime: float | None = None
        self._snapshot: bytes | None = None
        self._record()

    @property
    def path(self) -> Path:
        return self._path

    def _record(self) -> None:
        try:
            raw = self._path.read_bytes()
        except OSError:
            self._mtime = None
            self._snapshot = None
            return
        try:
            self._mtime = os.stat(self._path).st_mtime
        except OSError:
            self._mtime = None
        self._snapshot = raw

    def _wanted(self, changed_path: object) -> bool:
        try:
            name = Path(str(changed_path)).name
        except Exception:
            return False
        return name == self._path.name

    def poll(self) -> bool:
        """Re-read the file; fire the callback once when it changed."""
        try:
            raw = self._path.read_bytes()
        except OSError:
            raw = None
        try:
            mtime = os.stat(self._path).st_mtime if raw is not None else None
        except OSError:
            mtime = None
        if raw == self._snapshot and mtime == self._mtime:
            return False
        self._mtime = mtime
        self._snapshot = raw
        if self._on_change is not None:
            try:
                self._on_change(load_xxwm_config(self._path))
            except Exception:
                pass
        return True

    def start(self) -> None:
        """Attach a Gio directory monitor; no-op when Gio is unavailable."""
        try:
            from gi.repository import Gio
        except Exception:
            return
        try:
            parent = Gio.File.new_for_path(str(self._path.parent))
            self._monitor = parent.monitor_directory(
                Gio.FileMonitorFlags.NONE, None)
            self._changed_id = self._monitor.connect("changed",
                                                     self._on_dir_changed)
        except Exception:
            self._monitor = None
            self._changed_id = 0

    def stop(self) -> None:
        """Detach the directory monitor."""
        monitor, self._monitor = self._monitor, None
        changed_id, self._changed_id = self._changed_id, 0
        if monitor is not None and changed_id:
            try:
                monitor.disconnect(changed_id)
            except Exception:
                pass

    def _on_dir_changed(self, _monitor, changed, _other, _event) -> None:
        try:
            changed_path = changed.get_path()
        except Exception:
            return
        if not self._wanted(changed_path):
            return
        self.poll()
