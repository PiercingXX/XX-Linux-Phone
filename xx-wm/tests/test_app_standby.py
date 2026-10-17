"""Tests for app standby (Doze parity): scope naming, launch-to-scope,
policy table, thaw-before ordering, degraded freezer, saver, queries."""
import fnmatch
import json
import os
import signal
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

gi = pytest.importorskip('gi')

from gi.repository import GLib  # noqa: E402

import app_standby  # noqa: E402
from app_standby import (  # noqa: E402
    StandbyController,
    configure_standby,
    dbus_bus_name_for_app,
    get_standby,
    install_saver_check_gates,
    is_keyboard_app,
    is_telephony_app,
    launch_with_pid,
    normalize_app_id,
    parse_pw_dump_pids,
    parse_standby_scope,
    proc_starttime,
    query_battery,
    query_calls_owner_pid,
    query_mpris_playing,
    sanitize_app_id,
    saver_blank_seconds,
    saver_effective_minutes,
    scope_name_for,
    select_app_units,
    should_standby,
    stopped_file_path,
    unit_patterns_for_app,
)
from app_index import AppEntry  # noqa: E402


# --- fakes (mirror the real APIs, per the runtime traps) --------------------

class _Reply:
    """Gio D-Bus replies are a TUPLE of out-args: unpack() -> ({...},)."""

    def __init__(self, value):
        self._value = value

    def unpack(self):
        return (self._value,)


class _FakeBus:
    """Session/system bus stand-in.

    call_sync answers from a handler; call() (async, no user_data — the
    callback gets exactly (conn, result)) records and completes at once.
    """

    def __init__(self, on_sync=None, fail_async=False):
        self.sync_calls = []
        self.calls = []
        self._on_sync = on_sync or (lambda *a: _Reply(None))
        self.fail_async = fail_async

    def call_sync(self, *args):
        self.sync_calls.append(args)
        return self._on_sync(*args)

    def call(self, *args):
        assert len(args) == 10, f'bus.call takes 10 args, got {len(args)}'
        self.calls.append(args)
        callback = args[-1]
        callback(self, object())

    def call_finish(self, res):
        if self.fail_async:
            raise Exception('fake async D-Bus failure')
        return None


class _FakeDesktopApp:
    def __init__(self, pids=(), fail=None):
        self.pids = list(pids)
        self.fail = fail
        self.launched = []
        self.callback = None

    def launch_uris_as_manager(self, uris, ctx, flags, setup,
                               setup_data, callback, callback_data):
        self.launched.append(('as_manager', list(uris), flags))
        self.callback = callback
        if self.fail is not None:
            raise self.fail
        for pid in self.pids:
            callback(self, pid, callback_data)
        return True

    def launch(self, uris, ctx):
        self.launched.append(('plain', list(uris)))
        if self.fail is not None:
            raise self.fail
        return True


class _FakeTop:
    def __init__(self, app_id, activated=False, handle=None):
        self.app_id = app_id
        self.handle = handle if handle is not None else object()
        if activated != 'missing':
            self.activated = activated


class _FakeManager:
    def __init__(self, toplevels=(), with_signal=False):
        self._tops = list(toplevels)
        self.activated_handles = []
        if with_signal:
            self.on_activated_changed = self._subscribe
        self._callback = None

    def _subscribe(self, callback):
        self._callback = callback

    def fire(self, *args, **kwargs):
        self._callback(*args, **kwargs)

    def list(self):
        return list(self._tops)

    def available(self):
        return True

    def activate(self, handle):
        self.activated_handles.append(handle)


def _config(**overrides):
    data = dict(app_standby_minutes=10, standby_exempt=[],
                battery_saver=False, battery_saver_auto=True)
    data.update(overrides)
    cfg = types.SimpleNamespace(**data)
    cfg.set_battery_saver = lambda v: setattr(cfg, 'battery_saver', bool(v))
    return cfg


def _controller(bus=None, manager=None, **kwargs):
    kwargs.setdefault('config', _config())
    kwargs.setdefault('session_bus_factory', lambda: bus)
    kwargs.setdefault('system_bus_factory', lambda: None)
    if manager is not None:
        kwargs['manager'] = manager
    return StandbyController(**kwargs)


def _entry(app_id, desktop_app):
    return AppEntry(app_id=app_id, name=app_id, description='',
                    executable='', search_text=app_id, desktop_app=desktop_app)


@pytest.fixture
def kills(monkeypatch):
    """Capture os.kill without signalling anything."""
    calls = []

    def _kill(pid, sig):
        calls.append((pid, sig))

    monkeypatch.setattr(os, 'kill', _kill)
    return calls


# --- scope naming ------------------------------------------------------------

class TestScopeNaming:
    def test_scope_name_template(self):
        assert scope_name_for('org.gnome.Evolution.desktop', 1234) == \
            'app-xxwm-org.gnome.Evolution.desktop-1234.scope'

    def test_sanitize_table(self):
        assert sanitize_app_id('my.app_v2') == 'my.app_v2'
        assert sanitize_app_id('my app/x@y') == 'my_app_x_y'
        assert sanitize_app_id('') == 'unknown'
        assert sanitize_app_id('   ') == 'unknown'

    def test_scope_names_differ_per_pid(self):
        assert (scope_name_for('a.desktop', 1)
                != scope_name_for('a.desktop', 2))

    def test_normalize_strips_desktop_and_case(self):
        assert normalize_app_id('Org.GNOME.Calls.DESKTOP') == \
            normalize_app_id('org.gnome.calls')


# --- launch -> scope request ---------------------------------------------------

class TestLaunchToScope:
    def test_track_builds_scope_request(self):
        bus = _FakeBus()
        ctl = _controller(bus=bus, manager=_FakeManager())
        ctl.track('org.example.App.desktop', 4242, now=100.0)
        assert len(bus.calls) == 1
        dest, path, iface, method, params = bus.calls[0][:5]
        assert dest == 'org.freedesktop.systemd1'
        assert path == '/org/freedesktop/systemd1'
        assert iface == 'org.freedesktop.systemd1.Manager'
        assert method == 'StartTransientUnit'
        name, mode, props, aux = params.unpack()
        assert name == 'app-xxwm-org.example.App.desktop-4242.scope'
        assert mode == 'fail'
        assert props == [('PIDs', [4242])]
        assert aux == []
        rec = ctl._apps['org.example.App.desktop']
        assert rec.pids == {4242}
        assert rec.last_active == 100.0

    def test_track_without_bus_records_pid_only(self):
        ctl = _controller(bus=None, manager=_FakeManager())
        ctl.track('org.example.App.desktop', 4242, now=100.0)
        rec = ctl._apps['org.example.App.desktop']
        assert rec.pids == {4242}
        assert rec.scopes == {}

    def test_scope_failure_leaves_pid_scopeless(self):
        bus = _FakeBus(fail_async=True)
        ctl = _controller(bus=bus, manager=_FakeManager())
        ctl.track('org.example.App.desktop', 4242, now=100.0)
        assert ctl._apps['org.example.App.desktop'].scopes == {}
        assert ctl._apps['org.example.App.desktop'].pids == {4242}

    def test_bad_pids_ignored(self):
        ctl = _controller(bus=_FakeBus(), manager=_FakeManager())
        ctl.track('a.desktop', 0)
        ctl.track('a.desktop', -5)
        ctl.track('a.desktop', 'nope')
        assert 'a.desktop' not in ctl._apps


