"""W2 settings tests: deep links, 320 px rows, Shell section, WiFi password.

window.py is imported against recorder fakes (no display): fake widgets
record the sizing calls that keep the settings page within 320 px, and bare
ShellWindow instances drive open_settings / _on_wifi_selected / page build.
"""
import inspect
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


class _FakeWidget:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.children = []
        self.classes = []
        self.props = {}
        self.handlers = {}

    def append(self, child):
        self.children.append(child)
        try:
            child._parent = self
        except AttributeError:
            pass

    def remove(self, child):
        if child in self.children:
            self.children.remove(child)

    def get_first_child(self):
        return self.children[0] if self.children else None

    def get_next_sibling(self):
        parent = getattr(self, '_parent', None)
        if parent is None:
            return None
        siblings = parent.children
        try:
            index = siblings.index(self)
        except ValueError:
            return None
        return siblings[index + 1] if index + 1 < len(siblings) else None

    def set_child(self, child):
        self.child = child

    def get_child(self):
        return getattr(self, 'child', None) or _FakeWidget()

    def add_css_class(self, name):
        self.classes.append(name)

    def connect(self, signal, callback, *args):
        self.handlers[signal] = (callback, args)
        return 1

    def set_hexpand(self, value):
        self.props['hexpand'] = value

    def set_vexpand(self, value):
        self.props['vexpand'] = value

    def set_xalign(self, value):
        self.props['xalign'] = value

    def set_margin_top(self, value):
        self.props['margin_top'] = value

    def set_margin_bottom(self, value):
        self.props['margin_bottom'] = value

    def set_margin_start(self, value):
        self.props['margin_start'] = value

    def set_margin_end(self, value):
        self.props['margin_end'] = value

    def set_policy(self, horizontal, vertical):
        self.props['policy'] = (horizontal, vertical)

    def set_wrap(self, value):
        self.props['wrap'] = value

    def set_wrap_mode(self, value):
        self.props['wrap_mode'] = value

    def set_max_width_chars(self, value):
        self.props['max_width_chars'] = value

    def set_ellipsize(self, value):
        self.props['ellipsize'] = value

    def set_placeholder_text(self, value):
        self.props['placeholder'] = value

    def set_text(self, value):
        self.props['text'] = value

    def set_visibility(self, value):
        self.props['visibility'] = value

    def set_width_chars(self, value):
        self.props['width_chars'] = value

    def set_active(self, value):
        self.props['active'] = bool(value)

    def get_active(self):
        return self.props.get('active', False)

    def get_allocation(self):
        return self.props.get('allocation', SimpleNamespace(x=0, y=0))

    def get_vadjustment(self):
        return self.props['vadjustment']


class _FakeAdjustment:
    def __init__(self):
        self.values = []

    def set_value(self, value):
        self.values.append(value)


class _FakeStack:
    def __init__(self):
        self.visible = None
        self.handlers = {}

    def set_visible_child_name(self, name):
        self.visible = name

    def connect(self, signal, callback, *args):
        self.handlers[signal] = (callback, args)
        return 1


@pytest.fixture
def window():
    """Import window.py against recorder fakes (the test_layer_policy idiom:
    save sys.modules, pop window, install fakes, restore afterwards)."""

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
    gtk.Box = _FakeWidget
    gtk.Label = _FakeWidget
    gtk.Button = _FakeWidget
    gtk.Entry = _FakeWidget
    gtk.ScrolledWindow = _FakeWidget
    gtk.Switch = _FakeWidget
    gtk.Orientation = SimpleNamespace(HORIZONTAL=0, VERTICAL=1)
    gtk.PolicyType = SimpleNamespace(NEVER=0, AUTOMATIC=1)
    pango.WrapMode = SimpleNamespace(WORD=0, WORD_CHAR=1, CHAR=2)
    pango.EllipsizeMode = SimpleNamespace(NONE=0, START=1, MIDDLE=2, END=3)
    repo.Adw, repo.Gdk, repo.GLib, repo.Gtk, repo.Pango, repo.Gio = (
        adw, gdk, glib, gtk, pango, gio)
    glib.idle_add = lambda callback, *args: (callback(*args), 0)[1]

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
        sys.modules.pop('window', None)
        yield window
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if saved_window is not None:
            sys.modules['window'] = saved_window
        else:
            sys.modules.pop('window', None)


def _bare(window):
    w = object.__new__(window.ShellWindow)
    w.gesture_config = SimpleNamespace(get=lambda key: 'none')
    w.app_index = SimpleNamespace(entries=[])
    w.config = SimpleNamespace(data={}, label_for=lambda app_id, name: name)
    w._gesture_binding_labels = {}
    w._system_gesture_labels = {}
    return w


def _title_at(y):
    title = _FakeWidget(label='section', xalign=0)
    title.props['allocation'] = SimpleNamespace(x=0, y=y)
    return title


def _bare_open(window):
    w = _bare(window)
    w.stack = _FakeStack()
    w.presented = []
    w.present = lambda: w.presented.append(1)
    w._settings_section_titles = {
        'wifi': _title_at(120), 'bluetooth': _title_at(240),
        'mobile': _title_at(360),
    }
    w.adjustment = _FakeAdjustment()
    scroll = _FakeWidget()
    scroll.props['vadjustment'] = w.adjustment
    w._settings_scroll = scroll
    w.refreshed = []
    w._refresh_wifi = lambda: w.refreshed.append('wifi')
    w._refresh_bluetooth = lambda: w.refreshed.append('bluetooth')
    return w


