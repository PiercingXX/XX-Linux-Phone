from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path


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


# Canonical theme presets per design.md spec
# Dark themes (background, surface, surface_alt, border) derived from background
# Light themes use near-black text on light backgrounds
def _derive_shades(bg: str) -> tuple[str, str, str]:
    """Derive surface/surface_alt/border shades from background color."""
    # Simple approach: darken by ~10% for surface, ~15% for surface_alt, ~30% for border
    # Parse hex and adjust
    import re
    match = re.match(r'#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})', bg)
    if not match:
        return ('#111111', '#181818', '#2f2f2f')
    r = int(match.group(1), 16)
    
    def darken(val: int, pct: float) -> str:
        v = max(0, val - int(255 * pct))
        return f'#{v:02x}{v:02x}{v:02x}'
    
    return (darken(r, 0.10), darken(r, 0.15), darken(r, 0.30))

THEME_PRESETS = {
    'amoled': ThemePreset('amoled', 'AMOLED', '#000000', '#111111', '#181818', '#2f2f2f', '#f4f4f4', '#9a9a9a', '#d8d8d8'),
    'graphite': ThemePreset('graphite', 'Graphite', '#111827', '#1f2937', '#2d3748', '#4a5568', '#f7fafc', '#a0aec0', '#718096'),
    'forest': ThemePreset('forest', 'Forest', '#10261B', '#1a3a2a', '#244e3a', '#3d6b56', '#ecf4ee', '#a4b1a7', '#c9d8cc'),
    'ocean': ThemePreset('ocean', 'Ocean', '#0F1C2E', '#182a42', '#203856', '#34506e', '#edf4f7', '#9fb0bb', '#cad8df'),
    'paper': ThemePreset('paper', 'Paper', '#F3EEE2', '#e0d8cb', '#ccc4b4', '#b09c85', '#151515', '#585147', '#262626'),
    'mist': ThemePreset('mist', 'Mist', '#E6EDF5', '#d0d8e2', '#bac4cf', '#9aa8ba', '#151a1f', '#55606c', '#2f3943'),
    'aura': ThemePreset('aura', 'Aura', '#0d0b14', '#14112a', '#1e1a3a', '#3d3066', '#f0eeff', '#9080c0', '#a855f7'),
    'burgundy': ThemePreset('burgundy', 'Burgundy', '#2A1018', '#3b1a24', '#4c2330', '#6e3a4c', '#f6edef', '#ab949c', '#d9b3bf'),
}

FONT_FAMILIES = {
    'system-light': 'Sans Light',
    'space-mono': 'Space Mono, Monospace',
    'jetbrains-mono': 'JetBrains Mono, Monospace',
    'jetbrains-mono-nerd': 'JetBrainsMono Nerd Font, JetBrains Mono, Monospace',
}

# Semantic colors that must not track the theme (universal meanings).
DANGER_RED = '#ff6b6b'
ON_DANGER_FG = '#ffffff'
WARNING_ORANGE = '#ff9a3c'
DESTRUCTIVE_TINT_BG = '#2a1010'

DEFAULT_CONFIG = {
    'theme': 'amoled',
    'font': 'jetbrains-mono-nerd',
    'pinned': [],
    'hidden_apps': [],
    'prefer_dark': True,
    'auto_lock_timeout': 120,
    'text_size_scale': 1.0,
    'home_alignment': 'center',
    'update_script': '~/.scripts/PiercingXX-Settings-Menu/update-system.sh',
    'update_last_check': 0.0,
    'update_snooze_until': 0.0,
    'home_slots': [],
    'search_auto_launch': False,
    'preload_gesture_apps': False,
    'default_layout_applied': False,
    'app_labels': {},
    'muted_apps': {},
    'lock_screen_notifications': 'summary',
    'dnd_enabled': False,
    'dnd_schedules': [],
    'dnd_starred_numbers': [],
    'focus_enabled': False,
    'focus_apps': [],
    'focus_schedules': [],
    'focus_break_until': 0.0,
    'sound_ringtone': True,
    'sound_notifications': True,
    'widgets': {
        'time': {'enabled': True, 'order': 1, 'tap': 'default'},
        'date': {'enabled': True, 'order': 2, 'tap': 'default'},
        'weather': {'enabled': True, 'order': 3, 'tap': 'refresh'},
        'battery': {'enabled': True, 'order': 4, 'tap': 'default'},
    },
    'weather_lat': None,
    'weather_lon': None,
}


