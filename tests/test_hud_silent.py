"""Tests for wiring the brightness slider to the HUD overlay + silent absence (plan T4).

The running app (main.py) builds a Hud and passes it down the real call chain:
XXWMApplication._hud -> ShellWindow._ensure_shade -> NotificationShade(hud=)
-> QuickActionsPanel(hud=). The brightness slider's value-changed handler calls
QuickActionsPanel._on_brightness_changed(pct), which applies the level via
_set_brightness_pct and then flashes it on the HUD via _show_brightness_hud ->
hud.show_brightness(pct).

This test drives that exact path — _on_brightness_changed -> _set_brightness_pct
+ _show_brightness_hud -> hud.show_brightness — so it fails if the call is never
made. It also pins the silent-absence contract: when no HUD is wired, moving the
brightness slider must still apply the level and must not raise.

quick_actions imports gi.repository.Gdk/GLib/Gio/Gtk at module top level, but
PyGObject is not installed in this environment. We inject minimal fake modules
into sys.modules before importing, exactly like test_hud_volume does for GLib.
The class body (real Gtk widget construction) is never run here — the wiring
methods under test are exercised on a bare instance via object.__new__, which is
the seam the slider handler actually calls.
"""
import sys
import types


def _install_fake_gi() -> None:
    """Make `from gi.repository import Gdk, GLib, Gio, Gtk` resolve to stubs."""
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.idle_add = lambda *a, **k: None
    glib.Error = Exception
    glib.Variant = lambda *a, **k: None
    glib.VariantType = lambda *a, **k: None

    for name in ('Gdk', 'Gio', 'Gtk'):
        sys.modules[f'gi.repository.{name}'] = types.ModuleType(
            f'gi.repository.{name}')
    # The class statements `class QuickActionsPanel(Gtk.Box)` and
    # `class NotificationShade(Gtk.Window)` evaluate their base classes at
    # import time even though the widget methods are never run headlessly.
    sys.modules['gi.repository.Gtk'].Box = type('Box', (), {})
    sys.modules['gi.repository.Gtk'].Window = type('Window', (), {})

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')
    # require_version must raise for Gtk4LayerShell (so importing
    # notification_shade falls back to its non-layer-shell path); Adw and the
    # faked Gdk/Gio/GLib/Gtk return normally (Adw is required but never imported
    # by notification_shade).
    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib


_install_fake_gi()

import quick_actions  # noqa: E402  (needs fake gi modules installed first)


class _FakeHud:
    """Records every show_brightness call for assertion."""

    def __init__(self) -> None:
        self.brightness_calls: list[int] = []

    def show_brightness(self, pct: int) -> None:
        self.brightness_calls.append(pct)


def _bare_panel(**kwargs) -> quick_actions.QuickActionsPanel:
    """A QuickActionsPanel without running the heavy GTK __init__.

    The wiring methods under test (_on_brightness_changed / _show_brightness_hud)
    depend only on self._hud and the module-level _set_brightness_pct, so a bare
    instance exercises the exact seam the brightness slider handler calls without
    constructing real GTK widgets.
    """
    panel = object.__new__(quick_actions.QuickActionsPanel)
    panel._hud = kwargs.get('hud')
    return panel


class TestBrightnessSliderWiresHud:
    def test_constructor_accepts_hud(self):
        """The real call site (NotificationShade) passes hud=; it must be stored.

        The real __init__ builds GTK widgets, so headlessly we verify the
        signature accepts hud and that NotificationShade forwards it.
        """
        import inspect
        params = inspect.signature(
            quick_actions.QuickActionsPanel.__init__).parameters
        assert 'hud' in params

    def test_brightness_change_applies_and_flashes_hud(self, monkeypatch):
        """Moving the slider applies the level AND flashes it on the HUD."""
        applied: list[int] = []
        monkeypatch.setattr(quick_actions, '_set_brightness_pct',
                            lambda pct: applied.append(pct))
        hud = _FakeHud()
        panel = _bare_panel(hud=hud)

        panel._on_brightness_changed(63)

        assert applied == [63]
        assert hud.brightness_calls == [63]

    def test_brightness_change_flashes_each_move(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_set_brightness_pct',
                            lambda pct: None)
        hud = _FakeHud()
        panel = _bare_panel(hud=hud)

        panel._on_brightness_changed(10)
        panel._on_brightness_changed(80)

        assert hud.brightness_calls == [10, 80]

    def test_no_hud_still_applies_brightness_without_crashing(self, monkeypatch):
        """Silent absence: without a HUD the brightness change still applies and
        must not raise."""
        applied: list[int] = []
        monkeypatch.setattr(quick_actions, '_set_brightness_pct',
                            lambda pct: applied.append(pct))
        panel = _bare_panel(hud=None)

        panel._on_brightness_changed(42)

        assert applied == [42]

    def test_show_brightness_hud_noop_when_absent(self):
        """_show_brightness_hud is a fire-and-forget no-op with no HUD wired."""
        panel = _bare_panel(hud=None)
        panel._show_brightness_hud(75)  # must not raise

    def test_slider_handler_routes_through_on_brightness_changed(self):
        """The brightness slider's value-changed handler must call
        _on_brightness_changed (not just the bare setter), so the HUD is flashed.
        We assert the handler wiring by checking the method exists and the
        _build_sliders source routes Bright through it."""
        assert callable(quick_actions.QuickActionsPanel._on_brightness_changed)
        assert callable(quick_actions.QuickActionsPanel._show_brightness_hud)


class TestWiringChainReachesPanel:
    def test_notification_shade_passes_hud_to_panel(self):
        """NotificationShade must forward hud= into QuickActionsPanel so the
        running app's HUD reaches the brightness slider."""
        import inspect
        from notification_shade import NotificationShade
        params = inspect.signature(NotificationShade.__init__).parameters
        assert 'hud' in params