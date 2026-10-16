"""Charge-limit settings section: cap battery charging below 100%.

The privileged write is the root helper's SetChargeLimit (helper_client,
async, clamped server-side to 50..100); the shell never touches sysfs as
root. Support is probed read-only: the helper capability first, a local
/sys scan second (covers a helper that is not installed yet). Without a
supported knob the section says so and offers no control. Current charge
state comes from UPower through system_settings.battery_status.
"""
from __future__ import annotations

import settings_page

import os

CHARGE_MIN = 50
CHARGE_MAX = 100

CHARGE_PRESETS: tuple = (('Off', None), ('80%', 80), ('85%', 85),
                         ('90%', 90))

CUSTOM_PLACEHOLDER = 'Custom (50-100)'

NO_KNOB_TEXT = ("This phone's battery driver "
                "doesn't support a charge limit.")

# Sysfs knobs that cap the charge level, read-only probed: the standard
# power_supply threshold plus the vendor names seen in the wild.
CHARGE_KNOBS = ('charge_control_end_threshold', 'charge_stop_threshold',
                'charge_control_limit')

_SYSFS_POWER = os.path.join('/sys', 'class', 'power_supply')


def clamp_charge_limit(percent: int) -> int:
    """Clamp into the safe 50..100 window, mirroring the helper's
    clamp_charge_limit (a test pins the two together)."""
    try:
        value = int(percent)
    except (TypeError, ValueError):
        raise ValueError('charge limit is not a number')
    return max(CHARGE_MIN, min(CHARGE_MAX, value))


def _is_battery_supply(base: str, name: str) -> bool:
    """True only for `type == Battery` supplies: an AC/Mains/USB supply
    with a similarly named knob must never count as charge-limit support."""
    try:
        with open(os.path.join(base, name, 'type'),
                  encoding='utf-8') as handle:
            return handle.read().strip() == 'Battery'
    except OSError:
        return False


def find_charge_knobs(sysfs: str = '/sys') -> list[str]:
    """Battery dirs exposing a known charge-cap knob (read-only)."""
    base = os.path.join(sysfs, 'class', 'power_supply')
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    found = []
    for name in names:
        if not _is_battery_supply(base, name):
            continue
        for knob in CHARGE_KNOBS:
            if os.path.isfile(os.path.join(base, name, knob)):
                found.append(os.path.join(base, name))
                break
    return found


def knob_supported(sysfs: str = '/sys') -> bool:
    return bool(find_charge_knobs(sysfs))


def parse_custom_limit(text: str) -> int | None:
    """Digits from a custom entry, clamped; None when there is no number."""
    digits = ''.join(ch for ch in str(text or '') if ch.isdigit())
    if not digits:
        return None
    return clamp_charge_limit(int(digits))


def limit_label(limit: int | None) -> str:
    if limit is None:
        return 'Charge limit: Off'
    return f'Charge limit: {int(limit)}%'


