"""Tests for wiring toplevel_manager into app_switcher.py.

The switcher is a Gtk.Window, so it cannot be instantiated in a headless
environment. The wiring contract it depends on -- that a ToplevelManager
drives the app list and that activate/close dispatch through the manager's
backend -- is exercised here with a FakeBackend, the same seam the manager
itself is tested through. A fake manager is also used to verify the switcher's
own delegation logic (refresh -> AppInfo, focus/close -> manager) without a
display.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from app_switcher import AppInfo
from toplevel_manager import Toplevel, ToplevelManager


class FakeBackend:
    """Simulates the pywayland backend contract for the manager."""

    def __init__(self, available: bool = True) -> None:
        self._available = available
        self._callback = None
        self.activated: list[object] = []
        self.closed: list[object] = []

    def available(self) -> bool:
        return self._available

    def connect(self, callback) -> None:
        self._callback = callback

    def activate(self, handle: object) -> None:
        self.activated.append(handle)

    def close(self, handle: object) -> None:
        self.closed.append(handle)

    def add(self, handle: object, app_id: str, title: str) -> None:
        self._callback(handle, app_id, title)

    def remove(self, handle: object) -> None:
        self._callback(handle, None, None)


class FakeManager:
    """Duck-typed stand-in for ToplevelManager used by the switcher."""

    def __init__(self, apps: list[Toplevel]) -> None:
        self._apps = apps
        self.available = True
        self.change_callbacks: list[object] = []
        self.activated: list[object] = []
        self.closed: list[object] = []

    def list(self) -> list[Toplevel]:
        return list(self._apps)

    def on_change(self, callback) -> None:
        self.change_callbacks.append(callback)

    def activate(self, handle: object) -> None:
        self.activated.append(handle)

    def close(self, handle: object) -> None:
        self.closed.append(handle)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def manager(backend: FakeBackend) -> ToplevelManager:
    return ToplevelManager(backend=backend)


def test_refresh_builds_appinfo_from_manager_list(
    backend: FakeBackend, manager: ToplevelManager,
) -> None:
    """The switcher's refresh() turns manager.list() into AppInfo records."""
    backend.add('h1', 'org.a.App', 'Alpha')
    backend.add('h2', 'org.b.App', 'Beta')

    from app_switcher import AppSwitcher

    # refresh() is the headless seam: it reads the manager and produces the
    # AppInfo records the cards are rendered from, without touching GTK.
    apps = AppSwitcher._apps_from_manager(manager)
    assert [a.title for a in apps] == ['Alpha', 'Beta']
    assert [a.handle for a in apps] == ['h1', 'h2']
    assert all(a.app_id.startswith('org.') for a in apps)


def test_refresh_filters_own_shell_surface(
    backend: FakeBackend, manager: ToplevelManager,
) -> None:
    """Own-shell surfaces never appear as switchable apps."""
    backend.add('h1', 'org.a.App', 'Alpha')
    backend.add('h2', 'io.piercingxx.XXWM', 'Shell')

    from app_switcher import AppSwitcher

    apps = AppSwitcher._apps_from_manager(manager)
    assert [a.handle for a in apps] == ['h1']


def test_focus_delegates_to_manager_activate() -> None:
    """Focusing a card calls manager.activate(handle), not wmctrl/pid."""
    fake = FakeManager([Toplevel('org.a.App', 'Alpha', 'h1')])
    app = AppInfo('org.a.App', 'Alpha', handle='h1')

    from app_switcher import AppSwitcher

    AppSwitcher._focus_app_with_manager(fake, app)
    assert fake.activated == ['h1']


def test_kill_delegates_to_manager_close() -> None:
    """Killing a card calls manager.close(handle), not os.kill/pid."""
    fake = FakeManager([Toplevel('org.a.App', 'Alpha', 'h1')])
    app = AppInfo('org.a.App', 'Alpha', handle='h1')

    from app_switcher import AppSwitcher

    AppSwitcher._kill_app_with_manager(fake, app)
    assert fake.closed == ['h1']


def test_manager_on_change_wires_to_refresh() -> None:
    """The switcher registers a refresh callback on the manager."""
    fake = FakeManager([Toplevel('org.a.App', 'Alpha', 'h1')])
    from app_switcher import AppSwitcher

    seen: list[list[Toplevel]] = []
    AppSwitcher._wire_change(fake, lambda: seen.append(fake.list()))
    assert len(fake.change_callbacks) == 1
    # The registered callback is the switcher's refresh: invoking it re-reads
    # the manager's current list.
    fake.change_callbacks[0]()
    assert [t.handle for t in seen[0]] == ['h1']


