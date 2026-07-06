from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple, Callable

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gio, Gtk
from als_brightness import ALSBrightness


_QA_CSS = b"""
.qa-panel {
    background: transparent;
}
.qa-tile {
    font-family: 'Space Mono', monospace;
    min-width: 80px;
    min-height: 72px;
    border-radius: 16px;
    background: #1a1a1a;
    color: #9a9a9a;
    border: none;
    padding: 0;
}
.qa-tile.active {
    background: #f4f4f4;
    color: #000000;
}
.qa-tile:hover {
    background: #242424;
}
.qa-tile.active:hover {
    background: #e0e0e0;
}
.tile-label {
    font-size: 10pt;
    font-weight: 600;
    letter-spacing: 0.05em;
}
.tile-state {
    font-size: 8pt;
    margin-top: 2px;
    opacity: 0.7;
}
.qa-slider-label {
    font-size: 10pt;
    font-family: 'Space Mono', monospace;
    color: #9a9a9a;
    min-width: 60px;
}
"""


# --- DBus helpers (best-effort, all failures are silent) ---

def _dbus_system() -> Gio.DBusConnection | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error:
        return None


def _nm_get(prop: str) -> bool | None:
    bus = _dbus_system()
    if not bus:
        return None
    try:
        result = bus.call_sync(
            'org.freedesktop.NetworkManager',
            '/org/freedesktop/NetworkManager',
            'org.freedesktop.DBus.Properties', 'Get',
            GLib.Variant('(ss)', ('org.freedesktop.NetworkManager', prop)),
            GLib.VariantType('(v)'),
            Gio.DBusCallFlags.NONE, 800, None,
        )
        return bool(result.unpack()[0])
    except GLib.Error:
        return None


def _nm_set(prop: str, value: bool) -> None:
    bus = _dbus_system()
    if not bus:
        return
    try:
        bus.call_sync(
            'org.freedesktop.NetworkManager',
            '/org/freedesktop/NetworkManager',
            'org.freedesktop.DBus.Properties', 'Set',
            GLib.Variant('(ssv)', ('org.freedesktop.NetworkManager', prop, GLib.Variant('b', value))),
            None, Gio.DBusCallFlags.NONE, 800, None,
        )
    except GLib.Error:
        pass


def _bluez_get() -> bool | None:
    bus = _dbus_system()
    if not bus:
        return None
    try:
        result = bus.call_sync(
            'org.bluez', '/org/bluez/hci0',
            'org.freedesktop.DBus.Properties', 'Get',
            GLib.Variant('(ss)', ('org.bluez.Adapter1', 'Powered')),
            GLib.VariantType('(v)'),
            Gio.DBusCallFlags.NONE, 800, None,
        )
        return bool(result.unpack()[0])
    except GLib.Error:
        return None


def _bluez_set(value: bool) -> None:
    bus = _dbus_system()
    if not bus:
        return
    try:
        bus.call_sync(
            'org.bluez', '/org/bluez/hci0',
            'org.freedesktop.DBus.Properties', 'Set',
            GLib.Variant('(ssv)', ('org.bluez.Adapter1', 'Powered', GLib.Variant('b', value))),
            None, Gio.DBusCallFlags.NONE, 800, None,
        )
    except GLib.Error:
        pass


def _toggle_airplane(enabled: bool) -> None:
    _nm_set('WirelessEnabled', not enabled)
    _nm_set('WwanEnabled', not enabled)
    _bluez_set(not enabled)


def _toggle_torch(enabled: bool) -> None:
    for led in Path('/sys/class/leds').glob('*torch*'):
        bright = led / 'brightness'
        max_p = led / 'max_brightness'
        try:
            max_val = int(max_p.read_text()) if max_p.exists() else 1
            bright.write_text(str(max_val if enabled else 0))
        except OSError:
            pass