class TestLaunchWithPid:
    def test_pid_callback_reports_child(self):
        seen = []
        info = _FakeDesktopApp(pids=[777])
        assert launch_with_pid(info, on_pid=seen.append) is True
        assert seen == [777]
        kind, _uris, flags = info.launched[0]
        assert kind == 'as_manager'
        assert int(flags) & int(GLib.SpawnFlags.SEARCH_PATH)

    def test_callback_errors_never_break_launch(self):
        def _boom(pid):
            raise RuntimeError('tracker down')

        info = _FakeDesktopApp(pids=[777])
        assert launch_with_pid(info, on_pid=_boom) is True

    def test_glib_error_propagates(self):
        info = _FakeDesktopApp(fail=GLib.Error('launch failed'))
        with pytest.raises(GLib.Error):
            launch_with_pid(info, on_pid=lambda pid: None)

    def test_fallback_to_plain_launch(self):
        info = _FakeDesktopApp()
        info.launch_uris_as_manager = None
        assert launch_with_pid(info, on_pid=lambda pid: None) is True
        assert info.launched[0][0] == 'plain'


# --- policy table ---------------------------------------------------------------

def _policy(now=1000.0, **kwargs):
    params = dict(app_id='org.example.App.desktop', now=now,
                  last_active=now - 60.0, screen_blanked=False,
                  blank_since=None, standby_minutes=10)
    params.update(kwargs)
    return should_standby(**params)


class TestPolicy:
    def test_recently_used_stays(self):
        assert _policy(last_active=1000.0 - 9 * 60) is False

    def test_idle_past_timeout_is_candidate(self):
        assert _policy(last_active=1000.0 - 11 * 60) is True

    def test_boundary_is_inclusive(self):
        assert _policy(last_active=1000.0 - 10 * 60) is True

    def test_zero_disables_even_when_idle(self):
        assert _policy(standby_minutes=0,
                       last_active=1000.0 - 10 * 24 * 3600) is False

    def test_zero_disables_blank_rule(self):
        assert _policy(standby_minutes=0, screen_blanked=True,
                       blank_since=1000.0 - 3600) is False

    def test_blank_past_two_minutes_is_candidate(self):
        assert _policy(screen_blanked=True, last_active=1000.0 - 30,
                       blank_since=1000.0 - 121) is True

    def test_blank_before_two_minutes_stays(self):
        assert _policy(screen_blanked=True, last_active=1000.0 - 30,
                       blank_since=1000.0 - 119) is False

    def test_blank_without_timestamp_stays(self):
        assert _policy(screen_blanked=True, blank_since=None) is False

    def test_custom_blank_window(self):
        assert _policy(screen_blanked=True, blank_since=1000.0 - 31,
                       blank_seconds=30) is True
        assert _policy(screen_blanked=True, blank_since=1000.0 - 29,
                       blank_seconds=30) is False

    def test_unknown_activation_never_freezes(self):
        assert _policy(last_active=None) is False

    def test_mpris_playing_exempt(self):
        assert _policy(last_active=0.0, mpris_playing=True) is False

    def test_audio_busy_exempt(self):
        assert _policy(last_active=0.0, audio_busy=True) is False

    def test_telephony_exempt(self):
        assert _policy(last_active=0.0, is_telephony=True) is False

    def test_keyboard_exempt(self):
        assert _policy(last_active=0.0, is_keyboard=True) is False

    def test_exempt_list_matches_without_desktop_and_case(self):
        idle = dict(last_active=0.0)
        assert _policy(exempt_ids=['org.example.app'], **idle) is False
        assert _policy(exempt_ids=['ORG.EXAMPLE.APP.DESKTOP'], **idle) is False
        assert _policy(exempt_ids=['other.app'], **idle) is True

    def test_recent_notification_action_exempt(self):
        assert _policy(last_active=0.0, last_notif_action=1000.0 - 59) is False

    def test_old_notification_action_expires(self):
        assert _policy(last_active=0.0, last_notif_action=1000.0 - 61) is True

    def test_exemptions_win_over_blank(self):
        assert _policy(screen_blanked=True, blank_since=0.0,
                       mpris_playing=True) is False


class TestExemptMatching:
    def test_telephony_ids(self):
        assert is_telephony_app('org.gnome.Calls.desktop') is True
        assert is_telephony_app('xx-dialer.desktop') is True
        assert is_telephony_app('org.mozilla.firefox.desktop') is False
        assert is_telephony_app('') is False

    def test_keyboard_ids(self):
        assert is_keyboard_app('sm.puri.squeekboard.desktop') is True
        assert is_keyboard_app('stevia.desktop') is True
        assert is_keyboard_app('io.piercingxx.XXKeyboard') is True
        assert is_keyboard_app('xx-keyboard') is True
        assert is_keyboard_app('org.gnome.Terminal.desktop') is False


# --- app_index integration + thaw-before-launch ordering --------------------------

