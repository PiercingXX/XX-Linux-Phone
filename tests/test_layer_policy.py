"""Layer/keyboard focus policy for the shell window (W1-FIT).

phoc 0.56 speaks zwlr_layer_shell_v1 version 3, where KeyboardMode.ON_DEMAND
does not exist, so editable surfaces need TOP + EXCLUSIVE to receive keys.
The decision itself is the pure ``window.decide_layer_policy`` table
(page, popover_open, over_apps) -> (layer, keyboard_mode); ``window`` is
imported against fake gi bindings (the test_keyboard_dismiss idiom) because
it builds GTK widgets at module level.

Constructing a full ShellWindow under fakes is infeasible (``__init__``
builds the whole widget tree), so the construction call sites — stack
homogeneity off, policy wiring, the drawer layout — are asserted at the
source text instead (the test_preload_gate idiom).
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

SRC = Path(__file__).parent.parent / 'launcher' / 'src'


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


class TestDecideLayerPolicy:
    def test_home_at_rest_is_bottom_without_keys(self, window):
        assert window.decide_layer_policy('home', False, False) == ('BOTTOM', 'NONE')

    def test_home_over_apps_stays_top_without_keys(self, window):
        assert window.decide_layer_policy('home', False, True) == ('TOP', 'NONE')

    def test_drawer_needs_exclusive_keys(self, window):
        assert window.decide_layer_policy('apps', False, False) == ('TOP', 'EXCLUSIVE')
        assert window.decide_layer_policy('apps', False, True) == ('TOP', 'EXCLUSIVE')

    def test_settings_needs_exclusive_keys(self, window):
        assert window.decide_layer_policy('settings', False, False) == ('TOP', 'EXCLUSIVE')
        assert window.decide_layer_policy('settings', False, True) == ('TOP', 'EXCLUSIVE')

    def test_open_popover_needs_exclusive_keys_on_home(self, window):
        assert window.decide_layer_policy('home', True, False) == ('TOP', 'EXCLUSIVE')
        assert window.decide_layer_policy('home', True, True) == ('TOP', 'EXCLUSIVE')

    def test_unknown_page_falls_back_to_rest_state(self, window):
        assert window.decide_layer_policy(None, False, False) == ('BOTTOM', 'NONE')
        assert window.decide_layer_policy(None, False, True) == ('TOP', 'NONE')


class TestPopoverCounting:
    def _harness(self, window):
        calls: list[int] = []
        shell = types.SimpleNamespace(
            _popover_count=0,
            _apply_layer_policy=lambda: calls.append(1),
        )
        assert isinstance(window.ShellWindow._on_item_popover, object)
        return shell, calls

    def test_open_increments_and_close_decrements(self, window):
        shell, calls = self._harness(window)
        window.ShellWindow._on_item_popover(shell, True)
        window.ShellWindow._on_item_popover(shell, True)
        assert shell._popover_count == 2
        window.ShellWindow._on_item_popover(shell, False)
        assert shell._popover_count == 1
        assert len(calls) == 3

    def test_close_never_goes_negative(self, window):
        shell, calls = self._harness(window)
        window.ShellWindow._on_item_popover(shell, False)
        assert shell._popover_count == 0
        assert len(calls) == 1


def _read(name: str) -> str:
    return (SRC / name).read_text(encoding='utf-8')


class TestConstructionCallSites:
    """ShellWindow cannot be constructed headlessly; pin the call sites."""

    def test_shell_stack_is_not_homogeneous(self):
        src = _read('window.py')
        assert 'set_hhomogeneous(False)' in src
        assert 'set_vhomogeneous(False)' in src
        assert 'set_interpolate_size(False)' in src

    def test_wizard_stack_is_not_homogeneous(self):
        src = _read('first_boot.py')
        assert 'set_hhomogeneous(False)' in src
        assert 'set_vhomogeneous(False)' in src
        assert 'set_interpolate_size(False)' in src

    def test_policy_wired_to_page_changes_popovers_and_present(self):
        src = _read('window.py')
        assert 'self._apply_layer_policy()' in src
        assert 'on_popover_changed=self._on_item_popover' in src
        assert 'on_popover_changed' in _read('app_item_actions.py')

    def test_drop_to_background_forces_bottom_without_keys(self):
        body = _read('window.py').split('def drop_to_background')[1].split('def ')[0]
        assert 'Layer.BOTTOM' in body
        assert 'KeyboardMode.NONE' in body

    def test_lock_screen_no_longer_uses_on_demand(self):
        src = _read('lock_screen.py')
        assert 'KeyboardMode.EXCLUSIVE' in src
        code = [line for line in src.splitlines()
                if not line.strip().startswith('#')]
        assert not any('ON_DEMAND' in line for line in code)

    def test_drawer_gap_is_layout_not_spacer(self):
        src = _read('drawer_page.py')
        assert '_SheetTopGapLayout' in src
        assert 'set_size_request(-1, int(h' not in src

    def test_power_menu_card_has_no_min_width_and_caps_width(self):
        src = _read('power_menu.py')
        assert 'min-width: 260px' not in src
        assert 'Adw.Clamp' in src
        assert 'maximum_size=420' in src