class ChargingSection(settings_page._Section):
    """Settings section: charge-limit control gated on sysfs support."""

    key = 'charging'
    title = 'Charging'

    def __init__(self) -> None:
        self._status = None
        self._note = None
        self._options_box = None
        self._custom_entry = None
        self._apply_btn = None
        self._current: int | None = None
        self._supported: bool | None = None

    def build(self, win) -> list:
        from settings_page import (_section_card, _section_title,
                                   _shrink_settings_entry)
        from gi.repository import Gtk, Pango

        self._current = getattr(win.config, 'charge_limit', None)
        card = _section_card(spacing=8)
        self._status = Gtk.Label(label=limit_label(self._current), xalign=0)
        self._status.add_css_class('dim-label')
        self._status.set_wrap(True)
        self._status.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._status.set_max_width_chars(26)
        card.append(self._status)

        self._options_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.append(self._options_box)

        self._custom_entry = Gtk.Entry()
        self._custom_entry.set_placeholder_text(CUSTOM_PLACEHOLDER)
        try:
            self._custom_entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
        except Exception:
            pass
        _shrink_settings_entry(self._custom_entry)
        getattr(win, '_attach_osk', lambda _w: None)(self._custom_entry)
        apply_btn = Gtk.Button(label='Set custom limit')
        apply_btn.add_css_class('flat')
        apply_btn.add_css_class('action-link')
        apply_btn.connect(
            'clicked', lambda _b: self._on_custom(win))
        self._apply_btn = apply_btn
        card.append(self._custom_entry)
        card.append(apply_btn)

        self._note = Gtk.Label(label='', xalign=0)
        self._note.add_css_class('dim-label')
        self._note.set_wrap(True)
        self._note.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._note.set_max_width_chars(26)
        card.append(self._note)
        self._render_options(win)
        return [_section_title(self.title), card]

    def refresh(self, win) -> None:
        from settings_page import _run_bg
        self._current = getattr(win.config, 'charge_limit', None)
        state: dict = {}

        def _maybe_render() -> None:
            if 'local' not in state or 'caps' not in state:
                return
            local_supported, battery = state['local']
            ok, caps = state['caps']
            if ok:
                supported = bool(isinstance(caps, dict)
                                 and caps.get('charge_limit_supported'))
            else:
                supported = bool(local_supported)
            self._on_state(win, (supported, battery))

        try:
            import helper_client
            helper_client.get_capabilities(
                lambda ok, caps: (state.update(caps=(ok, caps)),
                                  _maybe_render()))
        except Exception:
            state['caps'] = (False, None)
        _run_bg(self._probe_local, lambda res: (state.update(local=res),
                                                _maybe_render()))

    # ------------------------------------------------------------ behavior

    def _probe_local(self):
        """Worker thread: (sysfs knob present, battery text). Never raises."""
        try:
            supported = knob_supported()
        except Exception:
            supported = False
        try:
            import system_settings
            battery = system_settings.battery_status()
        except Exception:
            battery = None
        return (supported, battery)

    def _on_state(self, win, res) -> None:
        try:
            supported, battery = res
        except (TypeError, ValueError):
            return
        self._supported = bool(supported)
        current = limit_label(getattr(win.config, 'charge_limit', None))
        if battery:
            current += f' — {battery}'
        if self._status is not None:
            self._status.set_text(current)
        self._render_options(win)

    def _render_options(self, win) -> None:
        from settings_page import _replace_box_children, _section_placeholder
        if self._options_box is None:
            return
        if self._supported is not True:
            _replace_box_children(
                self._options_box, [_section_placeholder(NO_KNOB_TEXT)])
            self._set_custom_visible(False)
            return
        from gi.repository import Gtk
        rows = []
        for label, value in CHARGE_PRESETS:
            marked = f'● {label}' if self._current == value else label
            btn = Gtk.Button(label=marked)
            btn.add_css_class('flat')
            btn.add_css_class('action-link')
            btn.connect(
                'clicked', lambda _b, v=value: self._on_pick(win, v))
            rows.append(btn)
        _replace_box_children(self._options_box, rows)
        self._set_custom_visible(True)

    def _set_custom_visible(self, visible: bool) -> None:
        for widget in (self._custom_entry, self._apply_btn):
            if widget is None:
                continue
            try:
                widget.set_visible(visible)
            except Exception:
                pass

    def _on_pick(self, win, value: int | None) -> None:
        self._apply(win, value)

    def _on_custom(self, win) -> None:
        try:
            text = self._custom_entry.get_text()
        except Exception:
            text = ''
        picked = parse_custom_limit(text)
        if picked is None:
            self._say('Enter a number from 50 to 100.')
            return
        self._apply(win, picked)

    def _apply(self, win, value: int | None) -> None:
        percent = 100 if value is None else clamp_charge_limit(value)
        try:
            import helper_client
        except ImportError as err:
            self._say(f'Could not set limit: {err}')
            return

        def _done(ok: bool, detail) -> None:
            if not ok:
                self._say(f'Could not set limit: {detail}')
                return
            try:
                win.config.set_charge_limit(value)
            except Exception:
                pass
            self._current = getattr(win.config, 'charge_limit', None)
            if self._status is not None:
                self._status.set_text(limit_label(self._current))
            self._render_options(win)
            self._say('')

        try:
            helper_client.set_charge_limit(percent, _done)
        except Exception as err:
            self._say(f'Could not set limit: {err}')

    def _say(self, text: str) -> None:
        try:
            if self._note is not None:
                self._note.set_text(text)
        except Exception:
            pass