class TestAppIndexLaunch:
    def test_thaw_before_launch_ordering(self, monkeypatch):
        order = []
        monkeypatch.setattr(
            os, 'kill',
            lambda pid, sig: order.append(('kill', pid, int(sig))))
        manager = _FakeManager([_FakeTop('other.app', activated=True)])
        ctl = _controller(bus=None, manager=manager, force_degraded=True)
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
        info = _FakeDesktopApp(pids=[424243])
        original = info.launch_uris_as_manager

        def _noted(*args, **kwargs):
            order.append(('launch-called',))
            return original(*args, **kwargs)

        info.launch_uris_as_manager = _noted
        from app_index import AppIndex
        ok, error = AppIndex().launch(
            _entry('org.example.App.desktop', info), tracker=ctl)
        assert (ok, error) == (True, None)
        assert order == [
            ('kill', 424242, int(signal.SIGSTOP)),
            ('kill', 424242, int(signal.SIGCONT)),
            ('launch-called',),
        ]
        assert ctl._apps['org.example.App.desktop'].pids == {424242, 424243}

    def test_launch_without_tracker_uses_plain_launch(self):
        info = _FakeDesktopApp(pids=[9])
        from app_index import AppIndex
        ok, error = AppIndex().launch(_entry('a.desktop', info))
        assert (ok, error) == (True, None)
        assert info.launched[0][0] == 'plain'

    def test_launch_error_returns_message(self):
        info = _FakeDesktopApp(fail=GLib.Error('no entry'))
        from app_index import AppIndex
        ok, error = AppIndex().launch(_entry('a.desktop', info),
                                      tracker=object())
        assert ok is False
        assert error == 'no entry'

    def test_broken_tracker_never_breaks_launch(self):
        class _Boom:
            def thaw_for_launch(self, app_id):
                raise RuntimeError('down')

            def track(self, app_id, pid):
                raise RuntimeError('down')

        info = _FakeDesktopApp(pids=[9])
        from app_index import AppIndex
        ok, _error = AppIndex().launch(_entry('a.desktop', info),
                                       tracker=_Boom())
        assert ok is True


# --- freeze/thaw: systemd path ---------------------------------------------------

def _observable_manager(*tops):
    return _FakeManager(list(tops) or [_FakeTop('other.app', activated=True)])


class TestSystemdFreezeThaw:
    def _tracked(self, bus, manager=None, app='org.example.App.desktop',
                 pid=424242, now=0.0):
        ctl = _controller(bus=bus,
                          manager=_observable_manager() if manager is None
                          else manager)
        ctl.track(app, pid, now=now)
        return ctl

    def test_sweep_freezes_idle_app(self, kills):
        bus = _FakeBus()
        ctl = self._tracked(bus)
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
        assert ctl.frozen_apps == ['org.example.App.desktop']
        methods = [c[3] for c in bus.calls]
        assert methods == ['StartTransientUnit', 'FreezeUnit']
        scope = 'app-xxwm-org.example.App.desktop-424242.scope'
        assert bus.calls[1][4].unpack() == (scope,)

    def test_active_app_not_frozen(self, kills):
        bus = _FakeBus()
        manager = _FakeManager([_FakeTop('org.example.App.desktop',
                                         activated=True)])
        ctl = self._tracked(bus, manager=manager)
        assert ctl.sweep(now=601.0) == []
        assert [c[3] for c in bus.calls] == ['StartTransientUnit']

    def test_thaw_before_activate_ordering(self, kills):
        bus = _FakeBus()
        ctl = self._tracked(bus)
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
        ctl.note_activated('org.example.App.desktop', now=601.0)
        assert [c[3] for c in bus.calls] == [
            'StartTransientUnit', 'FreezeUnit', 'ThawUnit']
        assert ctl.frozen_apps == []

    def test_freeze_unit_failure_falls_back_to_signals(self, kills):
        bus = _FakeBus(fail_async=True)
        ctl = self._tracked(bus)
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
        assert (424242, signal.SIGSTOP) in kills

    def test_never_freezes_untracked(self, kills):
        bus = _FakeBus()
        ctl = _controller(bus=bus, manager=_observable_manager())
        assert ctl.sweep(now=10 ** 9) == []
        assert bus.calls == []


# --- degraded SIGSTOP/SIGCONT path --------------------------------------------------

class TestDegradedFreezer:
    def _ctl(self, **kwargs):
        kwargs.setdefault('force_degraded', True)
        kwargs.setdefault('manager', _observable_manager())
        return _controller(bus=_FakeBus(), **kwargs)

    def test_freeze_uses_sigstop_and_thaw_sigcont(self, kills):
        ctl = self._ctl()
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
        assert kills == [(424242, signal.SIGSTOP)]
        ctl.thaw_for_launch('org.example.App.desktop', now=601.0)
        assert kills == [(424242, signal.SIGSTOP),
                         (424242, signal.SIGCONT)]
        assert ctl.frozen_apps == []

    def test_dead_pid_dropped_silently(self, kills, monkeypatch):
        def _kill(pid, sig):
            kills.append((pid, sig))
            raise ProcessLookupError('gone')

        monkeypatch.setattr(os, 'kill', _kill)
        ctl = self._ctl()
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
        rec = ctl._apps['org.example.App.desktop']
        assert rec.pids == set()
        assert rec.stopped == set()

    def test_cont_only_goes_to_stopped_pids(self, kills):
        ctl = self._ctl()
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        ctl.thaw_for_launch('org.example.App.desktop')
        assert kills == []

    def test_no_bus_means_record_pid_only(self, kills):
        ctl = _controller(bus=None, manager=_observable_manager(),
                          force_degraded=False)
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        rec = ctl._apps['org.example.App.desktop']
        assert rec.pids == {424242}
        assert rec.scopes == {}
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
        assert kills == [(424242, signal.SIGSTOP)]


# --- activation observation ----------------------------------------------------------

class TestActivationObservation:
    def test_signal_marks_use_and_thaws(self, kills):
        manager = _FakeManager(with_signal=True)
        ctl = _controller(bus=None, manager=manager, force_degraded=True)
        ctl.track('org.example.App.desktop', 424242, now=600.0)
        assert ctl.sweep(now=601.0) == []
        assert manager._callback is not None
        manager.fire('org.example.App.desktop')
        rec = ctl._apps['org.example.App.desktop']
        assert rec.last_active > 601.0
        ctl.track('org.example.App.desktop', 424243, now=0.0)
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
        manager.fire('org.example.App.desktop')
        assert ctl.frozen_apps == []
        assert (424243, signal.SIGCONT) in kills

    def test_signal_accepts_toplevel_and_kwargs(self):
        manager = _FakeManager(with_signal=True)
        ctl = _controller(bus=None, manager=manager, force_degraded=True)
        assert ctl.sweep(now=100.0) == []
        before = ctl._now()
        manager.fire(_FakeTop('a.desktop', activated=True))
        assert ctl._apps['a.desktop'].last_active >= before
        manager.fire(app_id='b.desktop')
        assert ctl._apps['b.desktop'].last_active >= before

    def test_poll_marks_activated_toplevel(self):
        app = _FakeTop('org.example.App.desktop', activated=True)
        other = _FakeTop('other.app', activated=False)
        ctl = _controller(bus=None, manager=_FakeManager([app, other]),
                          force_degraded=True)
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        assert ctl.sweep(now=601.0) == []
        assert ctl._apps['org.example.App.desktop'].last_active > 601.0

    def test_absent_activation_means_no_freezing(self, kills):
        tops = [_FakeTop('org.example.App.desktop', activated='missing')]
        assert not hasattr(tops[0], 'activated')
        ctl = _controller(bus=None, manager=_FakeManager(tops),
                          force_degraded=True)
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        assert ctl.sweep(now=10 ** 9) == []
        assert kills == []

    def test_no_manager_means_no_freezing(self, kills):
        ctl = _controller(bus=None, force_degraded=True)
        ctl.attach_manager(None)
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        assert ctl.sweep(now=10 ** 9) == []
        assert kills == []


