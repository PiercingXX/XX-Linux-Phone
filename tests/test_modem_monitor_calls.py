"""ModemMonitor call-answer surface (P2-D).

Covers the slice's blocker fix: accept/hangup reach the MM1 Call object
methods with the right paths, degrade to False without a bus or call, the
per-call StateChanged subscriptions no longer grow without bound, and a
failed bus init is logged instead of swallowed.

Fakes are always reached through modem_monitor.GLib/Gio: sibling test
modules also install fake gi packages at collection time, so sys.modules
contents are not stable but the module's own bound references are.
"""
import logging
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


def _install_fake_gi() -> None:
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.Error = type('Error', (Exception,), {})
    glib.idle_add = lambda *a, **k: 1
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None

    gio = types.ModuleType('gi.repository.Gio')
    gio.BusType = types.SimpleNamespace(SYSTEM=1)
    gio.DBusSignalFlags = types.SimpleNamespace(NONE=0)
    gio.DBusCallFlags = types.SimpleNamespace(NONE=0)
    gio.bus_get_sync = lambda *a, **k: None

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

import modem_monitor  # noqa: E402  (needs fake gi modules installed first)

_CALL_IFACE = 'org.freedesktop.ModemManager1.Call'
_OBJMGR_IFACE = 'org.freedesktop.DBus.ObjectManager'
_MM1 = 'org.freedesktop.ModemManager1'


class FakeParams:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


class FakeBus:
    def __init__(self):
        self.subs = []
        self.unsubs = []
        self.calls = []
        self._next = 1

    def signal_subscribe(self, sender, iface, signal, path, *_rest):
        self.subs.append({'sender': sender, 'iface': iface,
                          'signal': signal, 'path': path})
        sid = self._next
        self._next += 1
        return sid

    def signal_unsubscribe(self, sid):
        self.unsubs.append(sid)

    def call_sync(self, bus_name, path, iface, method, *_rest):
        self.calls.append((bus_name, path, iface, method))


def _make_monitor(bus, monkeypatch):
    """Monitor wired to a FakeBus with a drainable idle queue.

    Returns (monitor, fired, pending); pending is the live list that later
    GLib.idle_add calls append to, so tests drain it after termination or
    removal events.
    """
    glib, gio = modem_monitor.GLib, modem_monitor.Gio
    pending: list = []
    fired = {'incoming': [], 'answered': [], 'ended': 0}

    monkeypatch.setattr(glib, 'idle_add',
                        lambda cb, *a: (pending.append((cb, a)), len(pending))[1])
    monkeypatch.setattr(gio, 'bus_get_sync', lambda *a, **k: bus)
    monitor = modem_monitor.ModemMonitor(
        on_incoming=lambda c, n: fired['incoming'].append((c, n)),
        on_answered=lambda c, n: fired['answered'].append((c, n)),
        on_ended=lambda: fired.__setitem__('ended', fired['ended'] + 1),
    )
    while pending:
        cb, args = pending.pop(0)
        cb(*args)
    return monitor, fired, pending


def _interfaces_added(monitor, obj_path, direction=1, state=3):
    monitor._on_interfaces_added(
        None, None, None, None, None,
        FakeParams((obj_path, {_CALL_IFACE: {
            'Direction': direction, 'State': state, 'Number': 'tel:+15550001111',
        }})),
        None,
    )


def _interfaces_removed(monitor, obj_path):
    monitor._on_interfaces_removed(
        None, None, None, None, None,
        FakeParams((obj_path, [_CALL_IFACE])),
        None,
    )


def _state_changed(monitor, obj_path, new_state):
    monitor._on_state_changed(obj_path, '+15550001111',
                              FakeParams((3, new_state, 0)))


class TestAcceptHangupDBus:
    def test_accept_calls_mm1_accept_with_path(self, monkeypatch):
        bus = FakeBus()
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync', lambda *a, **k: bus)
        assert modem_monitor.accept_call('/org/freedesktop/ModemManager1/Call/7') is True
        assert bus.calls == [(
            _MM1, '/org/freedesktop/ModemManager1/Call/7', _CALL_IFACE, 'Accept',
        )]

    def test_hangup_calls_mm1_hangup_with_path(self, monkeypatch):
        bus = FakeBus()
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync', lambda *a, **k: bus)
        assert modem_monitor.hangup_call('/org/freedesktop/ModemManager1/Call/9') is True
        assert bus.calls == [(
            _MM1, '/org/freedesktop/ModemManager1/Call/9', _CALL_IFACE, 'Hangup',
        )]

    def test_graceful_false_without_bus(self, monkeypatch, caplog):
        def _no_bus(*a, **k):
            raise modem_monitor.GLib.Error('no system bus')
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync', _no_bus)
        with caplog.at_level(logging.WARNING):
            assert modem_monitor.accept_call('/org/freedesktop/ModemManager1/Call/1') is False
            assert modem_monitor.hangup_call('/org/freedesktop/ModemManager1/Call/1') is False
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2

    def test_graceful_false_when_call_absent(self, monkeypatch, caplog):
        bus = FakeBus()

        def _missing(*a, **k):
            raise modem_monitor.GLib.Error('no such object')
        bus.call_sync = _missing
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync', lambda *a, **k: bus)
        with caplog.at_level(logging.WARNING):
            assert modem_monitor.hangup_call('/org/freedesktop/ModemManager1/Call/404') is False
        assert any('Hangup' in r.getMessage() for r in caplog.records)


