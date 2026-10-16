from __future__ import annotations

import json
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

THEME_PRESETS = {
    'amoled': {'display': 'AMOLED', 'background': '#000000', 'dark': True},
    'graphite': {'display': 'Graphite', 'background': '#111827', 'dark': True},
    'forest': {'display': 'Forest', 'background': '#10261B', 'dark': True},
    'ocean': {'display': 'Ocean', 'background': '#0F1C2E', 'dark': True},
    'paper': {'display': 'Paper', 'background': '#F3EEE2', 'dark': False},
    'mist': {'display': 'Mist', 'background': '#E6EDF5', 'dark': False},
    'aura': {'display': 'Aura', 'background': '#0d0b14', 'dark': True},
    'burgundy': {'display': 'Burgundy', 'background': '#2A1018', 'dark': True},
}

FONT_FAMILIES = {
    'system-light': 'Sans Light',
    'space-mono': 'Space Mono, Monospace',
    'jetbrains-mono': 'JetBrains Mono, Monospace',
    'jetbrains-mono-nerd': 'JetBrainsMono Nerd Font, JetBrains Mono, Monospace',
}

#: Per-preset surfaces for list rows, entries and the compose area.
#: Values mirror the shell's ``THEME_PRESETS``/``shell_theme`` map so the
#: app matches the shell; every color comes from here, never a hardcoded
#: white — dark presets must never render a default white list.
_CSS_DETAIL = {
    'amoled': {'surface': '#111111', 'surface_alt': '#181818', 'border': '#2f2f2f',
               'foreground': '#f4f4f4', 'muted': '#9a9a9a', 'accent': '#d8d8d8'},
    'graphite': {'surface': '#1f2937', 'surface_alt': '#2d3748', 'border': '#4a5568',
                 'foreground': '#f7fafc', 'muted': '#a0aec0', 'accent': '#718096'},
    'forest': {'surface': '#1a3a2a', 'surface_alt': '#244e3a', 'border': '#3d6b56',
               'foreground': '#ecf4ee', 'muted': '#a4b1a7', 'accent': '#c9d8cc'},
    'ocean': {'surface': '#182a42', 'surface_alt': '#203856', 'border': '#34506e',
              'foreground': '#edf4f7', 'muted': '#9fb0bb', 'accent': '#cad8df'},
    'paper': {'surface': '#e0d8cb', 'surface_alt': '#ccc4b4', 'border': '#b09c85',
              'foreground': '#151515', 'muted': '#585147', 'accent': '#262626'},
    'mist': {'surface': '#d0d8e2', 'surface_alt': '#bac4cf', 'border': '#9aa8ba',
             'foreground': '#151a1f', 'muted': '#55606c', 'accent': '#2f3943'},
    'aura': {'surface': '#14112a', 'surface_alt': '#1e1a3a', 'border': '#3d3066',
             'foreground': '#f0eeff', 'muted': '#9080c0', 'accent': '#a855f7'},
    'burgundy': {'surface': '#3b1a24', 'surface_alt': '#4c2330', 'border': '#6e3a4c',
                 'foreground': '#f6edef', 'muted': '#ab949c', 'accent': '#d9b3bf'},
}

DEFAULT_THEME = 'amoled'
DEFAULT_FONT = 'jetbrains-mono-nerd'

_ANDROID_KEY_ALIASES = {
    'amoled-night': 'amoled',
    'forest-night': 'forest',
    'ocean-drift': 'ocean',
}

XXWM_CONFIG_PATH = Path.home() / '.config/xx-wm/config.json'


def normalize_preset_key(key: str | None) -> str:
    if not key:
        return DEFAULT_THEME
    key = str(key).strip().lower()
    if key in THEME_PRESETS:
        return key
    if key in _ANDROID_KEY_ALIASES:
        return _ANDROID_KEY_ALIASES[key]
    if key == 'custom':
        return key
    return DEFAULT_THEME


def load_xxwm_config(path: Path | None = None) -> dict:
    target = Path(path) if path is not None else XXWM_CONFIG_PATH
    try:
        raw = target.read_text(encoding='utf-8')
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def resolve_theme(shell_config: dict, manual_preset: str | None = None) -> dict:
    if manual_preset:
        key = normalize_preset_key(manual_preset)
    else:
        key = normalize_preset_key(shell_config.get('theme'))
    if key == 'custom':
        background = str(shell_config.get('custom_background') or '#000000')
        return {'key': 'custom', 'display': 'Custom', 'background': background,
                'dark': True, **_CSS_DETAIL['amoled']}
    preset = THEME_PRESETS.get(key, THEME_PRESETS[DEFAULT_THEME])
    return {'key': key, **preset, **_CSS_DETAIL.get(key, _CSS_DETAIL['amoled'])}


