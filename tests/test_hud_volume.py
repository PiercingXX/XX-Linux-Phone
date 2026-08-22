"""Tests for wiring the volume keys to the HUD overlay (plan T3).

The running app (main.py) constructs a Hud and passes it to DisplayManager;
when a volume key event arrives, DisplayManager must adjust the sink volume and
then flash the current level on the HUD via hud.show_volume(pct). This test
drives that exact path — DisplayManager._on_event -> hud.show_volume — so it
fails if the call is never made.

display_manager imports `gi.repository.GLib` at module top level, but PyGObject
is not installed in this environment. We inject a minimal fake GLib into
sys.modules before importing, so the wiring logic is exercised headlessly
without GTK. The HUD itself degrades to a no-op without GTK (see hud.py), so
the production path is: volume key -> _set_volume -> _show_volume_hud -> hud.
"""
import sys
import types

import pytest


def _install_fake_glib() -> None:
    """Make `from gi.repository import GLib` resolve to a stub."""
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.idle_add = lambda *a, **k: None

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib


_install_fake_glib()

import display_manager  # noqa: E402  (needs fake GLib installed first)


class _FakeHud:
    """Records every show_volume/show_brightness call for assertion."""

    def __init__(self) -> None:
        self.volume_calls: list[int] = []
        self.brightness_calls: list[int] = []

    def show_volume(self, pct: int) -> None:
        self.volume_calls.append(pct)

    def show_brightness(self, pct: int) -> None:
        self.brightness_calls.append(pct)


@pytest.fixture
def dm(monkeypatch):
    """A DisplayManager with no input devices (no threads), no real pactl, and
    a deterministic volume level."""
    monkeypatch.setattr(display_manager, '_all_event_devices', lambda: [])
    monkeypatch.setattr(display_manager, '_set_volume', lambda *a: None)
    monkeypatch.setattr(display_manager, '_get_volume_pct', lambda: 37)
    return display_manager.DisplayManager


def _vol_event(code: int, value: int = 1):
    return ('/dev/input/eventX', display_manager.EV_KEY, code, value)


class TestVolumeKeyWiresHud:
    def test_constructor_accepts_hud(self, dm):
        """The real call site passes hud=; the constructor must take it."""
        mgr = dm(hud=_FakeHud())
        assert mgr._hud is not None

    def test_volume_up_flashes_hud(self, dm):
        hud = _FakeHud()
        mgr = dm(hud=hud)
        mgr._on_event(*_vol_event(display_manager.KEY_VOLUMEUP))
        assert hud.volume_calls == [37]

    def test_volume_down_flashes_hud(self, dm):
        hud = _FakeHud()
        mgr = dm(hud=hud)
        mgr._on_event(*_vol_event(display_manager.KEY_VOLUMEDOWN))
        assert hud.volume_calls == [37]

    def test_volume_up_and_down_both_flash(self, dm):
        hud = _FakeHud()
        mgr = dm(hud=hud)
        mgr._on_event(*_vol_event(display_manager.KEY_VOLUMEUP))
        mgr._on_event(*_vol_event(display_manager.KEY_VOLUMEDOWN))
        assert hud.volume_calls == [37, 37]

    def test_no_hud_still_adjusts_volume_without_crashing(self, dm):
        """Silent absence: without a HUD the volume key must not raise."""
        mgr = dm(hud=None)
        mgr._on_event(*_vol_event(display_manager.KEY_VOLUMEUP))
        mgr._on_event(*_vol_event(display_manager.KEY_VOLUMEDOWN))

    def test_key_up_does_not_flash(self, dm):
        """Only the key-down edge flashes the HUD, not the release."""
        hud = _FakeHud()
        mgr = dm(hud=hud)
        mgr._on_event(*_vol_event(display_manager.KEY_VOLUMEUP, value=0))
        assert hud.volume_calls == []