class TestOpenSettings:
    def test_wifi_scrolls_and_scans(self, window):
        w = _bare_open(window)
        w.open_settings('wifi')
        assert w.stack.visible == 'settings'
        assert w.presented == [1]
        assert w.adjustment.values and w.adjustment.values[-1] == 120.0
        assert w.refreshed == ['wifi']

    def test_bluetooth_scrolls_and_lists(self, window):
        w = _bare_open(window)
        w.open_settings('bluetooth')
        assert w.stack.visible == 'settings'
        assert w.adjustment.values and w.adjustment.values[-1] == 240.0
        assert w.refreshed == ['bluetooth']

    def test_mobile_scrolls_without_extra_refresh(self, window):
        w = _bare_open(window)
        w.open_settings('mobile')
        assert w.adjustment.values and w.adjustment.values[-1] == 360.0
        assert w.refreshed == []

    def test_none_opens_top_without_scroll_or_refresh(self, window):
        w = _bare_open(window)
        w.open_settings(None)
        assert w.stack.visible == 'settings'
        assert w.presented == [1]
        assert w.adjustment.values == []
        assert w.refreshed == []

    def test_unknown_section_opens_top(self, window):
        w = _bare_open(window)
        w.open_settings('torch')
        assert w.stack.visible == 'settings'
        assert w.adjustment.values == []
        assert w.refreshed == []

    def test_shade_handler_is_open_settings(self, window):
        src = inspect.getsource(window.ShellWindow._ensure_shade)
        assert 'on_open_settings=self.open_settings' in src


class TestSettingsNarrowRows:
    def test_gesture_row_stacks_buttons_below_binding(self, window):
        w = _bare(window)
        binding, change, reset = _FakeWidget(), _FakeWidget(), _FakeWidget()
        row = w._build_gesture_row('Swipe left', binding, change, reset)
        assert len(row.children) == 3
        name, bound, buttons = row.children
        assert bound is binding
        assert buttons.children == [change, reset]
        assert name.props['wrap'] is True
        assert name.props['max_width_chars'] == 24

    def test_no_three_wide_control_row(self, window):
        import settings_page
        src = inspect.getsource(settings_page.SettingsPage._build_gesture_row)
        assert 'controls.append(binding)' not in src

    def test_apn_entries_shrink(self, window):
        w = _bare(window)
        w.stack = _FakeStack()
        w._build_settings_page()
        for entry in (w.apn_entry, w.apn_user_entry, w.apn_pass_entry):
            assert entry.props['width_chars'] == 12


class TestShellSection:
    def _page_box(self, window):
        w = _bare(window)
        w.stack = _FakeStack()
        scroll = w._build_settings_page()
        return w, scroll.child

    def _label_indices(self, box):
        return [(idx, child.kwargs.get('label')) for idx, child in
                enumerate(box.children)
                if isinstance(child, _FakeWidget)
                and 'label' in child.kwargs]

    def test_gestures_moved_to_bottom_under_shell_heading(self, window):
        _w, box = self._page_box(window)
        labels = self._label_indices(box)
        texts = [text for _idx, text in labels]
        assert 'Gestures' not in texts
        shell_idx = texts.index('Shell')
        about_idx = texts.index('About')
        assert shell_idx > about_idx
        assert box.children[-1].children, 'gestures card is the last block'

    def test_shell_note_names_config_dir(self, window):
        _w, box = self._page_box(window)
        labels = self._label_indices(box)
        texts = [text for _idx, text in labels]
        note_idx = texts.index('Other shell preferences live in ~/.config/xx-wm/')
        shell_idx = texts.index('Shell')
        assert note_idx == shell_idx + 1
        note = box.children[[idx for idx, _t in labels][note_idx]]
        assert 'dim-label' in note.classes
        assert note.props['wrap'] is True


class TestWifiPasswordMode:
    def _bare_wifi(self, window):
        w = _bare(window)
        w.statuses = []
        w._show_status = w.statuses.append
        w.connected = []
        w._connect_wifi = lambda ssid, pwd, sec=None: w.connected.append(
            (ssid, pwd, sec))
        w.shown = []
        w.item_actions = SimpleNamespace(
            _show_entry_dialog=lambda *args, **kwargs: w.shown.append(
                (args, kwargs)))
        return w

    def test_secured_network_uses_password_mode(self, window):
        w = self._bare_wifi(window)
        net = SimpleNamespace(ssid='HomeNet', security='WPA2', in_use=False)
        w._on_wifi_selected(net, object())
        assert len(w.shown) == 1
        (args, kwargs) = w.shown[0]
        assert args[1] == 'Password for HomeNet'
        assert kwargs.get('password') is True
        assert w.connected == []

    def test_open_network_connects_directly(self, window):
        w = self._bare_wifi(window)
        net = SimpleNamespace(ssid='Cafe', security='open', in_use=False)
        w._on_wifi_selected(net, object())
        assert w.shown == []
        assert w.connected == [('Cafe', None, 'open')]

    def test_current_network_is_noop(self, window):
        w = self._bare_wifi(window)
        net = SimpleNamespace(ssid='HomeNet', security='WPA2', in_use=True)
        w._on_wifi_selected(net, object())
        assert w.shown == []
        assert w.connected == []
        assert w.statuses == ['Already connected to HomeNet.']