# --- battery saver ---------------------------------------------------------------------

class TestSaverAdjustments:
    def test_halves_minutes_floor_one(self):
        assert saver_effective_minutes(10, True) == 5
        assert saver_effective_minutes(3, True) == 1
        assert saver_effective_minutes(1, True) == 1
        assert saver_effective_minutes(10, False) == 10

    def test_disabled_stays_disabled(self):
        assert saver_effective_minutes(0, True) == 0
        assert saver_effective_minutes(0, False) == 0

    def test_blank_window(self):
        assert saver_blank_seconds(True) == 30
        assert saver_blank_seconds(False) == 120

    def test_saver_freezes_earlier(self, kills):
        cfg = _config(battery_saver=True)
        ctl = _controller(bus=None, manager=_observable_manager(),
                          force_degraded=True, config=cfg)
        assert ctl.saver_effective is True
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        assert ctl.sweep(now=301.0) == ['org.example.App.desktop']

    def test_no_saver_waits_full_timeout(self, kills):
        ctl = _controller(bus=None, manager=_observable_manager(),
                          force_degraded=True)
        assert ctl.saver_effective is False
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        assert ctl.sweep(now=301.0) == []
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']

    def test_saver_blank_freeze_at_thirty_seconds(self, kills):
        cfg = _config(battery_saver=True)
        ctl = _controller(bus=None, manager=_observable_manager(),
                          force_degraded=True, config=cfg)
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        ctl.note_screen_blank(True, now=1000.0)
        assert ctl.sweep(now=1029.0) == []
        assert ctl.sweep(now=1030.0) == ['org.example.App.desktop']

    def test_plain_blank_freeze_at_two_minutes(self, kills):
        ctl = _controller(bus=None, manager=_observable_manager(),
                          force_degraded=True)
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        ctl.note_screen_blank(True, now=1000.0)
        assert ctl.sweep(now=1119.0) == []
        assert ctl.sweep(now=1120.0) == ['org.example.App.desktop']


class TestSaverAuto:
    def _ctl(self, **kwargs):
        kwargs.setdefault('manager', _observable_manager())
        kwargs.setdefault('force_degraded', True)
        kwargs.setdefault('bus', None)
        return _controller(**kwargs)

    def test_auto_on_at_fifteen_percent(self):
        ctl = self._ctl()
        assert ctl.saver_effective is False
        ctl._apply_exemptions(set(), None, set(), (15.0, False))
        ctl.sweep(now=100.0)
        assert ctl.saver_effective is True

    def test_stays_off_above_threshold(self):
        ctl = self._ctl()
        ctl._apply_exemptions(set(), None, set(), (16.0, False))
        ctl.sweep(now=100.0)
        assert ctl.saver_effective is False

    def test_auto_stays_until_charging(self):
        ctl = self._ctl()
        ctl._apply_exemptions(set(), None, set(), (10.0, False))
        ctl.sweep(now=100.0)
        assert ctl.saver_effective is True
        ctl._apply_exemptions(set(), None, set(), (10.0, False))
        ctl.sweep(now=200.0)
        assert ctl.saver_effective is True
        ctl._apply_exemptions(set(), None, set(), (10.0, True))
        ctl.sweep(now=300.0)
        assert ctl.saver_effective is False

    def test_unknown_battery_keeps_state(self):
        ctl = self._ctl()
        ctl._apply_exemptions(set(), None, set(), (None, False))
        ctl.sweep(now=100.0)
        assert ctl.saver_effective is False

    def test_auto_opt_out(self):
        ctl = self._ctl(config=_config(battery_saver_auto=False))
        ctl._apply_exemptions(set(), None, set(), (5.0, False))
        ctl.sweep(now=100.0)
        assert ctl.saver_effective is False

    def test_manual_off_clears_auto(self):
        ctl = self._ctl()
        ctl._apply_exemptions(set(), None, set(), (10.0, False))
        ctl.sweep(now=100.0)
        assert ctl.saver_effective is True
        ctl.set_saver(False)
        assert ctl.saver_effective is False

    def test_manual_toggle_writes_config(self):
        cfg = _config()
        ctl = self._ctl(config=cfg)
        ctl.set_saver(True)
        assert cfg.battery_saver is True
        assert ctl.saver_effective is True


# --- exemption queries (fake buses, tuple replies like the real API) ---------------------

def _mpris_bus(statuses, entries=None):
    entries = entries or {}

    def _on_sync(dest, path, iface, method, params, *rest):
        if method == 'ListNames':
            return _Reply(list(statuses))
        name = dest
        prop = params.unpack()[1]
        if prop == 'PlaybackStatus':
            return _Reply(statuses[name])
        return _Reply(entries.get(name))

    return _FakeBus(on_sync=_on_sync)


class TestMprisQuery:
    def test_playing_maps_desktop_entry(self):
        bus = _mpris_bus(
            {'org.mpris.MediaPlayer2.vlc': 'Playing',
             'org.mpris.MediaPlayer2.fox': 'Paused',
             'org.freedesktop.DBus': 'n/a'},
            {'org.mpris.MediaPlayer2.vlc': 'vlc',
             'org.mpris.MediaPlayer2.fox': 'firefox'})
        assert query_mpris_playing(bus) == {'vlc.desktop'}

    def test_missing_entry_skipped(self):
        bus = _mpris_bus({'org.mpris.MediaPlayer2.strange': 'Playing'}, {})
        assert query_mpris_playing(bus) == set()

    def test_bus_down_means_empty(self):
        assert query_mpris_playing(_FakeBus()) == set()


class TestCallsOwnerQuery:
    def _bus(self, owner=':1.42', pid=4242):
        def _on_sync(dest, path, iface, method, params, *rest):
            if method == 'GetNameOwner':
                if owner is None:
                    raise Exception('not owned')
                assert params.unpack() == (_CALLS_BUS_NAME_VALUE,)
                return _Reply(owner)
            assert params.unpack() == (owner,)
            return _Reply(pid)

        return _FakeBus(on_sync=_on_sync)

    def test_owner_pid_resolved(self):
        assert query_calls_owner_pid(self._bus()) == 4242

    def test_unowned_name_means_none(self):
        assert query_calls_owner_pid(self._bus(owner=None)) is None

    def test_calls_pid_exempts_app_in_sweep(self, kills):
        ctl = _controller(bus=None, manager=_observable_manager(),
                          force_degraded=True)
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        ctl._apply_exemptions(set(), 424242, set(), (None, False))
        assert ctl.sweep(now=10 ** 9) == []
        assert kills == []


