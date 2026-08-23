"""Tests for the Hotspot + Location shade tiles (WS25.5).

Hotspot toggles NM via nmcli (`device wifi hotspot` on, `connection down
Hotspot` off) and hides unless NM reports a WiFi device (same D-Bus probe
family as the WiFi tile). Location registers an org.freedesktop.GeoClue2.Agent
and gates app access through it — GeoClue2's Manager has no enable/disable
control, the agent authorization surface is the documented lever (the one
GNOME Shell and phosh pull).

Both tiles must hide when their backing service is absent and must survive the
service disappearing mid-session without crashing.

quick_actions imports gi.repository.Gdk/GLib/Gio/Gtk at module top level, but
PyGObject may be absent here; minimal fake modules are injected before import,
exactly like test_hud_silent does. Widget construction is never run — the tile
logic is exercised through module helpers and a bare QuickActionsPanel built
with object.__new__.
"""
import subprocess
import sys
import types


class _RecordingVariant:
    """Duck-typed GLib.Variant that remembers its constructor arguments so
    tests can assert exact D-Bus payloads."""

    def __init__(self, *args):
        self.args = args

    def __eq__(self, other):
        return (isinstance(other, _RecordingVariant)
                and self.args == other.args)

    def __repr__(self):
        return f'_Variant{self.args!r}'


def _install_fake_gi() -> None:
    """Make `from gi.repository import Gdk, GLib, Gio, Gtk` resolve to stubs."""
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.idle_add = lambda *a, **k: None
    glib.Error = Exception
    glib.Variant = _RecordingVariant
    glib.VariantType = lambda *a, **k: None

    gio = types.ModuleType('gi.repository.Gio')
    gio.DBusCallFlags = types.SimpleNamespace(NONE=0)
    gio.DBusSignalFlags = types.SimpleNamespace(NONE=0)

    for name in ('Gdk', 'Gtk'):
        sys.modules[f'gi.repository.{name}'] = types.ModuleType(
            f'gi.repository.{name}')
    # The class statement `class QuickActionsPanel(Gtk.Box)` evaluates its
    # base class at import time even though widgets are never constructed.
    sys.modules['gi.repository.Gtk'].Box = type('Box', (), {})

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib
    sys.modules['gi.repository.Gio'] = gio


_install_fake_gi()

import quick_actions  # noqa: E402  (needs fake gi modules installed first)


# --- fakes -----------------------------------------------------------------

class _Reply:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return (self._value,)


class _Bus:
    """System-bus stand-in: call_sync answers from an ordered reply script;
    entries may be values (wrapped) or exceptions (raised)."""

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = []
        self.registrations = []
        self.unregistered = []
        self.signals = []

    def call_sync(self, *args):
        self.calls.append(args)
        if not self.replies:
            return _Reply(False)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Reply(reply)

    def register_object(self, *args):
        self.registrations.append(args)
        return len(self.registrations)

    def unregister_object(self, obj_id):
        self.unregistered.append(obj_id)

    def emit_signal(self, *args):
        self.signals.append(args)


class _FakeLocation:
    def __init__(self, available=True):
        self.available_flag = available
        self.enabled = False
        self.set_calls = []

    def available(self):
        return self.available_flag

    def set_enabled(self, value):
        self.enabled = bool(value)
        self.set_calls.append(value)


class _FakeALS:
    def __init__(self, available=False):
        self._available = available

    def available(self):
        return self._available


def _bare_panel(location=None) -> quick_actions.QuickActionsPanel:
    panel = object.__new__(quick_actions.QuickActionsPanel)
    panel._dnd = None
    panel._focus = None
    panel._hud = None
    panel._als = _FakeALS()
    panel._location = location if location is not None else _FakeLocation()
    return panel


def _pin_dbus_seams(monkeypatch):
    """Pin GLib/Gio bindings on the imported quick_actions module itself.

    Which fake gi ends up bound depends on which test module first imports
    quick_actions during collection (see test_hud_silent), so tests that
    exercise D-Bus paths pin the exact seam objects they assert on.
    """
    class _FakeInterface:
        pass

    class _FakeNodeInfo:
        interfaces = [_FakeInterface()]

        @staticmethod
        def new_for_xml(xml):
            assert 'AuthorizeApp' in xml
            assert 'MaxAccuracyLevel' in xml
            return _FakeNodeInfo()

    glib = types.SimpleNamespace(
        Error=Exception,
        Variant=_RecordingVariant,
        VariantType=lambda *a, **k: None,
    )
    gio = types.SimpleNamespace(
        DBusCallFlags=types.SimpleNamespace(NONE=0),
        DBusSignalFlags=types.SimpleNamespace(NONE=0),
        DBusNodeInfo=_FakeNodeInfo,
    )
    monkeypatch.setattr(quick_actions, 'GLib', glib)
    monkeypatch.setattr(quick_actions, 'Gio', gio)


