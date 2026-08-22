"""Tests for the in-shell HUD module (plan T2).

The HUD is a GTK4 layer-shell overlay that flashes a volume/brightness level
and auto-hides after ~1s. It must degrade silently when PyGObject or the
layer-shell compositor seam is absent, so this module is importable and every
public method no-ops headlessly. The tests below pin that headless contract
deterministically — they run in this environment where PyGObject is not
installed, and they also hold on a GTK-capable host.
"""
from hud import Hud, _HAS_GTK, _HUD_HOLD_MS


class TestHudApi:
    def test_module_imports_headlessly(self):
        """The module must import even without PyGObject (silent absence)."""
        assert callable(Hud)

    def test_hold_contract(self):
        """The overlay auto-hides after ~1s."""
        assert _HUD_HOLD_MS == 1000

    def test_constructs_without_raising(self):
        Hud()

    def test_public_methods_noop_without_raising(self):
        hud = Hud()
        hud.show_volume(42)
        hud.show_brightness(17)
        hud.set_application(app=None)

    def test_silent_absence_when_no_gtk(self):
        """Without PyGObject the HUD keeps no window and every call is a no-op."""
        if not _HAS_GTK:
            hud = Hud()
            assert hud._window is None
            # no-op must not raise and must not create a window
            hud.show_volume(100)
            assert hud._window is None

    def test_api_surface(self):
        """The call sites in T3/T4 depend on these three methods."""
        for name in ('show_volume', 'show_brightness', 'set_application'):
            assert callable(getattr(Hud, name)), f'Hud.{name} missing'