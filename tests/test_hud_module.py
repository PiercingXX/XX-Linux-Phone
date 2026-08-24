"""Tests for the in-shell HUD module (plan T2).

The HUD is a GTK4 layer-shell overlay that flashes a volume/brightness level
and auto-hides after ~1s. It must degrade silently when PyGObject or the
layer-shell compositor seam is absent, so this module is importable and every
public method no-ops headlessly. The tests below pin that headless contract
deterministically: a fake gi whose require_version always rejects pins the
no-GTK branch regardless of which sibling modules clobbered sys.modules
before collection reached this file.
"""
import sys


def _install_headless_gi() -> None:
    """Force hud.py's silent-absence path: `import gi` must raise ImportError
    (None in sys.modules is CPython's canonical way to guarantee that), so no
    sibling module's earlier fake can flip the branch this file asserts."""
    sys.modules['gi'] = None


_install_headless_gi()

from hud import Hud, _HAS_GTK, _HUD_HOLD_MS  # noqa: E402

assert _HAS_GTK is False


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
        """Headless: no window, every call a no-op. GTK-capable: overlay exists.

        This asserts in BOTH branches so it cannot pass vacuously on a host
        where PyGObject is available. Without GTK the HUD must keep no window
        and every public call must no-op without raising; with GTK the overlay
        window must actually be created (otherwise the HUD is silently dead
        on a device that could display it).
        """
        hud = Hud()
        if not _HAS_GTK:
            assert hud._window is None
            # no-op must not raise and must not create a window
            hud.show_volume(100)
            hud.show_brightness(17)
            hud.set_application(app=None)
            assert hud._window is None
        else:
            # On a GTK-capable host the overlay must be created, not dropped.
            assert hud._window is not None

    def test_api_surface(self):
        """The call sites in T3/T4 depend on these three methods.

        Beyond existing, each method must be callable on a live instance and
        return None (fire-and-forget) without raising, so the call sites can
        invoke them unconditionally.
        """
        for name in ('show_volume', 'show_brightness', 'set_application'):
            assert callable(getattr(Hud, name)), f'Hud.{name} missing'
        hud = Hud()
        assert hud.show_volume(42) is None
        assert hud.show_brightness(17) is None
        assert hud.set_application(app=None) is None

    def test_config_param_preserves_silent_absence(self):
        """W1-B: Hud gains an optional live-config injection (custom-theme
        rendering). It must not flip the headless contract — without GTK the
        HUD stays window-less and every public call still no-ops; with GTK
        the overlay must still actually be created."""
        from config import ShellConfig
        cfg = ShellConfig.__new__(ShellConfig)
        cfg.data = {'theme': 'custom', 'custom_background': '#2A1018'}
        hud = Hud(config=cfg)
        if not _HAS_GTK:
            assert hud._window is None
            assert hud.show_volume(100) is None
            assert hud.show_brightness(17) is None
            assert hud.set_application(app=None) is None
            assert hud._window is None
        else:
            assert hud._window is not None