def resolve_font(shell_config: dict) -> str:
    key = str(shell_config.get('font', DEFAULT_FONT))
    if key == 'custom':
        family = shell_config.get('custom_font_family')
        if family:
            return str(family)
        return FONT_FAMILIES[DEFAULT_FONT]
    return FONT_FAMILIES.get(key, FONT_FAMILIES[DEFAULT_FONT])


MANUAL_FONT_FAMILIES = {
    'space-mono': 'Space Mono, Monospace',
    'jetbrains-mono': 'JetBrains Mono, Monospace',
    'system': 'Sans Light',
}


def manual_font_family(font_mode: str | None) -> str:
    """Font family for the in-app font toggle (manual theme mode)."""
    return MANUAL_FONT_FAMILIES.get(
        str(font_mode or '').strip().lower(),
        MANUAL_FONT_FAMILIES['space-mono'],
    )


def watch_xxwm_config(on_change, path: Path | None = None):
    """Hot-reload the shell config: call ``on_change(config)`` on edits.

    Watches the config *directory*, not the file: editors and the shell
    write the file atomically (rename over it), which silently kills a
    file monitor. Events for other directory entries are ignored; the
    callback always receives a fresh :func:`load_xxwm_config` dict
    (``{}`` when the file is missing or invalid, so the prevailing
    fallback applies). Returns the monitor to keep alive, or None when
    the directory cannot be watched. Manual-wins stays with the caller:
    it decides whether the fresh config replaces a manual pick.
    """
    target = Path(path) if path is not None else XXWM_CONFIG_PATH
    if not target.parent.is_dir():
        return None
    try:
        monitor = Gio.File.new_for_path(str(target.parent)).monitor_directory(
            Gio.FileMonitorFlags.NONE, None,
        )
    except Exception:
        return None
    if monitor is None:
        return None

    def _on_changed(_monitor, changed_file, _other, _event) -> None:
        try:
            changed_path = changed_file.get_path()
        except Exception:
            changed_path = None
        if changed_path is not None and Path(changed_path).name != target.name:
            return
        try:
            on_change(load_xxwm_config(target))
        except Exception:
            pass

    try:
        monitor.connect("changed", _on_changed)
    except Exception:
        return None
    return monitor


def css_for_theme(theme: dict, font_family: str, scale: float = 1.0) -> str:
    bg = theme['background']
    detail = _CSS_DETAIL.get(theme.get('key'), _CSS_DETAIL['amoled'])
    fg = str(theme.get('foreground') or detail['foreground'])
    muted = str(theme.get('muted') or detail['muted'])
    surface = str(theme.get('surface') or detail['surface'])
    surface_alt = str(theme.get('surface_alt') or detail['surface_alt'])
    border = str(theme.get('border') or detail['border'])
    try:
        size = 13.0 * float(scale)
    except (TypeError, ValueError):
        size = 13.0
    size = max(6.5, min(26.0, size))
    title_size = min(32.0, size * 1.6)
    return (
        f"window, .background {{ background-color: {bg}; color: {fg}; "
        f"font-family: {font_family}; font-size: {size:.1f}pt; }}\n"
        f".txxt-title {{ font-size: {title_size:.1f}pt; color: {fg}; }}\n"
        f".txxt-body {{ color: {fg}; }}\n"
        f".txxt-muted, .txxt-timestamp {{ color: {muted}; }}\n"
        f".txxt-detail {{ color: {muted}; }}\n"
        f".txxt-badge {{ background-color: {surface_alt}; color: {fg}; "
        f"border: 1px solid {border}; border-radius: 9px; }}\n"
        f"listview {{ background-color: {bg}; color: {fg}; }}\n"
        f"listview row {{ background-color: {bg}; color: {fg}; "
        f"border-bottom: 1px solid {border}; }}\n"
        f"listview row:selected {{ background-color: {surface_alt}; "
        f"color: {fg}; }}\n"
        f"entry, textview {{ background-color: {surface}; color: {fg}; "
        f"border: 1px solid {border}; }}\n"
        f"entry:focus, textview:focus {{ border-color: {muted}; }}\n"
        f".txxt-compose {{ background-color: {surface}; color: {fg}; "
        f"border-top: 1px solid {border}; }}\n"
    )