class TestSubscriptionLifecycle:
    def test_repeated_same_path_does_not_grow_subscriptions(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _ = _make_monitor(bus, monkeypatch)
        base = len(bus.subs)
        _interfaces_added(monitor, '/org/freedesktop/ModemManager1/Call/1')
        after_first = len(bus.subs)
        _interfaces_added(monitor, '/org/freedesktop/ModemManager1/Call/1')
        assert after_first == base + 1
        assert len(bus.subs) == after_first

    def test_state_changed_sub_targets_the_call_object(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _ = _make_monitor(bus, monkeypatch)
        _interfaces_added(monitor, '/org/freedesktop/ModemManager1/Call/2')
        state_subs = [s for s in bus.subs if s['signal'] == 'StateChanged']
        assert len(state_subs) == 1
        assert state_subs[0]['path'] == '/org/freedesktop/ModemManager1/Call/2'
        assert state_subs[0]['iface'] == _CALL_IFACE

    def test_interfaces_removed_unsubscribes_and_ends_call(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, pending = _make_monitor(bus, monkeypatch)
        _interfaces_added(monitor, '/org/freedesktop/ModemManager1/Call/3')
        sub_ids_before = {s for s in range(1, bus._next)}
        _interfaces_removed(monitor, '/org/freedesktop/ModemManager1/Call/3')
        while pending:
            cb, args = pending.pop(0)
            cb(*args)
        assert len(bus.unsubs) == 1
        assert bus.unsubs[0] in sub_ids_before
        assert monitor._active_call_path is None
        assert fired['ended'] == 1

    def test_terminated_state_unsubscribes(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, pending = _make_monitor(bus, monkeypatch)
        _interfaces_added(monitor, '/org/freedesktop/ModemManager1/Call/4')
        _state_changed(monitor, '/org/freedesktop/ModemManager1/Call/4',
                       modem_monitor._STATE_TERMINATED)
        while pending:
            cb, args = pending.pop(0)
            cb(*args)
        assert len(bus.unsubs) == 1
        assert '/org/freedesktop/ModemManager1/Call/4' not in monitor._state_subs
        assert fired['ended'] == 1

    def test_distinct_calls_each_get_one_subscription(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _ = _make_monitor(bus, monkeypatch)
        base = len(bus.subs)
        _interfaces_added(monitor, '/org/freedesktop/ModemManager1/Call/5')
        _interfaces_added(monitor, '/org/freedesktop/ModemManager1/Call/6')
        assert len(bus.subs) == base + 2


class TestBusInit:
    def test_bus_init_failure_logged_once(self, monkeypatch, caplog):
        glib, gio = modem_monitor.GLib, modem_monitor.Gio

        def _fail(*a, **k):
            raise glib.Error('system bus not reachable')

        monkeypatch.setattr(glib, 'idle_add', lambda cb, *a: cb(*a) or 1)
        monkeypatch.setattr(gio, 'bus_get_sync', _fail)
        with caplog.at_level(logging.WARNING):
            modem_monitor.ModemMonitor(lambda c, n: None, lambda c, n: None,
                                       lambda: None)
        bus_warnings = [r for r in caplog.records
                        if r.levelno == logging.WARNING and 'bus' in r.getMessage()]
        assert len(bus_warnings) == 1


class TestActiveCallPath:
    def test_tracks_current_call(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _ = _make_monitor(bus, monkeypatch)
        _interfaces_added(monitor, '/org/freedesktop/ModemManager1/Call/8')
        assert modem_monitor.active_call_path() == '/org/freedesktop/ModemManager1/Call/8'
        _interfaces_removed(monitor, '/org/freedesktop/ModemManager1/Call/8')
        assert modem_monitor.active_call_path() is None

    def test_none_without_monitor(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, '_last_monitor', None)
        assert modem_monitor.active_call_path() is None