class ShellConfig:
    def __init__(self) -> None:
        self.config_dir = Path.home() / '.config' / 'xx-wm'
        self.config_path = self.config_dir / 'config.json'
        # One-time migration from the pre-rename config dir (piercing-shell).
        legacy_dir = Path.home() / '.config' / 'piercing-shell'
        if legacy_dir.is_dir() and not self.config_dir.exists():
            legacy_dir.rename(self.config_dir)
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        if not self.config_path.exists():
            return

        try:
            loaded = json.loads(self.config_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(loaded, dict):
            return

        self.data.update(loaded)

    def save(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(self.data, indent=2) + '\n', encoding='utf-8')

    @property
    def theme(self) -> ThemePreset:
        key = str(self.data.get('theme', DEFAULT_CONFIG['theme']))
        return THEME_PRESETS.get(key, THEME_PRESETS[DEFAULT_CONFIG['theme']])

    @property
    def font_family(self) -> str:
        key = str(self.data.get('font', DEFAULT_CONFIG['font']))
        if key == 'custom':
            family = self.data.get('custom_font_family')
            if family:
                return str(family)
        return FONT_FAMILIES.get(key, FONT_FAMILIES[DEFAULT_CONFIG['font']])

    @property
    def pinned(self) -> list[str]:
        pinned = self.data.get('pinned', DEFAULT_CONFIG['pinned'])
        if isinstance(pinned, list):
            return [str(item) for item in pinned]
        return []

    @property
    def prefer_dark(self) -> bool:
        return bool(self.data.get('prefer_dark', DEFAULT_CONFIG['prefer_dark']))

    def set_theme(self, key: str) -> None:
        if key in THEME_PRESETS:
            self.data['theme'] = key
            self.save()

    def set_font(self, key: str) -> None:
        if key in FONT_FAMILIES or (key == 'custom' and self.data.get('custom_font_family')):
            self.data['font'] = key
            self.save()

    def set_prefer_dark(self, enabled: bool) -> None:
        self.data['prefer_dark'] = enabled
        self.save()

    def set_pinned(self, app_ids: list[str]) -> None:
        self.data['pinned'] = app_ids[:8]
        self.save()

    @property
    def pin_hash(self) -> str | None:
        value = self.data.get('pin_hash')
        return str(value) if value else None

    def set_pin(self, pin: str) -> None:
        self.data['pin_hash'] = hashlib.sha256(pin.encode()).hexdigest()
        self.save()

    def verify_pin(self, pin: str) -> bool:
        stored = self.pin_hash
        if stored is None:
            return True
        return hmac.compare_digest(stored, hashlib.sha256(pin.encode()).hexdigest())

    @property
    def auto_lock_timeout(self) -> int:
        val = self.data.get('auto_lock_timeout', DEFAULT_CONFIG['auto_lock_timeout'])
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            return int(DEFAULT_CONFIG['auto_lock_timeout'])

    def set_auto_lock_timeout(self, seconds: int) -> None:
        self.data['auto_lock_timeout'] = max(0, seconds)
        self.save()

    @property
    def hidden_apps(self) -> list[str]:
        val = self.data.get('hidden_apps', [])
        if isinstance(val, list):
            return [str(x) for x in val]
        return []

    def set_hidden_apps(self, app_ids: list[str]) -> None:
        self.data['hidden_apps'] = list(app_ids)
        self.save()

    @property
    def text_size_scale(self) -> float:
        try:
            return max(0.5, min(2.0, float(self.data.get('text_size_scale', 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def set_text_size_scale(self, scale: float) -> None:
        self.data['text_size_scale'] = max(0.5, min(2.0, round(scale, 2)))
        self.save()

    @property
    def home_alignment(self) -> str:
        val = str(self.data.get('home_alignment', 'center'))
        return val if val in ('left', 'center', 'right') else 'center'

    def set_home_alignment(self, alignment: str) -> None:
        if alignment in ('left', 'center', 'right'):
            self.data['home_alignment'] = alignment
            self.save()

    @property
    def update_script(self) -> str:
        val = self.data.get('update_script', DEFAULT_CONFIG['update_script'])
        return str(val) if val else str(DEFAULT_CONFIG['update_script'])

    @property
    def update_last_check(self) -> float:
        try:
            return max(0.0, float(self.data.get('update_last_check', 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def set_update_last_check(self, timestamp: float) -> None:
        self.data['update_last_check'] = max(0.0, timestamp)
        self.save()

    @property
    def update_snooze_until(self) -> float:
        try:
            return max(0.0, float(self.data.get('update_snooze_until', 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def set_update_snooze_until(self, timestamp: float) -> None:
        self.data['update_snooze_until'] = max(0.0, timestamp)
        self.save()

    @property
    def launch_counts(self) -> dict[str, int]:
        val = self.data.get('launch_counts', {})
        if isinstance(val, dict):
            return {str(k): int(v) for k, v in val.items()}
        return {}

    def record_launch(self, app_id: str) -> None:
        counts = self.launch_counts
        counts[app_id] = counts.get(app_id, 0) + 1
        self.data['launch_counts'] = counts
        self.save()

    @property
    def default_layout_applied(self) -> bool:
        return bool(self.data.get('default_layout_applied', False))

    def set_default_layout_applied(self, value: bool) -> None:
        self.data['default_layout_applied'] = bool(value)
        self.save()

    @property
    def home_slots(self) -> list[dict]:
        slots = self.data.get('home_slots', DEFAULT_CONFIG['home_slots'])
        if isinstance(slots, list):
            return slots
        return []

    def set_home_slots(self, slots: list[dict]) -> None:
        validated = []
        for slot in slots[:8]:
            if not isinstance(slot, dict):
                continue
            slot_type = slot.get('type')
            if slot_type not in ('app', 'folder'):
                continue
            validated.append({
                'type': slot_type,
                'label': str(slot.get('label', '')),
                'app_id': str(slot.get('app_id')) if slot.get('app_id') else None,
                'cmd': slot.get('cmd') if isinstance(slot.get('cmd'), list) else None,
                'folder': slot.get('folder') if isinstance(slot.get('folder'), list) else None,
            })
        self.data['home_slots'] = validated
        self.save()
    @property
    def app_labels(self) -> dict[str, str]:
        val = self.data.get('app_labels', {})
        if isinstance(val, dict):
            return {str(k): str(v) for k, v in val.items()}
        return {}

    def set_app_label(self, app_id: str, label: str | None) -> None:
        labels = self.app_labels
        if label:
            labels[app_id] = label
        else:
            labels.pop(app_id, None)
        self.data['app_labels'] = labels
        self.save()

    def label_for(self, app_id: str, fallback: str) -> str:
        return self.app_labels.get(app_id, fallback)

    @property
    def muted_apps(self) -> dict[str, float]:
        val = self.data.get('muted_apps', {})
        if not isinstance(val, dict):
            return {}
        out: dict[str, float] = {}
        for k, v in val.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    def _norm_app_id(app_id: str) -> str:
        # Mute keys come from drawer app ids (with .desktop) and notification
        # desktop-entry hints (usually without); compare them stripped.
        return app_id[:-8] if app_id.endswith('.desktop') else app_id

    def set_app_muted(self, app_id: str, until_epoch: float, now: float | None = None) -> None:
        if now is None:
            import time
            now = time.time()
        muted = {k: v for k, v in self.muted_apps.items() if v > now}
        muted[self._norm_app_id(app_id)] = float(until_epoch)
        self.data['muted_apps'] = muted
        self.save()

    def is_app_muted(self, app_id: str, now: float | None = None) -> bool:
        if not app_id:
            return False
        if now is None:
            import time
            now = time.time()
        wanted = self._norm_app_id(app_id).casefold()
        return any(
            until > now and self._norm_app_id(key).casefold() == wanted
            for key, until in self.muted_apps.items()
        )

    @property
    def lock_screen_notifications(self) -> str:
        val = str(self.data.get('lock_screen_notifications', 'summary'))
        return val if val in ('summary', 'count', 'off') else 'summary'

    @property
    def sound_ringtone(self) -> bool:
        return bool(self.data.get('sound_ringtone', True))

    @property
    def sound_notifications(self) -> bool:
        return bool(self.data.get('sound_notifications', True))

    @property
    def search_auto_launch(self) -> bool:
        return bool(self.data.get('search_auto_launch', False))

    def set_search_auto_launch(self, enabled: bool) -> None:
        self.data['search_auto_launch'] = bool(enabled)
        self.save()

    @property
    def preload_gesture_apps(self) -> bool:
        """Opt-in warm-up of swipe-bound apps at session start. Default off:
        preloading is counter to the minimalism directive on weak hardware."""
        return bool(self.data.get('preload_gesture_apps', DEFAULT_CONFIG['preload_gesture_apps']))

    def set_preload_gesture_apps(self, enabled: bool) -> None:
        self.data['preload_gesture_apps'] = bool(enabled)
        self.save()

    @property
    def widgets(self) -> dict[str, dict]:
        """Widget config merged over defaults; unknown keys ignored."""
        defaults = DEFAULT_CONFIG['widgets']
        val = self.data.get('widgets')
        merged: dict[str, dict] = {}
        for key, default in defaults.items():
            entry = dict(default)
            if isinstance(val, dict) and isinstance(val.get(key), dict):
                user = val[key]
                if isinstance(user.get('enabled'), bool):
                    entry['enabled'] = user['enabled']
                try:
                    entry['order'] = int(user.get('order', entry['order']))
                except (TypeError, ValueError):
                    pass
                tap = user.get('tap')
                if tap in ('default', 'none', 'refresh') or (
                    isinstance(tap, dict) and tap.get('app')
                ):
                    entry['tap'] = tap
            merged[key] = entry
        return merged

    def ordered_widgets(self) -> list[tuple[str, dict]]:
        widgets = self.widgets
        return sorted(
            ((k, v) for k, v in widgets.items() if v.get('enabled')),
            key=lambda item: item[1].get('order', 99),
        )

    @property
    def custom_font_family(self) -> str | None:
        val = self.data.get('custom_font_family')
        return str(val) if val else None

    @property
    def custom_background(self) -> str | None:
        val = self.data.get('custom_background')
        return str(val) if val else None

    def set_custom_background(self, color: str) -> bool:
        """Set custom background color. Returns True if valid."""
        import re
        if not re.match(r'^#[0-9a-fA-F]{6}$', color):
            return False
        self.data['theme'] = 'custom'
        self.data['custom_background'] = color
        self.save()
        return True


def should_preload_gesture_apps(config: 'ShellConfig', real_session: bool) -> bool:
    """Gate for the session-start app preload (20.4).

    Preloading warms swipe-bound apps into RAM so a gesture opens a resident
    process instead of cold-starting it. It is counter to the minimalism
    directive on weak hardware, so it only runs in a real XX-WM session
    (never under a host shell over Phosh) AND with the opt-in
    ``preload_gesture_apps`` config key set (default off).
    """
    return bool(real_session) and config.preload_gesture_apps


def install_custom_font(config: 'ShellConfig', path: str,
                        run: object = None) -> tuple[bool, str]:
    """Install a user .ttf/.otf into ~/.local/share/fonts and switch the shell
    to it (`font: 'custom'`, `custom_font_family`). Custom font files are
    deliberately excluded from backups. Returns (ok, message)."""
    import shutil
    import subprocess
    runner = run or subprocess.run

    src = Path(path).expanduser()
    if src.suffix.lower() not in ('.ttf', '.otf'):
        return False, 'Font must be a .ttf or .otf file.'
    if not src.is_file():
        return False, f'No such file: {src}'

    dest_dir = Path.home() / '.local' / 'share' / 'fonts'
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
    except OSError as error:
        return False, f'Could not install font: {error}'

    family = src.stem
    try:
        result = runner(['fc-scan', '--format', '%{family}', str(dest)],
                        capture_output=True, text=True, timeout=5)
        scanned = (result.stdout or '').split(',')[0].strip()
        if scanned:
            family = scanned
    except Exception:
        pass

    try:
        runner(['fc-cache', '-f'], capture_output=True, timeout=30)
    except Exception:
        pass

    config.data['custom_font_family'] = family
    config.data['font'] = 'custom'
    config.save()
    return True, family