def _get_brightness_pct() -> int:
    try:
        out = subprocess.check_output(['brightnessctl', 'get'], text=True, timeout=1).strip()
        max_out = subprocess.check_output(['brightnessctl', 'max'], text=True, timeout=1).strip()
        current, max_val = int(out), int(max_out)
        return int(current * 100 / max_val) if max_val else 50
    except Exception:
        pass
    for path in Path('/sys/class/backlight').glob('*/brightness'):
        try:
            current = int(path.read_text())
            max_val = int((path.parent / 'max_brightness').read_text())
            return int(current * 100 / max_val) if max_val else 50
        except (OSError, ValueError):
            pass
    return 50


def _set_brightness_pct(pct: int) -> None:
    try:
        subprocess.Popen(['brightnessctl', 'set', f'{max(1, pct)}%'], close_fds=True)
        return
    except FileNotFoundError:
        pass
    for path in Path('/sys/class/backlight').glob('*/brightness'):
        try:
            max_val = int((path.parent / 'max_brightness').read_text())
            path.write_text(str(max(0, min(max_val, int(pct * max_val / 100)))))
        except (OSError, ValueError):
            pass


def _get_volume_pct() -> int:
    try:
        out = subprocess.check_output(
            ['pactl', 'get-sink-volume', '@DEFAULT_SINK@'],
            text=True, timeout=1,
        )
        for token in out.split():
            if token.endswith('%'):
                return int(token.rstrip('%'))
    except Exception:
        pass
    return 50


