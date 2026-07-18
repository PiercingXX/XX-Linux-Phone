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


THEME_PRESETS = {
    'amoled': ThemePreset('amoled', 'AMOLED', '#000000', '#111111', '#181818', '#2f2f2f', '#f4f4f4', '#9a9a9a', '#d8d8d8'),
    'graphite': ThemePreset('graphite', 'Graphite', '#141414', '#1d1d1d', '#242424', '#343434', '#f0f0f0', '#a0a0a0', '#d2d2d2'),
    'forest': ThemePreset('forest', 'Forest', '#101612', '#172019', '#1e2922', '#314036', '#ecf4ee', '#a4b1a7', '#c9d8cc'),
    'ocean': ThemePreset('ocean', 'Ocean', '#10161a', '#182129', '#1f2a34', '#32404c', '#edf4f7', '#9fb0bb', '#cad8df'),
    'paper': ThemePreset('paper', 'Paper', '#f4f1ea', '#ede6db', '#e4dccf', '#d0c4b2', '#151515', '#585147', '#262626'),
    'mist': ThemePreset('mist', 'Mist', '#e8ecef', '#dce2e6', '#d1d8de', '#bcc6cd', '#151a1f', '#55606c', '#2f3943'),
    'aura': ThemePreset('aura', 'Aura', '#0d0b14', '#14112a', '#1e1a3a', '#3d3066', '#f0eeff', '#9080c0', '#a855f7'),
}

FONT_FAMILIES = {
    'system-light': 'Sans Light',
    'space-mono': 'Space Mono, Monospace',
    'jetbrains-mono': 'JetBrains Mono, Monospace',
}

DEFAULT_CONFIG = {
    'theme': 'graphite',
    'font': 'space-mono',
    'pinned': [],
    'hidden_apps': [],
    'prefer_dark': True,
    'auto_lock_timeout': 120,
    'text_size_scale': 1.0,
    'home_alignment': 'left',
    'update_script': '~/.scripts/PiercingXX-Settings-Menu/update-system.sh',
    'update_last_check': 0.0,
    'update_snooze_until': 0.0,
}


class ShellConfig:
    def __init__(self) -> None:
        self.config_dir = Path.home() / '.config' / 'piercing-shell'
        self.config_path = self.config_dir / 'config.json'
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
        if key in FONT_FAMILIES:
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
        val = str(self.data.get('home_alignment', 'left'))
        return val if val in ('left', 'center', 'right') else 'left'

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