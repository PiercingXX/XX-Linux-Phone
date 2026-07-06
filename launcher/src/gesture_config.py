from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path.home() / '.config' / 'piercing-shell' / 'gestures.json'

# Gesture slot → default action
_DEFAULTS: dict[str, str] = {
    'swipe_down_top':        'notification_shade',
    'swipe_up_short':        'home',
    'swipe_up_long':         'app_switcher',
    'swipe_left_edge':       'back',
    'long_press_bottom':     'search',
    'double_tap_home':       'lock_screen',
    'long_press_home':       'settings',
    'swipe_left_home':       'camera',
    'swipe_right_home':      'dialer',
    'squeeze':               'assistant',
    'fingerprint_swipe':     'notification_shade',
    'double_press_power':    'camera',
}

VALID_ACTIONS: frozenset[str] = frozenset({
    'home', 'app_switcher', 'notification_shade', 'back',
    'search', 'lock_screen', 'settings', 'camera', 'dialer',
    'assistant', 'none',
})

# Human-readable names for the settings UI
ACTION_LABELS: dict[str, str] = {
    'home':                'Home',
    'app_switcher':        'App Switcher',
    'notification_shade':  'Notification Shade',
    'back':                'Back',
    'search':              'Search',
    'lock_screen':         'Lock Screen',
    'settings':            'Settings',
    'camera':              'Camera',
    'dialer':              'Dialer',
    'assistant':           'Assistant',
    'none':                'Do Nothing',
}

GESTURE_LABELS: dict[str, str] = {
    'swipe_down_top':       'Swipe down from top',
    'swipe_up_short':       'Short swipe up from bottom',
    'swipe_up_long':        'Long swipe up from bottom',
    'swipe_left_edge':      'Swipe in from right edge',
    'long_press_bottom':    'Long-press bottom edge',
    'double_tap_home':      'Double-tap home background',
    'long_press_home':      'Long-press home background',
    'swipe_left_home':      'Swipe left on home',
    'swipe_right_home':     'Swipe right on home',
    'squeeze':              'Squeeze (Active Edge)',
    'fingerprint_swipe':    'Fingerprint sensor swipe',
    'double_press_power':   'Double-press power button',
}


class GestureConfig:
    def __init__(self) -> None:
        self._map: dict[str, str] = dict(_DEFAULTS)
        self._load()

    def _load(self) -> None:
        if not _CONFIG_PATH.exists():
            return
        try:
            raw = json.loads(_CONFIG_PATH.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        for key, action in raw.items():
            if key in _DEFAULTS and action in VALID_ACTIONS:
                self._map[key] = action

    def save(self) -> None:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(self._map, indent=2) + '\n', encoding='utf-8')

    def get(self, gesture: str) -> str:
        return self._map.get(gesture, 'none')

    def set(self, gesture: str, action: str) -> None:
        if gesture not in _DEFAULTS:
            raise ValueError(f'Unknown gesture: {gesture}')
        if action not in VALID_ACTIONS:
            raise ValueError(f'Unknown action: {action}')
        self._map[gesture] = action
        self.save()

    def reset(self, gesture: str) -> None:
        if gesture in _DEFAULTS:
            self._map[gesture] = _DEFAULTS[gesture]
            self.save()

    def all(self) -> list[tuple[str, str]]:
        """Return (gesture_key, action) pairs in definition order."""
        return [(k, self._map.get(k, 'none')) for k in _DEFAULTS]
