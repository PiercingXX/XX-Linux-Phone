from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple, Callable

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gio, Gtk
from als_brightness import ALSBrightness
from config import ShellConfig, ThemePreset


def theme_css(preset: ThemePreset) -> str:
    return f"""
.qa-panel {{
    background: transparent;
    color: {preset.foreground};
}}
.qa-tile {{
    min-width: 80px;
    min-height: 72px;
    border-radius: 16px;
    background: {preset.surface};
    color: {preset.muted};
    border: none;
    padding: 0;
}}
.qa-tile.active {{
    background: {preset.accent};
    color: {preset.background};
}}
.qa-tile:hover {{
    background: {preset.surface_alt};
}}
.qa-tile.active:hover {{
    background: mix({preset.accent}, {preset.background}, 0.85);
}}
.tile-label {{
    font-size: 10pt;
    font-weight: 600;
    letter-spacing: 0.05em;
}}
.tile-state {{
    font-size: 8pt;
    margin-top: 2px;
    opacity: 0.7;
}}
.qa-slider-label {{
    font-size: 10pt;
    color: {preset.muted};
    min-width: 60px;
}}
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


_NM_DEV_WIFI = 2  # NetworkManager DeviceType


def _nm_has_wifi_device() -> bool:
    """False also covers NM being down, so the Hotspot tile hides entirely."""
    bus = _dbus_system()
    if not bus:
        return False
    try:
        devices = bus.call_sync(
            'org.freedesktop.NetworkManager',
            '/org/freedesktop/NetworkManager',
            'org.freedesktop.DBus.Properties', 'Get',
            GLib.Variant('(ss)', ('org.freedesktop.NetworkManager', 'Devices')),
            GLib.VariantType('(v)'),
            Gio.DBusCallFlags.NONE, 800, None,
        ).unpack()[0]
        for dev_path in devices:
            dtype = bus.call_sync(
                'org.freedesktop.NetworkManager', dev_path,
                'org.freedesktop.DBus.Properties', 'Get',
                GLib.Variant('(ss)',
                             ('org.freedesktop.NetworkManager.Device',
                              'DeviceType')),
                GLib.VariantType('(v)'),
                Gio.DBusCallFlags.NONE, 800, None,
            ).unpack()[0]
            if int(dtype) == _NM_DEV_WIFI:
                return True
    except GLib.Error:
        return False
    return False


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


_NM_ACTIVE_TYPE_AP = 'ap'  # ActiveConnection.Type while a hotspot is up


def _active_hotspot_path(bus: Gio.DBusConnection) -> str | None:
    """Resolve the active hotspot by NM's Type='ap', never by profile name:
    the profile behind it may be renamed or auto-created. Raises GLib.Error
    when NM is unreachable."""
    conns = bus.call_sync(
        'org.freedesktop.NetworkManager',
        '/org/freedesktop/NetworkManager',
        'org.freedesktop.DBus.Properties', 'Get',
        GLib.Variant('(ss)', ('org.freedesktop.NetworkManager',
                              'ActiveConnections')),
        GLib.VariantType('(v)'),
        Gio.DBusCallFlags.NONE, 800, None,
    ).unpack()[0]
    for path in conns:
        actype = bus.call_sync(
            'org.freedesktop.NetworkManager', path,
            'org.freedesktop.DBus.Properties', 'Get',
            GLib.Variant('(ss)',
                         ('org.freedesktop.NetworkManager.Connection.Active',
                          'Type')),
            GLib.VariantType('(v)'),
            Gio.DBusCallFlags.NONE, 800, None,
        ).unpack()[0]
        if actype == _NM_ACTIVE_TYPE_AP:
            return path
    return None


def _toggle_hotspot(enabled: bool) -> None:
    if enabled:
        try:
            subprocess.Popen(['nmcli', 'device', 'wifi', 'hotspot'],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
        except OSError:
            pass
        return
    bus = _dbus_system()
    if bus is None:
        return
    try:
        active = _active_hotspot_path(bus)
    except GLib.Error:
        return
    if active is None:
        return
    try:
        bus.call_sync(
            'org.freedesktop.NetworkManager',
            '/org/freedesktop/NetworkManager',
            'org.freedesktop.NetworkManager', 'DeactivateConnection',
            GLib.Variant('(o)', (active,)), None,
            Gio.DBusCallFlags.NONE, 800, None,
        )
    except GLib.Error:
        pass


def _get_hotspot_state() -> bool | None:
    bus = _dbus_system()
    if bus is None:
        return None
    try:
        return _active_hotspot_path(bus) is not None
    except GLib.Error:
        return None


# --- Location (GeoClue2 master switch) ---

# GeoClue2 has no enable/disable control on its Manager; clients negotiate
# with a registered authorization agent (the same lever GNOME Shell and phosh
# pull). This tile is a global master switch, not per-app policy: while ON
# every client is granted up to EXACT accuracy, while OFF MaxAccuracyLevel=0
# denies all clients (and revokes live ones).
_GEOCLUE_NAME = 'org.freedesktop.GeoClue2'
_GEOCLUE_MGR_PATH = '/org/freedesktop/GeoClue2/Manager'
_AGENT_IFACE = 'org.freedesktop.GeoClue2.Agent'
_AGENT_PATH = '/org/xxwm/GeoClueAgent'
_AGENT_XML = (
    '<node>'
    f'<interface name="{_AGENT_IFACE}">'
    '<method name="AuthorizeApp">'
    '<arg type="s" direction="in" name="app_id"/>'
    '<arg type="u" direction="in" name="req_accuracy"/>'
    '<arg type="u" direction="out" name="allowed_accuracy"/>'
    '</method>'
    '<property name="MaxAccuracyLevel" type="u" access="read"/>'
    '</interface>'
    '</node>'
)

# GClueAccuracyLevel: NONE=0 … EXACT=8
_ACC_EXACT = 8


class LocationState:
    """GeoClue2 toggle without root.

    Geoclue serves ONE agent system-wide, so registration is lazy (first
    enable) and the slot is released on disable; if another agent owns the
    slot (e.g. phosh's prompting agent) the tile hides. The daemon's name
    owner is watched so state stays honest across geoclue restarts.
    """

    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self.enabled = False
        self.on_change = on_change
        self._obj_id: int | None = None
        self._owner: str | None = None
        self._rejected = False
        try:
            Gio.bus_watch_name(
                Gio.BusType.SYSTEM, _GEOCLUE_NAME,
                Gio.BusNameWatcherFlags.NONE,
                self._on_owner_appeared, self._on_owner_vanished)
        except Exception:
            pass

    def available(self) -> bool:
        """Read-only probe: daemon reachable and no live rejection. Never
        registers — claiming the system's single agent slot must wait for an
        explicit enable."""
        if self._rejected:
            return False
        bus = _dbus_system()
        if bus is None:
            return False
        try:
            bus.call_sync(
                _GEOCLUE_NAME, _GEOCLUE_MGR_PATH,
                'org.freedesktop.DBus.Properties', 'Get',
                GLib.Variant('(ss)', (_GEOCLUE_NAME + '.Manager', 'InUse')),
                GLib.VariantType('(v)'),
                Gio.DBusCallFlags.NONE, 800, None,
            )
        except GLib.Error:
            return False
        return True

    def is_active(self) -> bool:
        # Honest ON requires a live registration with the current owner.
        return self.enabled and self._obj_id is not None

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        bus = _dbus_system()
        if bus is None:
            return
        if self.enabled:
            self._activate(bus)
        else:
            self._deactivate(bus)

    def _activate(self, bus: Gio.DBusConnection) -> None:
        if not self._register(bus):
            self.enabled = False
            self._rejected = True
        else:
            self._rejected = False
            self._notify_max_accuracy(bus)
        self._fire_change()

    def _deactivate(self, bus: Gio.DBusConnection) -> None:
        # Revoke before releasing the slot so the daemon sees MaxAccuracyLevel=0.
        self._notify_max_accuracy(bus)
        self._unregister(bus)
        self._fire_change()

    def _fire_change(self) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change()
        except Exception:
            pass

    def _register(self, bus: Gio.DBusConnection) -> bool:
        if self._obj_id is not None:
            return True
        obj_id = None
        try:
            info = Gio.DBusNodeInfo.new_for_xml(_AGENT_XML)
            obj_id = bus.register_object(
                _AGENT_PATH, info.interfaces[0],
                self._on_method_call, self._on_get_property, None)
            bus.call_sync(
                _GEOCLUE_NAME, _GEOCLUE_MGR_PATH,
                _GEOCLUE_NAME + '.Manager', 'RegisterAgent',
                GLib.Variant('(o)', (_AGENT_PATH,)), None,
                Gio.DBusCallFlags.NONE, 2000, None,
            )
        except Exception:
            if obj_id is not None:
                try:
                    bus.unregister_object(obj_id)
                except Exception:
                    pass
            return False
        self._obj_id = obj_id
        return True

    def _unregister(self, bus: Gio.DBusConnection) -> None:
        if self._obj_id is None:
            return
        try:
            bus.call_sync(
                _GEOCLUE_NAME, _GEOCLUE_MGR_PATH,
                _GEOCLUE_NAME + '.Manager', 'UnregisterAgent',
                GLib.Variant('(o)', (_AGENT_PATH,)), None,
                Gio.DBusCallFlags.NONE, 2000, None,
            )
        except Exception:
            pass
        self._drop_export(bus)

    def _drop_export(self, bus: Gio.DBusConnection | None) -> None:
        if self._obj_id is None:
            return
        if bus is not None:
            try:
                bus.unregister_object(self._obj_id)
            except Exception:
                pass
        self._obj_id = None

    def _on_owner_appeared(self, _conn, _name, owner: str) -> None:
        if owner == self._owner:
            return
        # A new owner means the old daemon (and our registration with it)
        # died, even when no vanish was observed first.
        self._drop_export(_dbus_system())
        self._owner = owner
        self._rejected = False
        if self.enabled:
            bus = _dbus_system()
            if bus is not None:
                self._activate(bus)
                return
        self._fire_change()

    def _on_owner_vanished(self, _conn, _name) -> None:
        if self._owner is None:
            return
        self._owner = None
        was_registered = self._obj_id is not None
        self._drop_export(_dbus_system())
        if was_registered:
            self._fire_change()

    def _max_accuracy(self) -> int:
        return _ACC_EXACT if self.enabled else 0

    def _allow(self, req_accuracy: int) -> int:
        return min(int(req_accuracy), _ACC_EXACT) if self.enabled else 0

    def _notify_max_accuracy(self, bus: Gio.DBusConnection) -> None:
        if self._obj_id is None:
            return
        try:
            bus.emit_signal(
                None, _AGENT_PATH, 'org.freedesktop.DBus.Properties',
                'PropertiesChanged',
                GLib.Variant('(sa{sv}as)', (
                    _AGENT_IFACE,
                    {'MaxAccuracyLevel': GLib.Variant('u',
                                                      self._max_accuracy())},
                    [],
                )),
            )
        except Exception:
            pass

    def _on_method_call(self, _conn, _sender, _path, _iface, method,
                        params, invocation) -> None:
        if method == 'AuthorizeApp':
            req = params.unpack()[1]
            invocation.return_value(GLib.Variant('(u)', (self._allow(req),)))
            return
        invocation.return_error_by_name(
            'org.freedesktop.DBus.Error.UnknownMethod')

    def _on_get_property(self, _conn, _sender, _path, _iface, prop):
        if prop == 'MaxAccuracyLevel':
            return GLib.Variant('u', self._max_accuracy())
        return None


_SLIDER_DEFAULT_PCT = 50
_SLIDER_APPLY_DEBOUNCE_MS = 200


def _get_brightness_pct() -> int:
    try:
        out = subprocess.check_output(['brightnessctl', 'get'], text=True, timeout=1).strip()
        max_out = subprocess.check_output(['brightnessctl', 'max'], text=True, timeout=1).strip()
        current, max_val = int(out), int(max_out)
        return int(current * 100 / max_val) if max_val else _SLIDER_DEFAULT_PCT
    except Exception:
        pass
    for path in Path('/sys/class/backlight').glob('*/brightness'):
        try:
            current = int(path.read_text())
            max_val = int((path.parent / 'max_brightness').read_text())
            return int(current * 100 / max_val) if max_val else _SLIDER_DEFAULT_PCT
        except (OSError, ValueError):
            pass
    return _SLIDER_DEFAULT_PCT


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
    return _SLIDER_DEFAULT_PCT


def _set_volume_pct(pct: int) -> None:
    try:
        subprocess.Popen(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{pct}%'], close_fds=True)
    except FileNotFoundError:
        pass


def _read_slider_values() -> tuple[int, int]:
    """One getter batch per expand: (brightness_pct, volume_pct)."""
    return _get_brightness_pct(), _get_volume_pct()


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
    _TileDef('focus',    'Focus',    lambda: None,                        lambda _v: None,                         2),
    _TileDef('auto_br',  'Auto',     lambda: None,                        lambda _v: None,                         2),
    _TileDef('location', 'Location', lambda: None,                        lambda _v: None,                         2),
    _TileDef('hotspot',  'Hotspot',  _get_hotspot_state,                  _toggle_hotspot,                         2),
]


class QuickActionsPanel(Gtk.Box):
    """
    Quick-actions tile grid + brightness/volume sliders.
    Embed in NotificationShade. Call expand(True/False) to show tier-2 tiles.
    Brightness and volume sliders stay visible (not gated on expand).
    """

    def __init__(self, dnd_state: object | None = None,
                 focus_state: object | None = None,
                 hud: object | None = None,
                 config: ShellConfig | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class('qa-panel')

        self._dnd = dnd_state
        self._focus = focus_state
        # Volume/brightness HUD overlay (plan T3/T4). Optional: when absent the
        # sliders still adjust brightness/volume, just without an on-screen
        # indicator (silent absence — see _show_brightness_hud).
        self._hud = hud
        # The shade threads its LIVE config down so theme == 'custom' renders
        # the derived palette; the fallback keeps direct no-arg construction
        # working (pre-existing snapshot behavior).
        self._config = config if config is not None else ShellConfig()
        self._tile_buttons: dict[str, Gtk.ToggleButton] = {}
        self._tile_state: dict[str, bool] = {}
        self._state_labels: dict[str, Gtk.Label] = {}
        self._updating = False
        self._als = ALSBrightness()
        self._location = LocationState(on_change=self._on_location_changed)
        self._slider_pending: dict[str, int] = {}
        self._slider_timers: dict[str, int] = {}
        self._syncing_sliders = False
        self._slider_appliers = {
            'bright': self._apply_brightness,
            'vol': _set_volume_pct,
        }

        self._theme_provider = Gtk.CssProvider()
        self._theme_provider.load_from_data(
            theme_css(self._display_preset()).encode('utf-8'))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 3,
        )

        self._build()
        GLib.idle_add(self._refresh_all_states)

    def _display_preset(self) -> ThemePreset:
        """Preset this panel renders with: window.resolve_theme over the
        injected config, so theme == 'custom' derives its palette instead of
        falling back to the default preset (W1-B). Lazy import: window.py
        sits above this module in the shell stack, and a module-level import
        would drag its GTK requirements into headless contexts that import
        quick_actions for its tile/slider logic.
        """
        from window import resolve_theme
        return resolve_theme(self._config)

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        data = theme_css(preset if preset is not None else self._display_preset())
        self._theme_provider.load_from_data(data.encode('utf-8'))

    def _tiles(self) -> list[_TileDef]:
        tiles = []
        for tile in _TILES:
            # Hardware/service-gated tiles stay hidden without their backend
            if tile.key == 'auto_br' and not self._als.available():
                continue
            if tile.key == 'torch' and not any(Path('/sys/class/leds').glob('*torch*')):
                continue
            if tile.key == 'hotspot' and not _nm_has_wifi_device():
                continue
            if tile.key == 'location':
                if not self._location.available():
                    continue
                tile = tile._replace(
                    get_state=lambda: bool(self._location.is_active()),
                    set_state=self._location.set_enabled,
                )
            if tile.key == 'dnd':
                if self._dnd is None:
                    continue
                tile = tile._replace(
                    get_state=lambda: bool(self._dnd.is_active()),
                    set_state=lambda v: self._dnd.set_enabled(v),
                )
            if tile.key == 'focus':
                if self._focus is None:
                    continue
                tile = tile._replace(
                    get_state=lambda: bool(self._focus.is_active()),
                    set_state=lambda v: self._focus.set_enabled(v),
                )
            tiles.append(tile)
        return tiles

    def _build(self) -> None:
        tiles = self._tiles()
        tier1 = [t for t in tiles if t.tier == 1]
        tier2 = [t for t in tiles if t.tier == 2]

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
        self.sliders_box.set_visible(True)
        self._sync_sliders_to_live_state()

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
        if tile.key == 'focus':
            # Long-press → take a break (focus suspends, auto-resumes)
            long_press = Gtk.GestureLongPress.new()
            long_press.set_touch_only(False)
            long_press.connect('pressed', self._on_focus_long_press, btn)
            btn.add_controller(long_press)
        self._tile_buttons[tile.key] = btn
        return btn

    def _on_focus_long_press(self, gesture: Gtk.GestureLongPress,
                             _x: float, _y: float, btn: Gtk.Widget) -> None:
        if self._focus is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        popover = Gtk.Popover()
        popover.add_css_class('action-menu')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for minutes in (5, 10, 15):
            action = Gtk.Button(label=f'Take a break — {minutes} min')
            action.add_css_class('flat')
            action.add_css_class('menu-action')
            action.connect(
                'clicked',
                lambda _b, m=minutes, p=popover: self._start_focus_break(m, p))
            box.append(action)
        popover.set_child(box)
        popover.set_parent(btn)
        popover.connect('closed', lambda p: p.unparent())
        popover.popup()

    def _start_focus_break(self, minutes: int, popover: Gtk.Popover) -> None:
        popover.popdown()
        self._focus.start_break(minutes)
        self.refresh_states()

    def _build_sliders(self) -> None:
        for label_text, key in [('Bright', 'bright'), ('Volume', 'vol')]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            lbl = Gtk.Label(label=label_text, xalign=0)
            lbl.add_css_class('qa-slider-label')
            slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            slider.set_hexpand(True)
            slider.set_draw_value(False)
            slider.set_value(_SLIDER_DEFAULT_PCT)
            if key == 'bright':
                # Adjust brightness AND flash the level on the HUD (T4).
                slider.connect(
                    'value-changed',
                    lambda s: self._on_brightness_changed(int(s.get_value())))
            else:
                slider.connect(
                    'value-changed',
                    lambda s: self._on_volume_changed(int(s.get_value())))
            row.append(lbl)
            row.append(slider)
            self.sliders_box.append(row)

            if key == 'bright':
                self._bright_slider = slider
            else:
                self._vol_slider = slider

    def _on_brightness_changed(self, pct: int) -> None:
        """Brightness slider moved: the trailing value is applied (with a HUD
        flash) once per drag after the debounce window."""
        self._queue_slider_apply('bright', pct)

    def _on_volume_changed(self, pct: int) -> None:
        self._queue_slider_apply('vol', pct)

    def _queue_slider_apply(self, key: str, pct: int) -> None:
        if self._syncing_sliders:
            return
        self._slider_pending[key] = pct
        timer = self._slider_timers.get(key)
        if timer is not None:
            GLib.source_remove(timer)
        self._slider_timers[key] = GLib.timeout_add(
            _SLIDER_APPLY_DEBOUNCE_MS, self._flush_slider_apply, key)

    def _flush_slider_apply(self, key: str) -> bool:
        self._slider_timers.pop(key, None)
        pct = self._slider_pending.pop(key, None)
        if pct is not None:
            self._slider_appliers[key](pct)
        return False

    def _apply_brightness(self, pct: int) -> None:
        _set_brightness_pct(pct)
        self._show_brightness_hud(pct)

    def _show_brightness_hud(self, pct: int) -> None:
        """Flash the current brightness level on the HUD overlay (if one is wired).

        Silent absence: without a HUD the brightness change already happened in
        _apply_brightness; this is a fire-and-forget no-op, never a crash.
        """
        if self._hud is not None:
            self._hud.show_brightness(pct)

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
        if tile.key == 'location':
            # Registration may have failed: show the actual state, never the
            # requested one.
            self._apply_tile_state(tile.key, self._location.is_active())
            return
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

    def _on_location_changed(self) -> None:
        btn = self._tile_buttons.get('location')
        if btn is None:
            return
        shown = self._location.available()
        btn.set_visible(shown)
        self._apply_tile_state('location',
                               shown and self._location.is_active())

    def _refresh_all_states(self) -> bool:
        for tile in self._tiles():
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
        if expanded:
            self._sync_sliders_to_live_state()

    def sync_sliders(self) -> None:
        self._sync_sliders_to_live_state()

    def _sync_sliders_to_live_state(self) -> None:
        # One getter batch per expand feeds every slider; the sync guard keeps
        # the set_value round-trip from queueing a redundant apply.
        bright, vol = _read_slider_values()
        self._syncing_sliders = True
        self._bright_slider.set_value(bright)
        self._vol_slider.set_value(vol)
        self._syncing_sliders = False

    def refresh_states(self) -> None:
        GLib.idle_add(self._refresh_all_states)