_CALLS_BUS_NAME_VALUE = 'org.gnome.Calls'


class TestBatteryQuery:
    def _bus(self, props):
        def _on_sync(dest, path, iface, method, params, *rest):
            assert dest == 'org.freedesktop.UPower'
            assert method == 'GetAll'
            if isinstance(props, Exception):
                raise props
            return _Reply(props)

        return _FakeBus(on_sync=_on_sync)

    def test_discharging_low(self):
        pct, charging = query_battery(self._bus(
            {'IsPresent': True, 'Percentage': 12.0, 'State': 2}))
        assert (pct, charging) == (12.0, False)

    def test_charging(self):
        pct, charging = query_battery(self._bus(
            {'IsPresent': True, 'Percentage': 50.0, 'State': 1}))
        assert (pct, charging) == (50.0, True)

    def test_absent_or_failed_means_unknown(self):
        assert query_battery(self._bus({'IsPresent': False})) == (None, False)
        assert query_battery(
            self._bus(Exception('bus down'))) == (None, False)


class TestPwDumpParsing:
    _PAYLOAD = ('[{"type": "PipeWire:Interface:Node", "info": {"props": '
                '{"application.process.id": 4242, "media.class": '
                '"Stream/Output/Audio"}}}, {"type": '
                '"PipeWire:Interface:Client", "info": {"props": '
                '{"application.process.id": 999}}}, {"type": '
                '"PipeWire:Interface:Node", "info": {"props": '
                '{"media.class": "Stream/Input/Audio"}}}]')

    def test_nodes_match_clients_ignored(self):
        assert parse_pw_dump_pids(self._PAYLOAD) == {4242}

    def test_garbage_means_empty(self):
        assert parse_pw_dump_pids('') == set()
        assert parse_pw_dump_pids('not json') == set()
        assert parse_pw_dump_pids('{"a": 1}') == set()

    def test_audio_pid_exempts_app_in_sweep(self, kills):
        ctl = _controller(bus=None, manager=_observable_manager(),
                          force_degraded=True,
                          pw_dump_runner=lambda: self._PAYLOAD)
        ctl.track('org.example.App.desktop', 4242, now=0.0)
        ctl._audio_pids = parse_pw_dump_pids(ctl._pw_dump())
        assert ctl.sweep(now=10 ** 9) == []
        assert kills == []


# --- saver check gates ----------------------------------------------------------------------

class TestSaverCheckGates:
    def _shell(self):
        checker_calls = []
        weather_calls = []

        class _Checker:
            def maybe_check(self):
                checker_calls.append(True)

        class _Weather:
            def refresh(self, on_done, force=False):
                weather_calls.append(force)

        shell = types.SimpleNamespace(_update_checker=_Checker(),
                                      _weather=_Weather())
        return shell, checker_calls, weather_calls

    def test_checks_paused_under_saver(self):
        shell, checker_calls, weather_calls = self._shell()
        saver = types.SimpleNamespace(saver_effective=True)
        install_saver_check_gates(shell, saver)
        shell._update_checker.maybe_check()
        shell._weather.refresh(lambda text: None)
        assert checker_calls == []
        assert weather_calls == []

    def test_manual_weather_refresh_still_runs(self):
        shell, _checker_calls, weather_calls = self._shell()
        saver = types.SimpleNamespace(saver_effective=True)
        install_saver_check_gates(shell, saver)
        shell._weather.refresh(lambda text: None, force=True)
        assert weather_calls == [True]

    def test_checks_run_without_saver(self):
        shell, checker_calls, weather_calls = self._shell()
        saver = types.SimpleNamespace(saver_effective=False)
        install_saver_check_gates(shell, saver)
        shell._update_checker.maybe_check()
        shell._weather.refresh(lambda text: None)
        assert checker_calls == [True]
        assert weather_calls == [False]

    def test_double_install_wraps_once(self):
        shell, checker_calls, _weather_calls = self._shell()
        saver = types.SimpleNamespace(saver_effective=False)
        install_saver_check_gates(shell, saver)
        install_saver_check_gates(shell, saver)
        shell._update_checker.maybe_check()
        assert checker_calls == [True]

    def test_missing_surfaces_tolerated(self):
        install_saver_check_gates(types.SimpleNamespace(), types.SimpleNamespace(
            saver_effective=True))


# --- notification-action exemption -----------------------------------------------------------------

class TestNotificationAction:
    def _ctl(self):
        return _controller(bus=None, manager=_observable_manager(),
                           force_degraded=True)

    def test_action_thaws_and_exempts(self, kills):
        ctl = self._ctl()
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
        ctl.note_notification_action('org.example.App.desktop', now=601.0)
        assert ctl.frozen_apps == []
        assert (424242, signal.SIGCONT) in kills
        ctl.track('org.example.App.desktop', 424242, now=0.0)
        assert ctl.sweep(now=660.0) == []

    def test_daemon_wrap_maps_action_to_app(self):
        ctl = self._ctl()
        invoked = []
        daemon = types.SimpleNamespace(
            invoke_action=lambda nid, key: invoked.append((nid, key)))
        ctl.attach_daemon(daemon)
        ctl.note_notification_shown(7, 'org.example.App.desktop')
        before = ctl._now()
        daemon.invoke_action(7, 'open')
        assert invoked == [(7, 'open')]
        assert ctl._apps['org.example.App.desktop'].last_notif_action >= \
            before
        ctl.note_notification_closed(7)
        daemon.invoke_action(7, 'open')
        assert invoked == [(7, 'open'), (7, 'open')]

    def test_daemon_wrap_never_breaks_invoke(self):
        ctl = self._ctl()
        daemon = types.SimpleNamespace(
            invoke_action=lambda nid, key: 'done')
        ctl.attach_daemon(daemon)
        assert daemon.invoke_action(99, 'open') == 'done'
        ctl.attach_daemon(None)
        ctl.attach_daemon(types.SimpleNamespace())


# --- config keys ---------------------------------------------------------------------------------------

