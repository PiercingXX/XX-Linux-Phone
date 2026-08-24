"""Power long-press contract + fingerprint node routing (headless).

The key-up handler must blank only when the press was short: after a fired
long-press (power menu shown) the release must do nothing, or the menu
appears and the screen immediately goes dark. These tests drive
DisplayManager._on_event directly with fake GLib (PyGObject is absent here);
idle_add invokes callbacks synchronously so on_* wiring is observable.
"""
import sys
import types

from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import pytest


class _SyncGLib:
    """Synchronous GLib seam: idle_add runs inline; timers are inert ids."""

    SOURCE_REMOVE = False
    SOURCE_CONTINUE = True

    @staticmethod
    def idle_add(fn, *a):
        fn(*a)
        return 0

    @staticmethod
    def timeout_add(_ms, _cb, *_a):
        return 1

    timeout_add_seconds = timeout_add

    @staticmethod
    def source_remove(_tid):
        None


def _install_fake_glib() -> None:
    """Make `from gi.repository import GLib` resolve to a stub."""
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.idle_add = lambda fn, *a, **k: (fn(*a), 0)[1]

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib


_install_fake_glib()

import display_manager  # noqa: E402  (needs fake GLib installed first)


@pytest.fixture
def dm(monkeypatch):
    """Build DisplayManager with no input threads and stubbed display
    backends; _blank/_wake record invocations instead of touching
    brightnessctl/wlopm. The fingerprint node is pinned to event9."""
    # Which fake gi wins collection decides what display_manager bound as
    # GLib at import time, so pin the seam: idle_add synchronous, timers
    # inert — the tests drive _on_event/_on_power_long_press directly.
    monkeypatch.setattr(display_manager, 'GLib', _SyncGLib)
    monkeypatch.setattr(display_manager, '_all_event_devices', lambda: [])
    monkeypatch.setattr(display_manager, '_detect_fp_node',
                        lambda: '/dev/input/event9')
    blanks: list[bool] = []
    wakes: list[bool] = []
    monkeypatch.setattr(display_manager.DisplayManager, '_blank',
                        lambda self: blanks.append(True))
    monkeypatch.setattr(display_manager.DisplayManager, '_wake',
                        lambda self: wakes.append(True))

    def _build(**kwargs):
        return display_manager.DisplayManager(**kwargs), blanks, wakes

    return _build


def _power(value: int):
    return ('/dev/input/eventX', display_manager.EV_KEY,
            display_manager.KEY_POWER, value)


class TestLongPressContract:
    def test_fired_long_press_suppresses_key_up_blank(self, dm):
        menus: list[bool] = []
        mgr, blanks, wakes = dm(on_power_menu=lambda: menus.append(True))
        mgr._on_event(*_power(1))
        mgr._on_power_long_press()
        assert menus == [True]
        mgr._on_event(*_power(0))
        assert blanks == []
        assert wakes == []

    def test_cancelled_pending_behaves_as_short_press(self, dm):
        mgr, blanks, wakes = dm()
        mgr._on_event(*_power(1))
        mgr._on_event(*_power(0))
        assert len(blanks) == 1
        assert wakes == []

    def test_plain_key_up_without_press_unchanged(self, dm):
        mgr, blanks, wakes = dm()
        mgr._on_event(*_power(0))
        assert len(blanks) == 1
        assert wakes == []

    def test_next_press_after_long_press_blanks_again(self, dm):
        mgr, blanks, _wakes = dm()
        mgr._on_event(*_power(1))
        mgr._on_power_long_press()
        mgr._on_event(*_power(0))
        mgr._on_event(*_power(1))
        mgr._on_event(*_power(0))
        assert len(blanks) == 1


class TestFingerprintNodeRouting:
    def test_key_on_detected_node_triggers_auth(self, dm):
        auths: list[bool] = []
        mgr, _blanks, _wakes = dm(on_fingerprint=lambda: auths.append(True))
        mgr._on_event('/dev/input/event9', display_manager.EV_KEY, 96, 1)
        assert auths == [True]

    def test_key_on_other_node_does_not_trigger_auth(self, dm):
        auths: list[bool] = []
        mgr, _blanks, _wakes = dm(on_fingerprint=lambda: auths.append(True))
        mgr._on_event('/dev/input/event3', display_manager.EV_KEY, 96, 1)
        assert auths == []

    def test_key_on_detected_node_wakes_blanked_screen(self, dm):
        mgr, blanks, wakes = dm()
        mgr._blanked = True
        mgr._on_event('/dev/input/event9', display_manager.EV_KEY, 96, 1)
        assert wakes == [True]
        assert blanks == []
