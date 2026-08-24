"""WAYLAND_DISPLAY handling: children spawned by the shell (wlopm/grim,
waydroid) must target the session the shell actually runs on, not a
hardcoded wayland-0. The env maps are built at module import time, so each
check boots a fresh interpreter with a pre-stubbed gi (no PyGObject needed)
and reads the resulting dict."""
import os
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).parent.parent / 'launcher' / 'src'


def _bootstrap_script(mod: str, attr: str) -> str:
    # Stub gi before the module import so this passes on PyGObject-less
    # boxes; only attribute presence matters, nothing GTK is constructed.
    return '\n'.join([
        'import sys, types',
        "_gi = types.ModuleType('gi')",
        "_repo = types.ModuleType('gi.repository')",
        "for name in ('GLib', 'Gtk', 'Gdk'):",
        "    setattr(_repo, name, types.ModuleType('gi.repository.' + name))",
        # Widget base classes are referenced at class-definition time
        # (class HomeLauncher(Gtk.Box)); fabricate any that are touched.
        "_repo.Gtk.__getattr__ = lambda name: type(name, (), {})",
        '_gi.require_version = lambda *a, **k: None',
        '_gi.repository = _repo',
        "sys.modules['gi'] = _gi",
        "sys.modules['gi.repository'] = _repo",
        "for name in ('GLib', 'Gtk', 'Gdk'):",
        "    sys.modules['gi.repository.' + name] = getattr(_repo, name)",
        f'sys.path.insert(0, {str(_SRC)!r})',
        f'import {mod}',
        f'print({mod}.{attr}[{"'WAYLAND_DISPLAY'"}])',
    ])


def _wayland_display_for(mod: str, attr: str, wayland_display: str | None) -> str:
    env = dict(os.environ)
    if wayland_display is None:
        env.pop('WAYLAND_DISPLAY', None)
    else:
        env['WAYLAND_DISPLAY'] = wayland_display
    proc = subprocess.run(
        [sys.executable, '-c', _bootstrap_script(mod, attr)],
        env=env, capture_output=True, text=True, timeout=60, check=True,
    )
    return proc.stdout.strip()


class TestWaylandDisplayEnv:
    def test_display_manager_prefers_session_display(self):
        assert _wayland_display_for(
            'display_manager', '_WL_ENV', 'wayland-9') == 'wayland-9'

    def test_display_manager_falls_back_without_env(self):
        assert _wayland_display_for(
            'display_manager', '_WL_ENV', None) == 'wayland-0'

    def test_display_manager_empty_env_falls_back(self):
        assert _wayland_display_for(
            'display_manager', '_WL_ENV', '') == 'wayland-0'

    def test_home_launcher_prefers_session_display(self):
        assert _wayland_display_for(
            'home_launcher', '_WAYDROID_ENV', 'wayland-9') == 'wayland-9'

    def test_home_launcher_falls_back_without_env(self):
        assert _wayland_display_for(
            'home_launcher', '_WAYDROID_ENV', None) == 'wayland-0'

    def test_home_launcher_empty_env_falls_back(self):
        assert _wayland_display_for(
            'home_launcher', '_WAYDROID_ENV', '') == 'wayland-0'


if __name__ == '__main__':
    pytest = __import__('pytest')
    raise SystemExit(pytest.main([__file__, '-v']))