class TestStandbyConfig:
    def _isolated(self, tmp_path):
        from config import ShellConfig
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        return config

    def test_defaults(self, tmp_path):
        config = self._isolated(tmp_path)
        assert config.app_standby_minutes == 10
        assert config.standby_exempt == []
        assert config.battery_saver is False
        assert config.battery_saver_auto is True

    def test_round_trip_and_reload(self, tmp_path):
        config = self._isolated(tmp_path)
        config.set_app_standby_minutes(3)
        config.set_standby_exempt(['org.example.App.desktop'])
        config.set_battery_saver(True)
        config.set_battery_saver_auto(False)
        reread = self._isolated(tmp_path)
        reread.config_path = config.config_path
        reread.load()
        assert reread.app_standby_minutes == 3
        assert reread.standby_exempt == ['org.example.App.desktop']
        assert reread.battery_saver is True
        assert reread.battery_saver_auto is False

    def test_invalid_values_fall_back(self, tmp_path):
        config = self._isolated(tmp_path)
        config.data['app_standby_minutes'] = 'soon'
        config.data['standby_exempt'] = 'all'
        assert config.app_standby_minutes == 10
        assert config.standby_exempt == []
        config.set_app_standby_minutes(-5)
        assert config.app_standby_minutes == 0


# --- process-wide registry -------------------------------------------------------------------------------

class TestRegistry:
    def test_singleton_and_replacement(self):
        saved = app_standby._default_controller
        try:
            app_standby._default_controller = None
            first = get_standby()
            assert get_standby() is first
            second = configure_standby()
            assert get_standby() is second
            assert second is not first
        finally:
            app_standby._default_controller = saved


# --- restart recovery + D-Bus-activated units (systemd-mirroring fakes) -------


def _unit_tuple(name, active='active', sub='running'):
    """One ListUnitsByPatterns row: a(ssssssouso) — id, description,
    load, active, sub, following, object path, job id, job type, job path."""
    return (name, f'{name} description', 'loaded', active, sub, '',
            f'/org/freedesktop/systemd1/unit/{name.replace(".", "_")}',
            0, '', '/')


class _FakeSystemdBus(_FakeBus):
    """User-manager stand-in mirroring the real systemd D-Bus shapes.

    ListUnitsByPatterns replies are a 1-tuple of the unit array (filtering
    by the requested glob patterns like the real manager); FreezeUnit and
    ThawUnit failures raise GLib.Error carrying the real D-Bus error names
    (org.freedesktop.systemd1.NoSuchUnit / ...UnitInactive).
    """

    def __init__(self, units=(), freeze_errors=None, thaw_errors=None):
        super().__init__()
        self._units = [u if isinstance(u, tuple) else _unit_tuple(u)
                       for u in units]
        self._freeze_errors = dict(freeze_errors or {})
        self._thaw_errors = dict(thaw_errors or {})

    def _filter(self, patterns):
        return [u for u in self._units
                if any(fnmatch.fnmatchcase(u[0], pat) for pat in patterns)]

    def call_sync(self, *args):
        self.sync_calls.append(args)
        method = args[3]
        if method == 'ListUnitsByPatterns':
            _states, patterns = args[4].unpack()
            return _Reply(self._filter(patterns))
        if method in ('FreezeUnit', 'ThawUnit'):
            (name,) = args[4].unpack()
            errors = (self._freeze_errors if method == 'FreezeUnit'
                      else self._thaw_errors)
            if name in errors:
                raise GLib.Error(
                    f'GDBus.Error:org.freedesktop.systemd1.'
                    f'{errors[name]}: Unit {name} {errors[name]}.')
            return _Reply('/org/freedesktop/systemd1/job/1')
        return _Reply(None)

    def call(self, *args):
        assert len(args) == 10, f'bus.call takes 10 args, got {len(args)}'
        self.calls.append(args)
        method = args[3]
        callback = args[-1]
        if method == 'ListUnitsByPatterns':
            _states, patterns = args[4].unpack()
            callback(self, ('listunits', list(patterns)))
        else:
            try:
                name = args[4].unpack()[0]
            except Exception:
                name = ''
            callback(self, ('unit', method, name))

    def call_finish(self, res):
        if isinstance(res, tuple) and res and res[0] == 'listunits':
            return _Reply(self._filter(res[1]))
        if isinstance(res, tuple) and res and res[0] == 'unit':
            _tag, method, name = res
            errors = (self._freeze_errors if method == 'FreezeUnit'
                      else self._thaw_errors)
            if name in errors:
                raise GLib.Error(
                    f'GDBus.Error:org.freedesktop.systemd1.'
                    f'{errors[name]}: Unit {name} {errors[name]}.')
            return None
        return super().call_finish(res)


def _systemd_controller(bus, clock_now=0.0, stopped_file=None, **kwargs):
    now = [float(clock_now)]
    kwargs.setdefault('config', _config())
    kwargs.setdefault('session_bus_factory', lambda: bus)
    kwargs.setdefault('system_bus_factory', lambda: None)
    kwargs.setdefault('manager', _observable_manager())
    kwargs.setdefault('clock', lambda: now[0])
    ctl = StandbyController(stopped_file=stopped_file, **kwargs)
    return ctl, now


class TestParseStandbyScope:
    def test_round_trip(self):
        name = scope_name_for('org.gnome.Evolution.desktop', 1234)
        assert parse_standby_scope(name) == \
            ('org.gnome.Evolution.desktop', 1234)

    def test_foreign_units_rejected(self):
        assert parse_standby_scope(
            'dbus-:1.42-org.gnome.Evolution@0.service') == (None, None)
        assert parse_standby_scope('app-gnome-org.foo.Bar-7.scope') == \
            (None, None)
        assert parse_standby_scope('') == (None, None)
        assert parse_standby_scope(None) == (None, None)

    def test_bad_pid_still_names_app(self):
        assert parse_standby_scope('app-xxwm-foo-noid.scope') == \
            ('foo', None)
        assert parse_standby_scope('app-xxwm-foo-0.scope') == ('foo', None)


class TestDbusPatterns:
    def test_bus_name_strips_desktop_case_preserved(self):
        assert dbus_bus_name_for_app('org.gnome.Evolution.desktop') == \
            'org.gnome.Evolution'
        assert dbus_bus_name_for_app('Org.GNOME.Calls.DESKTOP') == \
            'Org.GNOME.Calls'
        assert dbus_bus_name_for_app('org.gnome.Calls') == 'org.gnome.Calls'

    def test_patterns_cover_broker_classic_and_scope(self):
        patterns = unit_patterns_for_app('org.gnome.Evolution.desktop')
        assert 'dbus-*-org.gnome.Evolution@*.service' in patterns
        assert 'dbus-org.gnome.Evolution.service' in patterns
        assert 'dbus-org.gnome.Evolution@*.service' in patterns
        assert 'app-*-org.gnome.Evolution-*.scope' in patterns
        assert 'app-*-org.gnome.Evolution.desktop-*.scope' in patterns

    def test_no_desktop_means_no_extra_scope_pattern(self):
        patterns = unit_patterns_for_app('org.gnome.Calls')
        assert len(patterns) == 4
        assert len(set(patterns)) == len(patterns)


