"""
Backup and restore — versioned JSON export of the shell configuration.

Scope (design.md "Backup / restore"): home slots, folders + membership,
pins, app rename labels, widget config + tap actions, theme, hidden apps,
gestures, prefs (sounds, DnD, Focus, lock screen, weather coords, search).
Excluded: PIN hash (security), launch counts (noise), active mutes and
focus breaks (transient), custom font files (only the family name travels).
Restore validates the entire payload first and never writes on invalid input.
"""
from __future__ import annotations

import re

_BOOL_KEYS = (
    'prefer_dark',
    'search_auto_launch',
    'sound_ringtone',
    'sound_notifications',
    'dnd_enabled',
    'focus_enabled',
)

_LIST_KEYS = (
    'hidden_apps',
    'dnd_schedules',
    'dnd_starred_numbers',
    'focus_apps',
    'focus_schedules',
)


def export_backup(config) -> dict:
    backup = {
        'version': 1,
        'home_slots': config.home_slots,
        'app_labels': config.data.get('app_labels', {}),
        'pinned': config.pinned,
        'widgets': config.data.get('widgets', {}),
        'theme': config.data.get('theme', 'amoled'),
        'font': config.data.get('font', 'jetbrains-mono-nerd'),
        'text_size_scale': config.text_size_scale,
        'home_alignment': config.home_alignment,
        'auto_lock_timeout': config.auto_lock_timeout,
        'lock_screen_notifications': config.lock_screen_notifications,
        'weather_lat': config.data.get('weather_lat'),
        'weather_lon': config.data.get('weather_lon'),
    }
    for key in _BOOL_KEYS:
        backup[key] = bool(config.data.get(key, False))
    for key in _LIST_KEYS:
        val = config.data.get(key, [])
        backup[key] = val if isinstance(val, list) else []

    if config.data.get('theme') == 'custom' and config.custom_background:
        backup['custom_background'] = config.custom_background
    if config.data.get('font') == 'custom':
        backup['custom_font_family'] = config.data.get('custom_font_family', '')

    try:
        from gesture_config import GestureConfig
        backup['gestures'] = dict(GestureConfig().all())
    except Exception:
        pass

    return backup


def validate_backup(payload: dict) -> tuple[bool, str | None]:
    """Validate the entire payload; restore only runs on a clean pass."""
    if not isinstance(payload, dict):
        return False, 'Payload must be a dictionary'

    if payload.get('version') != 1:
        return False, f'Unsupported version: {payload.get("version")}'

    home_slots = payload.get('home_slots', [])
    if not isinstance(home_slots, list):
        return False, 'home_slots must be a list'
    if len(home_slots) > 8:
        return False, 'home_slots must have at most 8 entries'
    for i, slot in enumerate(home_slots):
        if not isinstance(slot, dict):
            return False, f'home_slots[{i}] must be a dict'
        if slot.get('type') not in ('app', 'folder'):
            return False, f'home_slots[{i}].type must be app or folder'

    if not isinstance(payload.get('app_labels', {}), dict):
        return False, 'app_labels must be a dict'

    widgets = payload.get('widgets', {})
    if not isinstance(widgets, dict):
        return False, 'widgets must be a dict'
    for name, conf in widgets.items():
        if not isinstance(conf, dict):
            return False, f'widgets[{name}] must be a dict'

    if not isinstance(payload.get('theme', ''), str):
        return False, 'theme must be a string'
    if payload.get('theme') == 'custom':
        custom_bg = payload.get('custom_background')
        if not isinstance(custom_bg, str) or not re.match(r'^#[0-9a-fA-F]{6}$', custom_bg):
            return False, 'custom_background must be #RRGGBB when theme is custom'

    if not isinstance(payload.get('font', ''), str):
        return False, 'font must be a string'

    if 'custom_font_family' in payload:
        family = payload['custom_font_family']
        if not isinstance(family, str):
            return False, 'custom_font_family must be a string'
        from font_theme import sanitize_font_family
        payload['custom_font_family'] = sanitize_font_family(family)

    try:
        scale = float(payload.get('text_size_scale', 1.0))
        if not (0.5 <= scale <= 2.0):
            return False, 'text_size_scale must be between 0.5 and 2.0'
    except (TypeError, ValueError):
        return False, 'text_size_scale must be a number'

    try:
        if int(payload.get('auto_lock_timeout', 120)) < 0:
            return False, 'auto_lock_timeout must be non-negative'
    except (TypeError, ValueError):
        return False, 'auto_lock_timeout must be an integer'

    if payload.get('home_alignment', 'center') not in ('left', 'center', 'right'):
        return False, 'home_alignment must be left/center/right'

    if payload.get('lock_screen_notifications', 'summary') not in ('summary', 'count', 'off'):
        return False, 'lock_screen_notifications must be summary/count/off'

    for key in _BOOL_KEYS:
        if key in payload and not isinstance(payload[key], bool):
            return False, f'{key} must be a boolean'

    for key in _LIST_KEYS + ('pinned',):
        if key in payload and not isinstance(payload[key], list):
            return False, f'{key} must be a list'

    for key in ('weather_lat', 'weather_lon'):
        if payload.get(key) is not None:
            try:
                float(payload[key])
            except (TypeError, ValueError):
                return False, f'{key} must be a number or null'

    gestures = payload.get('gestures', {})
    if not isinstance(gestures, dict):
        return False, 'gestures must be a dict'
    # gesture_config's own slot/value rule: actions are valid everywhere,
    # IPC verbs (gesture_bindings lisgd slots) only on the system-level slots.
    from gesture_config import _DEFAULTS, _is_valid_value
    for key, action in gestures.items():
        if key not in _DEFAULTS:
            return False, f'unknown gesture: {key}'
        if not isinstance(action, str) or not _is_valid_value(key, action):
            return False, f'unknown gesture action: {action}'

    return True, None


def restore_backup(config, payload: dict) -> bool:
    """Apply a validated payload: one config write plus one gestures write.
    Invalid payload → zero writes."""
    is_valid, _error = validate_backup(payload)
    if not is_valid:
        return False

    data = config.data
    passthrough = (
        ('app_labels', dict), ('widgets', dict), ('theme', str),
        ('custom_background', str), ('font', str), ('custom_font_family', str),
        ('home_alignment', str), ('lock_screen_notifications', str),
        ('pinned', list),
    )
    for key, expected in passthrough:
        if isinstance(payload.get(key), expected):
            data[key] = payload[key]
    for key in _BOOL_KEYS:
        if key in payload:
            data[key] = bool(payload[key])
    for key in _LIST_KEYS:
        if key in payload:
            data[key] = list(payload[key])
    for key in ('weather_lat', 'weather_lon'):
        if key in payload:
            data[key] = payload[key]
    if 'text_size_scale' in payload:
        data['text_size_scale'] = max(0.5, min(2.0, float(payload['text_size_scale'])))
    if 'auto_lock_timeout' in payload:
        data['auto_lock_timeout'] = max(0, int(payload['auto_lock_timeout']))

    # set_home_slots validates slot shape and performs the single save
    config.set_home_slots(payload.get('home_slots', []))

    if 'gestures' in payload:
        try:
            from gesture_config import GestureConfig
            gc = GestureConfig()
            for key, action in payload['gestures'].items():
                gc._map[key] = action
            gc.save()
        except Exception:
            pass

    return True
