"""Headless seams for the tablet glass failures: Search OSK, switcher
visibility, shade Settings, always-on sliders, edge-swipe vs Files.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

ROOT = Path(__file__).parent.parent
WINDOW_SRC = (ROOT / 'launcher' / 'src' / 'window.py').read_text(encoding='utf-8')
SWITCHER_SRC = (
    ROOT / 'launcher' / 'src' / 'app_switcher.py').read_text(encoding='utf-8')
SHADE_SRC = (
    ROOT / 'launcher' / 'src' / 'notification_shade.py').read_text(encoding='utf-8')
QA_SRC = (
    ROOT / 'launcher' / 'src' / 'quick_actions.py').read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def window():
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
    gtk.Button = type('Button', (), {})

    installed = {
        'gi': gi, 'gi.repository': repo,
        'gi.repository.Adw': adw, 'gi.repository.Gdk': gdk,
        'gi.repository.GLib': glib, 'gi.repository.Gtk': gtk,
        'gi.repository.Pango': pango, 'gi.repository.Gio': gio,
    }
    saved = {name: sys.modules.get(name) for name in installed}
    saved_window = sys.modules.get('window')
    sys.modules.update(installed)
    sys.modules.pop('window', None)
    try:
        import window as window_mod
        yield window_mod
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        if saved_window is None:
            sys.modules.pop('window', None)
        else:
            sys.modules['window'] = saved_window


def test_switcher_anchors_bottom_like_shade():
    assert 'LayerShell.Edge.TOP, False' in SWITCHER_SRC
    assert 'LayerShell.Edge.BOTTOM, True' in SWITCHER_SRC
    assert "add_css_class('switcher-window')" in SWITCHER_SRC


def test_switcher_show_presents_visible_window():
    show = SWITCHER_SRC.split('def show_switcher')[1].split('def hide_switcher')[0]
    assert 'set_visible(True)' in show
    assert 'self.present()' in show
    assert 'Layer.OVERLAY' in SWITCHER_SRC


def test_show_switcher_uses_live_manager():
    body = WINDOW_SRC.split('def _show_switcher')[1].split('def _build_apps_page')[0]
    assert 'AppSwitcher(manager=manager' in body
    assert '_ensure_toplevel_manager()' in body
    assert 'hide_switcher()' in body
    assert 'get_visible()' in body


def test_switcher_has_close_button_and_tappable_cards():
    assert "label='▲ Close'" in SWITCHER_SRC
    assert 'def _on_card_tap' in SWITCHER_SRC
    make = SWITCHER_SRC.split('def _make_card')[1].split('def _on_card_drag')[0]
    assert 'Gtk.Button()' not in make
    assert 'GestureClick' in make
    show = SWITCHER_SRC.split('def show_switcher')[1].split('def hide_switcher')[0]
    assert 'KeyboardMode.EXCLUSIVE' in show
    hide = SWITCHER_SRC.split('def hide_switcher')[1]
    assert 'KeyboardMode.NONE' in hide


def test_toplevel_activate_is_queued_to_wayland_thread():
    src = (ROOT / 'launcher' / 'src' / 'toplevel_manager.py').read_text(
        encoding='utf-8')
    backend = src.split('class WaylandToplevelBackend')[1].split(
        'class ToplevelManager')[0]
    public = backend.split('def activate(self, handle')[1].split(
        'def close(self, handle')[0]
    assert "_requests.put(('activate'" in public
    assert 'toplevel.activate' not in public
    assert 'def _apply_request' in backend
    assert 'select.select' in backend
    thread = src.split('def _thread_loop')[1].split('def _arm_pump')[0]
    assert 'select.select' in thread
    assert '_read_and_dispatch' in thread


def test_toplevel_thread_reads_display_fd():
    """dispatch(block=False) is dispatch_pending and does not read the socket.

    Recents showed "No open apps" after the wakeup loop called only that.
    """
    src = (ROOT / 'launcher' / 'src' / 'toplevel_manager.py').read_text(
        encoding='utf-8')
    reader = src.split('def _read_and_dispatch')[1].split('def _thread_loop')[0]
    assert 'reader()' in reader
    assert 'dispatch(block=True)' in reader
    connect = src.split('def _connect')[1].split('def _is_real_display')[0]
    assert 'Thread(' not in connect
    assert 'dispatch(block=True)' not in connect
    bind = src.split('def connect(self, callback)')[1].split(
        'def _replay_handles')[0]
    assert 'Thread(' in bind
    assert '_replay_handles()' in bind


def test_open_settings_hops_over_apps():
    body = WINDOW_SRC.split('def _open_settings')[1].split('def _leave_settings')[0]
    assert "set_visible_child_name('settings')" in body
    assert 'present_over_apps()' in body
    assert 'on_open_settings=self._open_settings' in WINDOW_SRC


def test_leave_settings_drops_layer():
    assert 'def _leave_settings' in WINDOW_SRC
    assert 'drop_to_background()' in WINDOW_SRC
    assert "label='Back'" in WINDOW_SRC


def test_toplevel_manager_never_blocks_gtk_thread():
    src = (ROOT / 'launcher' / 'src' / 'toplevel_manager.py').read_text(
        encoding='utf-8')
    assert 'self._display.roundtrip()' not in src
    assert '_FOREIGN_TOPLEVEL_BIND_VERSION = 1' in src
    assert 'def _thread_loop' in src
    connect = src.split('def _connect')[1].split('def _is_real_display')[0]
    assert 'dispatch(block=True)' not in connect
    assert 'roundtrip' not in connect
    thread = src.split('def _thread_loop')[1].split('def _arm_pump')[0]
    assert '_read_and_dispatch' in thread


def test_shade_settings_hides_immediately():
    body = SHADE_SRC.split('def _on_settings_clicked')[1].split('def _on_power')[0]
    assert 'self.hide()' in body
    assert '_on_open_settings' in body


def test_sliders_are_not_hidden_at_build():
    assert 'self.sliders_box.set_visible(True)' in QA_SRC
    expand = QA_SRC.split('def expand')[1].split('def sync_sliders')[0]
    assert 'sliders_box.set_visible' not in expand


def test_wifi_password_dialog_is_wired_to_osk():
    assert 'on_keyboard=self._on_entry_keyboard' in WINDOW_SRC
    assert 'def _attach_osk' in WINDOW_SRC
    assert '_attach_osk(self.apn_pass_entry)' in WINDOW_SRC


def test_home_compacts_unresolved_slots_at_start():
    assert 'compact_unresolved_home' in WINDOW_SRC


def test_search_hops_over_apps_while_typing():
    arm = WINDOW_SRC.split('def _arm_search_keyboard')[1].split(
        'def _disarm_search_keyboard')[0]
    disarm = WINDOW_SRC.split('def _disarm_search_keyboard')[1].split(
        'def _on_entry_keyboard')[0]
    assert 'present_over_apps()' in arm
    assert 'drop_to_background()' in disarm


def test_appearance_card_is_on_settings_page():
    assert 'def _build_appearance_card' in WINDOW_SRC
    assert 'def _on_theme_picked' in WINDOW_SRC
    assert 'appearance_card' in WINDOW_SRC


def test_origin_is_screen_edge(window):
    assert window.origin_is_screen_edge((10, 400), 800) is True
    assert window.origin_is_screen_edge((790, 400), 800) is True
    assert window.origin_is_screen_edge((400, 400), 800) is False
    assert window.origin_is_screen_edge(None, 800) is False
    assert window.origin_is_screen_edge((10, 400), 0) is False


def test_origin_is_bottom_edge(window):
    assert window.origin_is_bottom_edge((400, 1220), 1280) is True
    assert window.origin_is_bottom_edge((400, 400), 1280) is False
    assert window.origin_is_bottom_edge(None, 1280) is False


def test_home_edge_swipe_does_not_launch_files(window):
    actions: list[str] = []
    shell = types.SimpleNamespace(
        stack=types.SimpleNamespace(
            get_visible_child_name=lambda: 'home',
            set_visible_child_name=lambda _n: None,
            set_transition_type=lambda _t: None,
        ),
        _home_launcher=types.SimpleNamespace(edit_mode=False),
        gesture_config=types.SimpleNamespace(
            get=lambda _k: 'launch:org.gnome.Nautilus.desktop'),
        _swipe_origin=(8.0, 400.0),
        get_width=lambda: 800,
        get_height=lambda: 1280,
        _dispatch_gesture_action=lambda action: actions.append(action),
        _show_switcher=lambda: None,
    )
    window.ShellWindow._on_stack_swipe(shell, None, 400.0, 0.0)
    assert actions == []


def test_home_mid_swipe_still_launches_files(window):
    actions: list[str] = []
    shell = types.SimpleNamespace(
        stack=types.SimpleNamespace(
            get_visible_child_name=lambda: 'home',
            set_visible_child_name=lambda _n: None,
            set_transition_type=lambda _t: None,
        ),
        _home_launcher=types.SimpleNamespace(edit_mode=False),
        gesture_config=types.SimpleNamespace(
            get=lambda _k: 'launch:org.gnome.Nautilus.desktop'),
        _swipe_origin=(400.0, 400.0),
        get_width=lambda: 800,
        get_height=lambda: 1280,
        _dispatch_gesture_action=lambda action: actions.append(action),
        _show_switcher=lambda: None,
    )
    window.ShellWindow._on_stack_swipe(shell, None, 400.0, 0.0)
    assert actions == ['launch:org.gnome.Nautilus.desktop']


def test_bottom_origin_up_swipe_opens_switcher_not_drawer(window):
    shown: list[str] = []
    stack = types.SimpleNamespace(
        child='home',
        get_visible_child_name=lambda: stack.child,
        set_visible_child_name=lambda name: shown.append(name),
        set_transition_type=lambda _t: None,
    )
    shell = types.SimpleNamespace(
        stack=stack,
        _home_launcher=types.SimpleNamespace(edit_mode=False),
        gesture_config=types.SimpleNamespace(get=lambda _k: 'notification_shade'),
        _swipe_origin=(400.0, 1260.0),
        get_width=lambda: 800,
        get_height=lambda: 1280,
        _dispatch_gesture_action=lambda _a: None,
        _show_switcher=lambda: shown.append('switcher'),
    )
    window.ShellWindow._on_stack_swipe(shell, None, 0.0, -1200.0)
    assert shown == ['switcher']


def test_mid_display_long_up_still_opens_drawer(window):
    shown: list[str] = []
    stack = types.SimpleNamespace(
        child='home',
        get_visible_child_name=lambda: 'home',
        set_visible_child_name=lambda name: shown.append(name),
        set_transition_type=lambda _t: None,
    )
    shell = types.SimpleNamespace(
        stack=stack,
        _home_launcher=types.SimpleNamespace(edit_mode=False),
        gesture_config=types.SimpleNamespace(get=lambda _k: 'notification_shade'),
        _swipe_origin=(400.0, 400.0),
        get_width=lambda: 800,
        get_height=lambda: 1280,
        _dispatch_gesture_action=lambda _a: None,
        _show_switcher=lambda: shown.append('switcher'),
    )
    window.ShellWindow._on_stack_swipe(shell, None, 0.0, -1200.0)
    assert shown == ['apps']


def test_open_settings_presents_over_apps(window):
    events: list[object] = []
    shell = types.SimpleNamespace(
        stack=types.SimpleNamespace(
            set_visible_child_name=lambda name: events.append(('page', name))),
        present_over_apps=lambda: events.append('top'),
        _disarm_search_keyboard=lambda: events.append('disarm'),
        _raised_for_settings=False,
    )
    window.ShellWindow._open_settings(shell)
    assert events == ['disarm', ('page', 'settings'), 'top']
    assert shell._raised_for_settings is True


def test_leaving_settings_drops_to_background(window):
    events: list[str] = []
    shell = types.SimpleNamespace(
        _raised_for_settings=True,
        _pick_slot_mode=True,
        _pick_gesture_key='x',
        _disarm_search_keyboard=lambda: events.append('disarm'),
        drop_to_background=lambda: events.append('drop'),
        stack=types.SimpleNamespace(get_visible_child_name=lambda: 'home'),
    )
    window.ShellWindow._on_stack_page_changed(shell, shell.stack, None)
    assert events == ['disarm', 'drop']
    assert shell._raised_for_settings is False


def test_theme_pick_paper_clears_prefer_dark(window):
    events: list[str] = []

    class FakeConfig:
        def __init__(self) -> None:
            self.data = {'theme': 'amoled'}
            self.calls: list[tuple] = []

        def set_theme(self, key: str) -> None:
            self.data['theme'] = key
            self.calls.append(('theme', key))

        def set_prefer_dark(self, enabled: bool) -> None:
            self.data['prefer_dark'] = enabled
            self.calls.append(('dark', enabled))

    shell = types.SimpleNamespace(
        config=FakeConfig(),
        _theme_buttons={},
        _refresh_theme_buttons=lambda: events.append('buttons'),
        _apply_theme=lambda: events.append('theme'),
        _retheme_surfaces=lambda: events.append('surfaces'),
    )
    window.ShellWindow._on_theme_picked(shell, 'paper')
    assert ('theme', 'paper') in shell.config.calls
    assert ('dark', False) in shell.config.calls
    assert events == ['buttons', 'theme', 'surfaces']