class TestSelectAppUnits:
    _UNITS = [
        _unit_tuple('dbus-:1.42-org.gnome.Evolution@0.service'),
        _unit_tuple('dbus-org.gnome.Evolution.service'),
        _unit_tuple('dbus-org.gnome.Evolution@0.service'),
        _unit_tuple('app-gnome-org.gnome.Evolution-1234.scope'),
        _unit_tuple('app-gnome-org.gnome.Evolution.desktop-7.scope'),
        _unit_tuple('dbus-:1.7-org.gnome.EvolutionHelper@0.service'),
        _unit_tuple('dbus-org.gnome.EvolutionHelper.service'),
        _unit_tuple('app-gnome-org.gnome.EvolutionHelper-5.scope'),
        _unit_tuple('dbus-:1.9-org.gnome.Calendar@0.service'),
        _unit_tuple('dbus-org.freedesktop.NetworkManager.service'),
        _unit_tuple('app-xxwm-org.gnome.Evolution.desktop-4242.scope'),
        _unit_tuple('suspend.target', sub='active'),
    ]

    def test_selects_only_exact_bus_matches(self):
        matched = select_app_units(self._UNITS, 'org.gnome.Evolution.desktop')
        assert matched == [
            'dbus-:1.42-org.gnome.Evolution@0.service',
            'dbus-org.gnome.Evolution.service',
            'dbus-org.gnome.Evolution@0.service',
            'app-gnome-org.gnome.Evolution-1234.scope',
            'app-gnome-org.gnome.Evolution.desktop-7.scope',
        ]

    def test_accepts_plain_names(self):
        names = [u[0] for u in self._UNITS]
        assert select_app_units(names, 'org.gnome.Evolution.desktop') == \
            select_app_units(self._UNITS, 'org.gnome.Evolution.desktop')

    def test_similar_prefix_never_matches(self):
        assert select_app_units(self._UNITS, 'org.gnome.Evo.desktop') == []
        helper = select_app_units(self._UNITS, 'org.gnome.EvolutionHelper')
        assert helper == [
            'dbus-:1.7-org.gnome.EvolutionHelper@0.service',
            'dbus-org.gnome.EvolutionHelper.service',
            'app-gnome-org.gnome.EvolutionHelper-5.scope',
        ]


class TestStartReadopt:
    def test_start_lists_thaws_and_readopts(self, tmp_path):
        scope = scope_name_for('org.example.App.desktop', 4242)
        bus = _FakeSystemdBus(units=[scope, 'suspend.target'])
        ctl, _now = _systemd_controller(
            bus, stopped_file=str(tmp_path / 'stopped.json'))
        ctl.start()
        try:
            assert ctl._apps['org.example.App.desktop'].pids == {4242}
            rec = ctl._apps['org.example.App.desktop']
            assert rec.scopes == {4242: scope}
            assert rec.frozen is False
        finally:
            ctl.stop()
        dest, path, iface, method, params = bus.calls[0][:5]
        assert method == 'ListUnitsByPatterns'
        assert params.unpack() == ([], ['app-xxwm-*.scope'])
        thawed = [c[4].unpack()[0] for c in bus.calls if c[3] == 'ThawUnit']
        assert thawed == [scope]

    def test_start_without_bus_is_safe(self, tmp_path):
        ctl, _now = _systemd_controller(
            None, stopped_file=str(tmp_path / 'stopped.json'))
        ctl.start()
        try:
            assert ctl._apps == {}
        finally:
            ctl.stop()

    def test_readopt_thaw_errors_are_harmless(self, tmp_path):
        scope = scope_name_for('org.example.App.desktop', 4242)
        bus = _FakeSystemdBus(units=[scope],
                              thaw_errors={scope: 'NoSuchUnit'})
        ctl, _now = _systemd_controller(
            bus, stopped_file=str(tmp_path / 'stopped.json'))
        ctl.start()
        try:
            assert ctl._apps['org.example.App.desktop'].pids == {4242}
        finally:
            ctl.stop()


class TestStoppedLedger:
    def test_record_restore_and_clear(self, tmp_path, monkeypatch):
        ledger = str(tmp_path / 'stopped.json')
        pid = os.getpid()
        starttime = proc_starttime(pid)
        assert starttime is not None
        conted = []

        def _kill(kill_pid, sig):
            conted.append((kill_pid, int(sig)))

        monkeypatch.setattr(os, 'kill', _kill)
        ctl, _now = _systemd_controller(
            _FakeSystemdBus(), stopped_file=ledger, force_degraded=True)
        ctl.track('org.example.App.desktop', pid, now=0.0)
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
        assert (pid, int(signal.SIGSTOP)) in conted
        with open(ledger, encoding='utf-8') as handle:
            entries = json.load(handle)
        assert {'pid': pid, 'starttime': starttime} in entries

        conted.clear()
        ctl2, _now2 = _systemd_controller(
            _FakeSystemdBus(), stopped_file=ledger, force_degraded=True)
        assert ctl2._restore_stopped_file() == [pid]
        assert conted == [(pid, int(signal.SIGCONT))]
        assert not os.path.exists(ledger)

    def test_recycled_pid_never_signalled(self, tmp_path, monkeypatch):
        ledger = str(tmp_path / 'stopped.json')
        pid = os.getpid()
        starttime = proc_starttime(pid)
        with open(ledger, 'w', encoding='utf-8') as handle:
            json.dump([{'pid': pid, 'starttime': starttime + 99999}], handle)
        conted = []
        monkeypatch.setattr(os, 'kill',
                            lambda kill_pid, sig: conted.append(kill_pid))
        ctl, _now = _systemd_controller(
            _FakeSystemdBus(), stopped_file=ledger, force_degraded=True)
        assert ctl._restore_stopped_file() == []
        assert conted == []
        assert not os.path.exists(ledger)

    def test_missing_pid_silently_skipped(self, tmp_path):
        ledger = str(tmp_path / 'stopped.json')
        with open(ledger, 'w', encoding='utf-8') as handle:
            json.dump([{'pid': 2 ** 30, 'starttime': 1}], handle)
        ctl, _now = _systemd_controller(
            _FakeSystemdBus(), stopped_file=ledger, force_degraded=True)
        assert ctl._restore_stopped_file() == []
        assert not os.path.exists(ledger)

    def test_stopped_path_uses_runtime_dir(self, monkeypatch):
        monkeypatch.setenv('XDG_RUNTIME_DIR', '/run/user/1000')
        assert stopped_file_path() == \
            '/run/user/1000/xx-wm/standby-stopped.json'