def _set_volume_pct(pct: int) -> None:
    try:
        subprocess.Popen(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{pct}%'], close_fds=True)
    except FileNotFoundError:
        pass


# --- Tile definitions ---

class _TileDef(NamedTuple):
    key: str
    label: str
    get_state: Callable[[], bool | None]
    set_state: Callable[[bool], None]
    tier: int  # 1 = always shown, 2 = expanded only


_TILES: list[_TileDef] = [
    _TileDef('wifi',     'WiFi',     lambda: _nm_get('WirelessEnabled'), lambda v: _nm_set('WirelessEnabled', v), 1),
    _TileDef('bt',       'BT',       _bluez_get,                          _bluez_set,                              1),
    _TileDef('data',     'Data',     lambda: _nm_get('WwanEnabled'),      lambda v: _nm_set('WwanEnabled', v),     1),
    _TileDef('airplane', 'Airplane', lambda: None,                        _toggle_airplane,                        1),
    _TileDef('torch',    'Torch',    lambda: None,                        _toggle_torch,                           2),
    _TileDef('dnd',      'DnD',      lambda: None,                        lambda _v: None,                         2),
    _TileDef('auto_br',  'Auto',     lambda: None,                        lambda _v: None,                         2),
    _TileDef('location', 'Location', lambda: None,                        lambda _v: None,                         2),
    _TileDef('hotspot',  'Hotspot',  lambda: None,                        lambda _v: None,                         2),
]


class QuickActionsPanel(Gtk.Box):
    """
    Quick-actions tile grid + brightness/volume sliders.
    Embed in NotificationShade. Call expand(True/False) to show tier-2 + sliders.
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class('qa-panel')

        self._tile_buttons: dict[str, Gtk.ToggleButton] = {}
        self._tile_state: dict[str, bool] = {}
        self._state_labels: dict[str, Gtk.Label] = {}
        self._updating = False
        self._als = ALSBrightness()

        provider = Gtk.CssProvider()
        provider.load_from_data(_QA_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 3,
        )

        self._build()
        GLib.idle_add(self._refresh_all_states)

    def _build(self) -> None:
        tier1 = [t for t in _TILES if t.tier == 1]
        tier2 = [t for t in _TILES if t.tier == 2]

        self.tier1_grid = Gtk.Grid(row_spacing=8, column_spacing=8)
        for col, tile in enumerate(tier1):
            self.tier1_grid.attach(self._make_tile(tile), col, 0, 1, 1)

        self.tier2_grid = Gtk.Grid(row_spacing=8, column_spacing=8)
        for idx, tile in enumerate(tier2):
            self.tier2_grid.attach(self._make_tile(tile), idx % 4, idx // 4, 1, 1)

        self.sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)

        self.sliders_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        self._build_sliders()

        self.append(self.tier1_grid)
        self.append(self.tier2_grid)
        self.append(self.sep)
        self.append(self.sliders_box)

        self.tier2_grid.set_visible(False)
        self.sep.set_visible(False)
        self.sliders_box.set_visible(False)

    def _make_tile(self, tile: _TileDef) -> Gtk.ToggleButton:
        label_w = Gtk.Label(label=tile.label)
        label_w.add_css_class('tile-label')

        state_w = Gtk.Label(label='—')
        state_w.add_css_class('tile-state')
        self._state_labels[tile.key] = state_w

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)
        inner.append(label_w)
        inner.append(state_w)

        btn = Gtk.ToggleButton()
        btn.add_css_class('qa-tile')
        btn.set_child(inner)
        btn.connect('toggled', self._on_tile_toggled, tile)
        self._tile_buttons[tile.key] = btn
        return btn

    def _build_sliders(self) -> None:
        for label_text, getter, setter in [
            ('Bright', _get_brightness_pct, _set_brightness_pct),
            ('Volume', _get_volume_pct,     _set_volume_pct),
        ]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            lbl = Gtk.Label(label=label_text, xalign=0)
            lbl.add_css_class('qa-slider-label')
            slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            slider.set_hexpand(True)
            slider.set_draw_value(False)
            slider.set_value(getter())
            slider.connect('value-changed', lambda s, fn=setter: fn(int(s.get_value())))
            row.append(lbl)
            row.append(slider)
            self.sliders_box.append(row)

            if label_text == 'Bright':
                self._bright_slider = slider
            else:
                self._vol_slider = slider

    def _on_tile_toggled(self, btn: Gtk.ToggleButton, tile: _TileDef) -> None:
        if self._updating:
            return
        new_state = btn.get_active()
        if tile.key == 'auto_br':
            self._toggle_auto_brightness(new_state)
        else:
            try:
                tile.set_state(new_state)
            except Exception:
                pass
        self._apply_tile_ui(tile.key, new_state)

    def _toggle_auto_brightness(self, enabled: bool) -> None:
        if enabled:
            if not self._als.available():
                # No ALS found — show tile as inactive
                GLib.idle_add(self._apply_tile_state, 'auto_br', False)
                return
            self._als.start()
            # Disable manual brightness slider while auto is active
            if hasattr(self, '_bright_slider'):
                self._bright_slider.set_sensitive(False)
        else:
            self._als.stop()
            if hasattr(self, '_bright_slider'):
                self._bright_slider.set_sensitive(True)

    def _refresh_all_states(self) -> bool:
        for tile in _TILES:
            try:
                state = tile.get_state()
            except Exception:
                state = None
            if state is not None:
                self._apply_tile_state(tile.key, state)
        return False

    def _apply_tile_state(self, key: str, state: bool) -> None:
        self._tile_state[key] = state
        btn = self._tile_buttons.get(key)
        if btn:
            self._updating = True
            btn.set_active(state)
            self._updating = False
        self._apply_tile_ui(key, state)

    def _apply_tile_ui(self, key: str, state: bool) -> None:
        btn = self._tile_buttons.get(key)
        if btn:
            if state:
                btn.add_css_class('active')
            else:
                btn.remove_css_class('active')
        lbl = self._state_labels.get(key)
        if lbl:
            lbl.set_text('on' if state else 'off')

    def expand(self, expanded: bool) -> None:
        self.tier2_grid.set_visible(expanded)
        self.sep.set_visible(expanded)
        self.sliders_box.set_visible(expanded)
        if expanded:
            self._bright_slider.set_value(_get_brightness_pct())
            self._vol_slider.set_value(_get_volume_pct())

    def refresh_states(self) -> None:
        GLib.idle_add(self._refresh_all_states)