def _panel_keys(panel):
    return [t.key for t in panel._tiles()]


# --- Hotspot gating ---------------------------------------------------------

class TestHotspotGating:
    def test_tile_appears_with_wifi_device(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', lambda: True)
        assert 'hotspot' in _panel_keys(_bare_panel())

    def test_tile_hidden_without_wifi_device_or_nm(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', lambda: False)
        assert 'hotspot' not in _panel_keys(_bare_panel())

    def test_gate_uses_nm_dbus_probe_family(self):
        """The gate must reuse the WiFi tile's D-Bus probing, not a new stack."""
        import inspect
        src = inspect.getsource(quick_actions._nm_has_wifi_device)
        assert '_dbus_system()' in src
        assert 'NetworkManager' in src
        assert 'DeviceType' in src


class TestHotspotToggleArgv:
    def test_enable_issues_nmcli_hotspot(self, monkeypatch):
        calls = []
        monkeypatch.setattr(subprocess, 'Popen',
                            lambda cmd, **k: calls.append(cmd))
        quick_actions._toggle_hotspot(True)
        assert calls == [['nmcli', 'device', 'wifi', 'hotspot']]

    def test_disable_downs_the_hotspot_connection(self, monkeypatch):
        calls = []
        monkeypatch.setattr(subprocess, 'Popen',
                            lambda cmd, **k: calls.append(cmd))
        quick_actions._toggle_hotspot(False)
        assert calls == [['nmcli', 'connection', 'down', 'Hotspot']]

    def test_missing_nmcli_does_not_raise(self, monkeypatch):
        def _boom(cmd, **k):
            raise FileNotFoundError('nmcli')
        monkeypatch.setattr(subprocess, 'Popen', _boom)
        quick_actions._toggle_hotspot(False)


class TestHotspotStateQuery:
    def test_active_connection_reports_on(self, monkeypatch):
        monkeypatch.setattr(subprocess, 'check_output',
                            lambda cmd, **k: 'MyWifi\nHotspot\n')
        assert quick_actions._get_hotspot_state() is True

    def test_no_hotspot_connection_reports_off(self, monkeypatch):
        monkeypatch.setattr(subprocess, 'check_output',
                            lambda cmd, **k: 'MyWifi\n')
        assert quick_actions._get_hotspot_state() is False

    def test_nmcli_failure_reports_unknown(self, monkeypatch):
        def _fail(cmd, **k):
            raise subprocess.CalledProcessError(1, 'nmcli')
        monkeypatch.setattr(subprocess, 'check_output', _fail)
        assert quick_actions._get_hotspot_state() is None

    def test_nmcli_missing_reports_unknown(self, monkeypatch):
        def _missing(cmd, **k):
            raise OSError('nmcli')
        monkeypatch.setattr(subprocess, 'check_output', _missing)
        assert quick_actions._get_hotspot_state() is None


# --- Location gating --------------------------------------------------------

class TestLocationGating:
    def test_tile_appears_when_geoclue_available(self):
        assert 'location' in _panel_keys(_bare_panel())

    def test_tile_hidden_when_geoclue_unavailable(self):
        assert 'location' not in _panel_keys(
            _bare_panel(location=_FakeLocation(available=False)))

    def test_expanded_tier_orders_both_after_auto_brightness(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', lambda: True)

        class _ALS:
            def available(self):
                return True

        class _LedDir:
            def glob(self, pattern):
                return iter([object()])
        monkeypatch.setattr(quick_actions, 'Path', lambda p: _LedDir())
        panel = _bare_panel()
        panel._als = _ALS()
        keys = [t.key for t in panel._tiles()]
        assert keys[-3:] == ['auto_br', 'location', 'hotspot']

    def test_panel_toggle_routes_to_location_state(self):
        loc = _FakeLocation()
        panel = _bare_panel(location=loc)
        tiles = {t.key: t for t in panel._tiles()}
        assert tiles['location'].get_state() is False
        tiles['location'].set_state(True)
        assert loc.enabled is True


# --- Location mechanism (GeoClue2 agent) ------------------------------------

class TestLocationAgentMechanism:
    def test_enable_registers_agent_and_announces_accuracy(self, monkeypatch):
        bus = _Bus()
        _pin_dbus_seams(monkeypatch)
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()

        state.set_enabled(True)

        assert state.enabled is True
        assert len(bus.registrations) == 1
        reg_path = bus.registrations[0][0]
        assert reg_path == quick_actions._AGENT_PATH
        register_call = bus.calls[-1]
        assert register_call[:4] == (
            'org.freedesktop.GeoClue2', '/org/freedesktop/GeoClue2/Manager',
            'org.freedesktop.GeoClue2.Manager', 'RegisterAgent')
        assert register_call[4].args == ('(o)', (reg_path,))
        signals = [(sig[2], sig[3], sig[4]) for sig in bus.signals]
        assert signals == [('org.freedesktop.DBus.Properties',
                            'PropertiesChanged',
                            _RecordingVariant(
                                '(sa{sv}as)',
                                ('org.freedesktop.GeoClue2.Agent',
                                 {'MaxAccuracyLevel': _RecordingVariant('u', 8)},
                                 [])))]

    def test_disabled_agent_revokes_everything(self):
        state = quick_actions.LocationState()
        assert state._max_accuracy() == 0
        assert state._allow(8) == 0
        assert state._allow(4) == 0

    def test_enabled_agent_grants_requested_accuracy_capped_at_exact(self):
        state = quick_actions.LocationState()
        state.enabled = True
        assert state._max_accuracy() == 8
        assert state._allow(4) == 4
        assert state._allow(99) == 8

    def test_authorize_app_returns_denial_while_disabled(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        class _Params:
            def unpack(self):
                return ('org.example.MapApp', 4)

        class _Invocation:
            def __init__(self):
                self.value = None

            def return_value(self, value):
                self.value = value
        state = quick_actions.LocationState()
        invocation = _Invocation()
        state._on_method_call(None, None, None, None, 'AuthorizeApp',
                              _Params(), invocation)
        assert invocation.value.args == ('(u)', (0,))

    def test_authorize_app_returns_requested_while_enabled(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        class _Params:
            def unpack(self):
                return ('org.example.MapApp', 4)

        class _Invocation:
            def __init__(self):
                self.value = None

            def return_value(self, value):
                self.value = value
        state = quick_actions.LocationState()
        state.enabled = True
        invocation = _Invocation()
        state._on_method_call(None, None, None, None, 'AuthorizeApp',
                              _Params(), invocation)
        assert invocation.value.args == ('(u)', (4,))

    def test_property_getter_tracks_max_accuracy_level(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        state = quick_actions.LocationState()
        prop = state._on_get_property(None, None, None, None,
                                      'MaxAccuracyLevel')
        assert prop.args == ('u', 0)
        state.enabled = True
        prop = state._on_get_property(None, None, None, None,
                                      'MaxAccuracyLevel')
        assert prop.args == ('u', 8)

    def test_geoclue_absent_means_hidden_and_never_registered(
            self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus(replies=[Exception('ServiceUnknown')])
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()
        assert state.available() is False
        assert bus.registrations == []

    def test_rejected_agent_stays_hidden_and_cleans_up(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        """Geoclue rejects unwhitelisted agents: probe OK, RegisterAgent not."""
        bus = _Bus(replies=[_Reply(False), Exception('Unauthorized')])
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()
        assert state.available() is False
        assert len(bus.registrations) == 1
        assert bus.unregistered == [1]

    def test_accepted_registration_makes_tile_available(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus(replies=[_Reply(False)])
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()
        assert state.available() is True
        assert state.enabled is False

    def test_no_bus_toggle_keeps_intent_without_raising(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: None)
        state = quick_actions.LocationState()
        state.set_enabled(True)
        assert state.enabled is True


# --- mid-session disappearance ----------------------------------------------

class TestServiceDisappearsMidSession:
    def test_refresh_survives_hotspot_query_dying_mid_session(
            self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', lambda: True)
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: None)

        def _died():
            raise RuntimeError('NM went away')
        monkeypatch.setattr(quick_actions, '_get_hotspot_state', _died)

        panel = _bare_panel()
        panel._tile_buttons = {}
        panel._state_labels = {}
        panel._tile_state = {}
        panel._updating = False

        assert panel._refresh_all_states() is False
        assert panel._tile_state.get('location') is False

    def test_set_enabled_with_dead_service_never_raises(self, monkeypatch):
        bus = _Bus()
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()
        state.set_enabled(True)

        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: None)
        state.set_enabled(False)
        assert state.enabled is False