class TestShutdownThaw:
    def test_shutdown_thaws_scopes_and_conts(self, tmp_path, monkeypatch):
        scope = scope_name_for('org.example.App.desktop', 4242)
        bus = _FakeSystemdBus(units=[scope])
        ledger = str(tmp_path / 'stopped.json')
        ctl, now = _systemd_controller(bus, stopped_file=ledger)
        ctl.track('org.example.App.desktop', 4242, now=0.0)
        now[0] = 601.0
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
        assert ctl.frozen_apps == ['org.example.App.desktop']
        conted = []
        monkeypatch.setattr(os, 'kill',
                            lambda pid, sig: conted.append((pid, int(sig))))
        ctl._apps['org.example.App.desktop'].stopped.add(999999)
        ctl.shutdown_thaw()
        thawed = [c[4].unpack()[0] for c in bus.sync_calls
                  if c[3] == 'ThawUnit']
        assert thawed == [scope]
        assert ctl.frozen_apps == []
        assert not os.path.exists(ledger)

    def test_shutdown_tolerates_dbus_errors(self, tmp_path):
        scope = scope_name_for('org.example.App.desktop', 4242)
        bus = _FakeSystemdBus(units=[scope],
                              thaw_errors={scope: 'UnitInactive'})
        ctl, now = _systemd_controller(bus, stopped_file=str(tmp_path / 's'))
        ctl.track('org.example.App.desktop', 4242, now=0.0)
        now[0] = 601.0
        ctl.sweep(now=601.0)
        ctl.shutdown_thaw()
        assert ctl.frozen_apps == []

    def test_main_quit_path_thaws(self):
        text = (Path(__file__).parent.parent / 'launcher' / 'src'
                / 'main.py').read_text(encoding='utf-8')
        assert 'def do_shutdown' in text
        assert 'shutdown_thaw' in text


class TestDbusActivatedFreeze:
    _UNITS = [
        _unit_tuple('dbus-:1.42-org.gnome.Evolution@0.service'),
        _unit_tuple('dbus-org.gnome.EvolutionHelper.service'),
        _unit_tuple('app-gnome-org.gnome.Evolution-1234.scope'),
        _unit_tuple('app-gnome-org.gnome.EvolutionHelper-5.scope'),
    ]

    def test_pidless_app_freezes_its_unit_only(self, tmp_path):
        bus = _FakeSystemdBus(units=self._UNITS)
        ctl, now = _systemd_controller(
            bus, stopped_file=str(tmp_path / 'stopped.json'))
        ctl.note_activated('org.gnome.Evolution.desktop', now=0.0)
        now[0] = 601.0
        assert ctl.sweep(now=601.0) == ['org.gnome.Evolution.desktop']
        frozen = sorted(c[4].unpack()[0] for c in bus.calls
                        if c[3] == 'FreezeUnit')
        assert frozen == [
            'app-gnome-org.gnome.Evolution-1234.scope',
            'dbus-:1.42-org.gnome.Evolution@0.service',
        ]
        assert ctl._apps['org.gnome.Evolution.desktop'].dbus_units == \
            set(frozen)

    def test_pidless_thaw_thaws_units(self, tmp_path):
        bus = _FakeSystemdBus(units=self._UNITS)
        ctl, now = _systemd_controller(
            bus, stopped_file=str(tmp_path / 'stopped.json'))
        ctl.note_activated('org.gnome.Evolution.desktop', now=0.0)
        now[0] = 601.0
        ctl.sweep(now=601.0)
        bus.calls.clear()
        ctl.thaw_for_launch('org.gnome.Evolution.desktop', now=601.0)
        thawed = sorted(c[4].unpack()[0] for c in bus.calls
                        if c[3] == 'ThawUnit')
        assert thawed == [
            'app-gnome-org.gnome.Evolution-1234.scope',
            'dbus-:1.42-org.gnome.Evolution@0.service',
        ]
        assert ctl.frozen_apps == []

    def test_unresolvable_pidless_app_never_reports_frozen(self, tmp_path):
        bus = _FakeSystemdBus(units=[])
        ctl, now = _systemd_controller(
            bus, stopped_file=str(tmp_path / 'stopped.json'))
        ctl.note_activated('org.unknown.App.desktop', now=0.0)
        now[0] = 601.0
        assert ctl.sweep(now=601.0) == []
        assert [c[3] for c in bus.sync_calls] == ['ListUnitsByPatterns']
        assert bus.calls == []


class TestThawBeforeActivateOrdering:
    def _ordered(self, ctl, bus, manager):
        order = []
        orig_call = bus.call

        def _call(*args):
            if args[3] == 'ThawUnit':
                order.append(('thaw', args[4].unpack()[0]))
            return orig_call(*args)

        bus.call = _call
        orig_activate = manager.activate

        def _activate(handle):
            order.append(('activate', handle))
            return orig_activate(handle)

        manager.activate = _activate
        return order

    def test_switcher_thaw_before_manager_activate(self, tmp_path):
        bus = _FakeSystemdBus()
        ctl, now = _systemd_controller(
            bus, stopped_file=str(tmp_path / 'stopped.json'))
        manager = ctl._manager
        ctl.track('org.example.App.desktop', 4242, now=0.0)
        now[0] = 601.0
        assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
        bus.calls.clear()
        order = self._ordered(ctl, bus, manager)
        handed = []
        ctl.activate_with_thaw(manager, 'org.example.App.desktop', 'h1',
                               on_activated=lambda: handed.append(True))
        scope = scope_name_for('org.example.App.desktop', 4242)
        assert order == [('thaw', scope), ('activate', 'h1')]
        assert handed == [True]
        assert ctl.frozen_apps == []

    def test_readopted_scope_thaw_before_manager_activate(self, tmp_path):
        scope = scope_name_for('org.example.App.desktop', 4242)
        bus = _FakeSystemdBus(units=[scope])
        ctl, now = _systemd_controller(
            bus, stopped_file=str(tmp_path / 'stopped.json'))
        manager = ctl._manager
        ctl.start()
        try:
            assert ctl._apps['org.example.App.desktop'].scopes == \
                {4242: scope}
            now[0] = 601.0
            assert ctl.sweep(now=601.0) == ['org.example.App.desktop']
            bus.calls.clear()
            order = self._ordered(ctl, bus, manager)
            ctl.activate_with_thaw(manager, 'org.example.App.desktop',
                                   'h1')
            assert order == [('thaw', scope), ('activate', 'h1')]
            assert ctl.frozen_apps == []
        finally:
            ctl.stop()