def test_manager_backend_does_not_require_gtk() -> None:
    """The manager wiring itself needs no GTK: pywayland absence degrades."""
    mgr = ToplevelManager(backend=None)
    assert mgr.list() == []


def test_focus_hands_screen_to_app_via_on_activated() -> None:
    """Activating a row calls on_activated after dispatching to the manager."""
    fake = FakeManager([Toplevel('org.a.App', 'Alpha', 'h1')])
    app = AppInfo('org.a.App', 'Alpha', handle='h1')
    handed: list[bool] = []

    from app_switcher import AppSwitcher

    AppSwitcher._focus_app_with_manager(fake, app, on_activated=lambda: handed.append(True))
    assert fake.activated == ['h1']
    assert handed == [True]


def test_focus_without_callback_still_activates() -> None:
    """on_activated is optional: standalone switchers just activate."""
    fake = FakeManager([Toplevel('org.a.App', 'Alpha', 'h1')])
    app = AppInfo('org.a.App', 'Alpha', handle='h1')

    from app_switcher import AppSwitcher

    AppSwitcher._focus_app_with_manager(fake, app)
    assert fake.activated == ['h1']


def test_switcher_accepts_on_activated() -> None:
    """AppSwitcher.__init__ takes the activation-handoff callback."""
    import inspect

    from app_switcher import AppSwitcher

    assert 'on_activated' in inspect.signature(AppSwitcher.__init__).parameters


def test_window_hands_screen_to_app_on_switch() -> None:
    """ShellWindow builds the switcher with drop_to_background as on_activated,
    so activating a row returns the shell to the BOTTOM layer."""
    window_text = (
        Path(__file__).parent.parent / 'launcher' / 'src' / 'window.py'
    ).read_text(encoding='utf-8')
    assert 'on_activated=self.drop_to_background' in window_text


def test_window_rethemes_switcher() -> None:
    """ShellWindow fans hot-reloaded themes out to the switcher surface."""
    window_text = (
        Path(__file__).parent.parent / 'launcher' / 'src' / 'window.py'
    ).read_text(encoding='utf-8')
    start = window_text.index('def _retheme_surfaces')
    end = window_text.index('def ', start + len('def _retheme_surfaces'))
    assert 'self._switcher' in window_text[start:end]


class FakeThemeProvider:
    """Stand-in for Gtk.CssProvider recording loaded sheets."""

    def __init__(self) -> None:
        self.loads: list[str] = []

    def load_from_data(self, data: bytes) -> None:
        self.loads.append(data.decode('utf-8'))


def _stub_switcher(monkeypatch, preset):
    """AppSwitcher instance without a window: apply_theme only needs the
    provider (and the config preset for the standalone path)."""
    from app_switcher import AppSwitcher

    inst = AppSwitcher.__new__(AppSwitcher)
    inst._theme_provider = FakeThemeProvider()
    monkeypatch.setattr(AppSwitcher, '_display_preset', lambda self: preset)
    return inst


def test_apply_theme_reloads_single_provider(monkeypatch) -> None:
    """apply_theme(preset) reloads the one display-level provider in place."""
    from app_switcher import AppSwitcher
    from config import THEME_PRESETS

    preset = THEME_PRESETS['paper']
    inst = _stub_switcher(monkeypatch, preset)
    provider = inst._theme_provider
    AppSwitcher.apply_theme(inst, preset)
    AppSwitcher.apply_theme(inst, THEME_PRESETS['ocean'])
    assert len(provider.loads) == 2
    assert preset.background in provider.loads[0]
    assert THEME_PRESETS['ocean'].background in provider.loads[1]


def test_apply_theme_defaults_to_config_preset(monkeypatch) -> None:
    """apply_theme() with no preset renders the surface's own preset."""
    from app_switcher import AppSwitcher
    from config import THEME_PRESETS

    preset = THEME_PRESETS['forest']
    inst = _stub_switcher(monkeypatch, preset)
    AppSwitcher.apply_theme(inst)
    assert len(inst._theme_provider.loads) == 1
    assert preset.background in inst._theme_provider.loads[0]