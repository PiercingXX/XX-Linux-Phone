"""Notification action targets (w5_fixup_7 item 2).

A notification may carry the ``x-xx-wm-action-target`` hint so that
invoking one of its actions also wakes the D-Bus-activatable service
that posted it. The fake bus records the forward call (destination,
auto-start flags, timeout); malformed hints are ignored while
ActionInvoked is still emitted.
"""
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import notif_daemon  # noqa: E402

TARGET = ('io.piercingxx.XXDialer.Policy /io/piercingxx/XXDialer/Policy '
          'io.piercingxx.XXDialer.Policy1')


class _Variant:
    def __init__(self, *args):
        self.args = args


class _FakeGLib:
    Error = Exception
    Variant = _Variant
    idle_add = staticmethod(lambda *a, **k: 1)
    timeout_add = staticmethod(lambda *a, **k: 1)
    source_remove = staticmethod(lambda *a, **k: None)


class _FakeGio:
    DBusCallFlags = SimpleNamespace(NONE=0, NO_AUTO_START=2)


class _FakeBus:
    def __init__(self):
        self.signals = []
        self.calls = []
        self.call_error = None

    def emit_signal(self, *args):
        self.signals.append(args)

    def call(self, bus_name, obj_path, iface, method, params=None,
             reply_type=None, flags=None, timeout=-1, cancellable=None,
             callback=None):
        if self.call_error is not None:
            raise self.call_error
        self.calls.append({
            'bus': bus_name, 'path': obj_path, 'iface': iface,
            'method': method, 'params': params, 'reply_type': reply_type,
            'flags': flags, 'timeout': timeout, 'callback': callback,
        })

    def call_finish(self, result):
        if isinstance(result, Exception):
            raise result
        return result


def _daemon(monkeypatch, hints, bus=None):
    daemon = object.__new__(notif_daemon.NotificationDaemon)
    daemon._on_notify = lambda *a: None
    daemon._on_close = lambda _i: None
    daemon._bus = bus if bus is not None else _FakeBus()
    daemon._reg_id = 0
    daemon._name_id = 0
    daemon._next_id = 1
    daemon._owns_name = False
    daemon._expire_timers = {}
    daemon._notif_hints = dict(hints)
    daemon._takes_actions = None
    monkeypatch.setattr(notif_daemon, 'GLib', _FakeGLib)
    monkeypatch.setattr(notif_daemon, 'Gio', _FakeGio)
    return daemon


