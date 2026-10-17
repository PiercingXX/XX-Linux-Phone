"""Keyboard layout picker (lane sm_device, ref keyboard-layout.sh).

The session script owns the boot default from the `keyboard_layout`
config key (default `us+colemak`); this picker applies the choice live
with gsettings, which phoc and the on-screen keyboard both read.
Unprivileged:
fire-and-forget gsettings writes, never waited on.
"""
from __future__ import annotations

import re
import subprocess

import settings_page
import settings_ui as ui

DEFAULT_KEY = 'us+colemak'

KEYBOARD_PRESETS = (
    ('US Colemak', 'us', 'colemak'),
    ('Colemak-DH', 'us', 'colemak_dh'),
    ('QWERTY', 'us', ''),
    ('Dvorak', 'us', 'dvorak'),
    ('UK', 'gb', ''),
    ('DE', 'de', ''),
)

_LAYOUT_RE = re.compile(r'^[a-z][a-z0-9-]{1,23}$')
_VARIANT_RE = re.compile(r'^[a-z0-9_-]{1,32}$')


def parse_layout_key(value: str) -> tuple[str, str]:
    """'us+colemak' → ('us', 'colemak'); garbage falls back to default."""
    text = str(value or '').strip().lower()
    if '+' in text:
        layout, _, variant = text.partition('+')
    else:
        layout, variant = text, ''
    if not _LAYOUT_RE.match(layout):
        return ('us', 'colemak')
    if variant and not _VARIANT_RE.match(variant):
        return (layout, '')
    return (layout, variant)


def format_layout_key(layout: str, variant: str) -> str:
    """('us', 'colemak') → 'us+colemak' for the config key."""
    layout = str(layout or '').strip().lower()
    variant = str(variant or '').strip().lower()
    return '%s+%s' % (layout, variant) if variant else layout


def gsettings_sources(layout: str, variant: str) -> str:
    """gsettings value for sources/mru-sources: "[('xkb', 'us+colemak')]"."""
    return "[('xkb', '%s')]" % format_layout_key(layout, variant)


def validate_custom(layout: str, variant: str) -> str | None:
    """Error string for a custom layout+variant, None when acceptable."""
    if not _LAYOUT_RE.match(str(layout or '').strip().lower()):
        return 'Layout must be 2–24 lowercase letters, digits or dashes.'
    if variant and not _VARIANT_RE.match(str(variant).strip().lower()):
        return 'Variant must be lowercase letters, digits, _ or -.'
    return None


def apply_keyboard_layout(layout: str, variant: str) -> None:
    """Live-apply via gsettings (fire-and-forget, never waited on)."""
    value = gsettings_sources(layout, variant)
    for key in ('sources', 'mru-sources'):
        try:
            subprocess.Popen(
                ['gsettings', 'set',
                 'org.gnome.desktop.input-sources', key, value],
                close_fds=True,
            )
        except (OSError, ValueError):
            pass


def current_label(key: str) -> str:
    """Preset label for a config key, or 'Custom (…)'."""
    for label, layout, variant in KEYBOARD_PRESETS:
        if format_layout_key(layout, variant) == key:
            return label
    return 'Custom (%s)' % key


class KeyboardSection(settings_page._Section):
    """Keyboard layout picker: presets plus a custom layout+variant."""

    key = 'keyboard'
    title = 'Keyboard'

    def __init__(self) -> None:
        self._row = None
        self._status = None

    def build(self, win) -> list:
        self._row = ui.nav_row('Keyboard layout', '',
                               lambda r: self._show_presets(win, r))
        built = ui.group(self.title, [self._row], note='')
        self._status = built[2]
        self._render(win)
        return built

    def refresh(self, win) -> None:
        self._render(win)

    def _key(self, win) -> str:
        try:
            return str(win.config.data.get('keyboard_layout', DEFAULT_KEY))
        except Exception:
            return DEFAULT_KEY

    def _render(self, win) -> None:
        ui.set_subtitle(self._row, current_label(self._key(win)))

    def _show_presets(self, win, anchor) -> None:
        actions = [(label, lambda layout=layout, variant=variant:
                    self._pick(win, layout, variant))
                   for label, layout, variant in KEYBOARD_PRESETS]
        actions.append(('Custom…', lambda: self._open_custom(win, anchor)))
        win.item_actions._show_action_menu(anchor, 'Keyboard layout',
                                           actions)

    def _open_custom(self, win, anchor):
        layout, variant = parse_layout_key(self._key(win))
        return ui.form_sheet(
            win, anchor, 'Custom layout',
            [('layout', 'Layout (e.g. fr)', layout, 'text'),
             ('variant', 'Variant (blank for none)', variant, 'text')],
            'Apply', lambda values: self._pick_custom(win, values))

    def _pick(self, win, layout: str, variant: str) -> None:
        try:
            win.config.set('keyboard_layout',
                           format_layout_key(layout, variant))
        except Exception:
            pass
        apply_keyboard_layout(layout, variant)
        self._say('')
        self._render(win)

    def _pick_custom(self, win, values: dict) -> str | None:
        layout = str(values.get('layout', '')).strip().lower()
        variant = str(values.get('variant', '')).strip().lower()
        error = validate_custom(layout, variant)
        if error is not None:
            return error
        self._pick(win, layout, variant)
        return None

    def _say(self, text: str) -> None:
        ui.say(self._status, text)
