"""Yield to apps mapped outside the shell (W5 fix-up 3).

On the VM an app started by systemd-run / D-Bus activation / a notification
action mapped UNDER the shell (last on Settings, sent home via IPC) and was
invisible. A new foreign toplevel now gets the shell out of the way exactly
as a self-made launch does: home page, BOTTOM + NONE, drawer/settings/shade
closed. ``window`` is imported against fake gi bindings (the
test_keyboard_dismiss idiom) and the real ShellWindow helpers run unbound
on bare namespaces; main.py routing is asserted at the source text (the
test_preload_gate idiom — main.py needs GTK).
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
MAIN_PY = SRC / 'main.py'


@pytest.fixture(scope='module')
def window():
    """Import window.py once against fake gi bindings (no display)."""
    def require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError('no layer shell')
        return None

    gi = types.ModuleType('gi')
    gi.require_version = require_version
    repo = types.ModuleType('gi.repository')
    gi.repository = repo

    def make(name):
        return types.ModuleType(f'gi.repository.{name}')

    adw, gdk, glib, gtk, pango, gio = (
        make('Adw'), make('Gdk'), make('GLib'), make('Gtk'),
        make('Pango'), make('Gio'),
    )
    adw.ApplicationWindow = type('ApplicationWindow', (), {})
    gtk.Editable = type('Editable', (), {})
    gtk.PickFlags = types.SimpleNamespace(DEFAULT=0)

    installed = {
        'gi': gi, 'gi.repository': repo,
        'gi.repository.Adw': adw, 'gi.repository.Gdk': gdk,
        'gi.repository.GLib': glib, 'gi.repository.Gtk': gtk,
        'gi.repository.Pango': pango, 'gi.repository.Gio': gio,
    }
    saved = {name: sys.modules.get(name) for name in installed}
    saved_window = sys.modules.pop('window', None)
    sys.modules.update(installed)
    try:
        import window
        yield window
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        if saved_window is not None:
            sys.modules['window'] = saved_window
        else:
            sys.modules.pop('window', None)


class FakeStack:
    def __init__(self, page='settings'):
        self.page = page
        self.calls: list[str] = []

    def set_visible_child_name(self, page):
        self.calls.append(page)
        self.page = page

    def get_visible_child_name(self):
        return self.page


class FakeOverlay:
    def __init__(self, visible=True):
        self._visible = visible
        self.hides = 0

    def get_visible(self):
        return self._visible

    def hide_shade(self):
        self._visible = False
        self.hides += 1

    def hide_switcher(self):
        self._visible = False
        self.hides += 1


class FakeManager:
    """Duck-typed stand-in for ToplevelManager."""

    def __init__(self, apps=()):
        self._apps = list(apps)
        self.change_callbacks: list[object] = []

    def list(self):
        return list(self._apps)

    def on_change(self, callback):
        self.change_callbacks.append(callback)

    def fire(self):
        for callback in list(self.change_callbacks):
            callback()


def _top(app_id='org.example.App', title='App', handle='h1'):
    return types.SimpleNamespace(app_id=app_id, title=title, handle=handle)


def _shell(window):
    """Bare-namespace shell stuck on Settings, raised, overlays open.

    Sibling helpers are attached explicitly (the test_layer_policy idiom):
    bare namespaces have no class fallback, so unbound ShellWindow methods
    can only reach siblings present on the instance.
    """
    shell = types.SimpleNamespace(
        stack=FakeStack('settings'),
        _over_apps=True,
        _popover_count=0,
        _drawer_open_folder=7,
        _shade=FakeOverlay(True),
        _switcher=FakeOverlay(True),
        _toplevel_manager=None,
        _known_toplevels=set(),
    )
    shell._on_foreign_toplevels = (
        lambda: window.ShellWindow._on_foreign_toplevels(shell))
    shell._yield_to_foreign_app = (
        lambda: window.ShellWindow._yield_to_foreign_app(shell))
    shell.drop_to_background = (
        lambda: window.ShellWindow.drop_to_background(shell))
    shell._apply_layer_policy = (
        lambda: window.ShellWindow._apply_layer_policy(shell))
    return shell


class TestForeignToplevelYield:
    def test_new_foreign_toplevel_yields(self, window):
        shell = _shell(window)
        manager = FakeManager()
        window.ShellWindow._wire_foreign_toplevels(shell, manager)
        assert manager.change_callbacks != []
        manager._apps.append(_top())
        manager.fire()
        assert shell.stack.page == 'home'
        assert shell._over_apps is False
        assert shell._drawer_open_folder is None
        assert shell._shade.hides == 1
        assert shell._switcher.hides == 1

    def test_already_home_still_drops(self, window):
        shell = _shell(window)
        shell.stack.page = 'home'
        manager = FakeManager()
        window.ShellWindow._wire_foreign_toplevels(shell, manager)
        manager._apps.append(_top())
        manager.fire()
        assert shell.stack.page == 'home'
        assert shell.stack.calls == ['home']
        assert shell._over_apps is False

    def test_preexisting_toplevels_do_not_yield(self, window):
        shell = _shell(window)
        manager = FakeManager([_top()])
        window.ShellWindow._wire_foreign_toplevels(shell, manager)
        manager.fire()
        assert shell.stack.calls == []
        assert shell._over_apps is True
        assert shell._shade.hides == 0

    def test_removal_does_not_yield(self, window):
        shell = _shell(window)
        manager = FakeManager([_top()])
        window.ShellWindow._wire_foreign_toplevels(shell, manager)
        manager._apps.clear()
        manager.fire()
        assert shell.stack.calls == []
        assert shell._over_apps is True

    def test_second_fire_without_change_does_not_yield_again(self, window):
        shell = _shell(window)
        manager = FakeManager()
        window.ShellWindow._wire_foreign_toplevels(shell, manager)
        manager._apps.append(_top())
        manager.fire()
        assert shell.stack.calls == ['home']
        manager.fire()
        assert shell.stack.calls == ['home']


class TestHomeRecomputesPolicy:
    def test_ipc_page_home_recomputes_policy(self):
        body = MAIN_PY.read_text(encoding='utf-8')
        start = body.find('def _show_ipc_page')
        assert start != -1
        block = body[start:start + 600]
        assert 'set_visible_child_name' in block
        assert '_apply_layer_policy' in block

    def test_home_gesture_raises_through_present(self):
        body = MAIN_PY.read_text(encoding='utf-8')
        start = body.find("command == 'gesture.home'")
        assert start != -1
        block = body[start:start + 500]
        # The arm funnels through ShellWindow.go_home, which hides the
        # overlays, shows home, and raises via present_over_apps
        # (see test_go_home_hides_overlays_and_raises).
        assert 'go_home()' in block

    def test_present_recomputes_policy(self):
        src = (SRC / 'window.py').read_text(encoding='utf-8')
        start = src.find('def present_over_apps')
        assert start != -1
        assert '_apply_layer_policy' in src[start:start + 900]

    def test_shell_wires_toplevel_yield_at_construction(self):
        src = (SRC / 'window.py').read_text(encoding='utf-8')
        assert 'self._wire_foreign_toplevels()' in src


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