class TestActionTargetForward:
    def test_valid_hint_emits_signal_and_forwards(self, monkeypatch):
        daemon = _daemon(monkeypatch, {7: {'x-xx-wm-action-target': TARGET}})
        daemon.invoke_action(7, 'call-back')
        assert len(daemon._bus.signals) == 1
        assert daemon._bus.signals[0][3] == 'ActionInvoked'
        assert daemon._bus.signals[0][4].args == ('(us)', (7, 'call-back'))
        assert len(daemon._bus.calls) == 1
        call = daemon._bus.calls[0]
        assert (call['bus'], call['path'], call['iface'],
                call['method']) == (
            'io.piercingxx.XXDialer.Policy',
            '/io/piercingxx/XXDialer/Policy',
            'io.piercingxx.XXDialer.Policy1',
            'HandleNotificationAction')
        assert call['params'].args == ('(us)', (7, 'call-back'))
        assert call['flags'] == _FakeGio.DBusCallFlags.NONE
        assert call['flags'] & _FakeGio.DBusCallFlags.NO_AUTO_START == 0
        assert call['timeout'] == 5000
        assert callable(call['callback'])

    def test_no_hint_only_emits_signal(self, monkeypatch):
        daemon = _daemon(monkeypatch, {7: {'urgency': 1}})
        daemon.invoke_action(7, 'open')
        assert len(daemon._bus.signals) == 1
        assert daemon._bus.calls == []

    def test_unknown_id_only_emits_signal(self, monkeypatch):
        daemon = _daemon(monkeypatch, {})
        daemon.invoke_action(99, 'open')
        assert len(daemon._bus.signals) == 1
        assert daemon._bus.calls == []

    @pytest.mark.parametrize('hint', [
        '',
        '   ',
        'only-bus-name',
        'two parts',
        'one two three four',
        '!!! /io/x/Object com.example.Iface',
        'com.example..double /io/x/Object com.example.Iface',
        '1com.example /io/x/Object com.example.Iface',
        'com.example no-leading-slash com.example.Iface',
        'com.example /io//double com.example.Iface',
        'com.example /io/x/Object no-dot-iface',
        'com.example /io/x/Object 1bad.Iface',
        True,
        42,
        ['com.example', '/io/x', 'com.example.I'],
        None,
    ])
    def test_malformed_hints_ignored_signal_kept(
            self, monkeypatch, hint):
        daemon = _daemon(
            monkeypatch, {7: {'x-xx-wm-action-target': hint}})
        daemon.invoke_action(7, 'open')
        assert len(daemon._bus.signals) == 1
        assert daemon._bus.calls == []

    def test_dispatch_failure_only_logs_debug(self, monkeypatch, caplog):
        daemon = _daemon(monkeypatch, {7: {'x-xx-wm-action-target': TARGET}})
        daemon._bus.call_error = RuntimeError('bus wedged')
        with caplog.at_level(logging.DEBUG, logger='piercing.notif_daemon'):
            daemon.invoke_action(7, 'open')
        assert len(daemon._bus.signals) == 1
        assert any('action target call failed' in r.message
                   for r in caplog.records)

    def test_reply_failure_only_logs_debug(self, monkeypatch, caplog):
        daemon = _daemon(monkeypatch, {7: {'x-xx-wm-action-target': TARGET}})
        daemon.invoke_action(7, 'open')
        callback = daemon._bus.calls[0]['callback']
        with caplog.at_level(logging.DEBUG, logger='piercing.notif_daemon'):
            callback(daemon._bus, RuntimeError('no reply'))
        assert any('action target call failed' in r.message
                   for r in caplog.records)

    def test_reply_success_stays_silent(self, monkeypatch, caplog):
        daemon = _daemon(monkeypatch, {7: {'x-xx-wm-action-target': TARGET}})
        daemon.invoke_action(7, 'open')
        callback = daemon._bus.calls[0]['callback']
        with caplog.at_level(logging.DEBUG, logger='piercing.notif_daemon'):
            callback(daemon._bus, object())
        assert caplog.records == []


class TestParseActionTarget:
    def test_valid_triplet(self):
        assert notif_daemon.parse_action_target(TARGET) == (
            'io.piercingxx.XXDialer.Policy',
            '/io/piercingxx/XXDialer/Policy',
            'io.piercingxx.XXDialer.Policy1')

    def test_unique_name_accepted(self):
        assert notif_daemon.parse_action_target(
            ':1.42 /io/x/Object com.example.Service') == (
            ':1.42', '/io/x/Object', 'com.example.Service')

    def test_variant_wrapped_hint_unwrapped(self):
        assert notif_daemon.parse_action_target(
            _Variant('(s)', (TARGET,))) is None

        class _StrVariant:
            def unpack(self):
                return TARGET

        assert notif_daemon.parse_action_target(_StrVariant()) == (
            'io.piercingxx.XXDialer.Policy',
            '/io/piercingxx/XXDialer/Policy',
            'io.piercingxx.XXDialer.Policy1')

    def test_extra_whitespace_tolerated(self):
        assert notif_daemon.parse_action_target(
            '  com.example.Svc   /io/x/Object   com.example.Svc  ') == (
            'com.example.Svc', '/io/x/Object', 'com.example.Svc')

    def test_root_path_accepted(self):
        assert notif_daemon.parse_action_target(
            'com.example.Svc / com.example.Svc') == (
            'com.example.Svc', '/', 'com.example.Svc')

    @pytest.mark.parametrize('hint', [
        None, True, 7, b'com.example /o com.example.I',
        '', 'a b', 'a b c d', 'nodots /o/com com.example.I',
        'com.example relative/path com.example.I',
    ])
    def test_rejected(self, hint):
        assert notif_daemon.parse_action_target(hint) is None
