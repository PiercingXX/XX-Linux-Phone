"""CallUI button flow + CallBar show/hide contract (P2-D).

window.py already calls ``CallBar.show_bar(number)`` / ``hide_bar()`` and
constructs ``CallUI(on_accept=..., on_decline=...)``, so those signatures
are pinned here. Widget construction never runs — instances are built with
``object.__new__`` and fake labels/stacks, matching the test_quick_sliders
idiom.
"""
import inspect
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


def _install_fake_gi() -> None:
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.Error = type('Error', (Exception,), {})
    glib.idle_add = lambda *a, **k: 1
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.Variant = lambda *a, **k: None

    gdk = types.ModuleType('gi.repository.Gdk')
    gdk.Display = types.SimpleNamespace(get_default=lambda: None)

    gio = types.ModuleType('gi.repository.Gio')
    gio.BusType = types.SimpleNamespace(SYSTEM=1)
    gio.DBusSignalFlags = types.SimpleNamespace(NONE=0)
    gio.DBusCallFlags = types.SimpleNamespace(NONE=0)
    gio.bus_get_sync = lambda *a, **k: None

    gtk = types.ModuleType('gi.repository.Gtk')
    gtk.Window = type('Window', (), {})

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib
    sys.modules['gi.repository.Gdk'] = gdk
    sys.modules['gi.repository.Gio'] = gio
    sys.modules['gi.repository.Gtk'] = gtk


_install_fake_gi()

import call_ui  # noqa: E402  (needs fake gi modules installed first)
import modem_monitor  # noqa: E402


class FakeLabel:
    def __init__(self, text=''):
        self.text = text

    def set_text(self, text):
        self.text = text

    def get_text(self):
        return self.text


class FakeStack:
    def __init__(self):
        self.visible = None

    def set_visible_child_name(self, name):
        self.visible = name


def _make_call_ui(accept_fn=None, hangup_fn=None):
    ui = call_ui.CallUI.__new__(call_ui.CallUI)
    ui._events = []
    ui._on_accept = lambda: ui._events.append('accept')
    ui._on_decline = lambda: ui._events.append('decline')
    ui._on_hangup = lambda: ui._events.append('hangup')
    ui._accept_fn = accept_fn or (lambda path: True)
    ui._hangup_fn = hangup_fn or (lambda path: True)
    ui._incoming_call_path = '/org/freedesktop/ModemManager1/Call/3'
    ui._current_caller = 'Incoming call'
    ui._current_number = '+15550001111'
    ui._inc_caller = FakeLabel()
    ui._inc_number = FakeLabel()
    ui._act_caller = FakeLabel()
    ui._act_number = FakeLabel()
    ui._stack = FakeStack()
    ui._timer_id = None
    ui._call_start = None
    ui.presented = 0
    ui.hidden = 0
    ui.present = lambda: setattr(ui, 'presented', ui.presented + 1)
    ui.hide = lambda: setattr(ui, 'hidden', ui.hidden + 1)
    return ui


def _make_call_bar():
    bar = call_ui.CallBar.__new__(call_ui.CallBar)
    bar._label = FakeLabel('In call')
    bar._shown = False
    bar.presented = 0
    bar.hidden = 0
    bar.present = lambda: setattr(bar, 'presented', bar.presented + 1)
    bar.hide = lambda: setattr(bar, 'hidden', bar.hidden + 1)
    return bar


class TestWindowContract:
    def test_call_bar_has_show_hide_bar_used_by_window(self):
        sig = inspect.signature(call_ui.CallBar.show_bar)
        assert list(sig.parameters) == ['self', 'number']
        assert list(inspect.signature(call_ui.CallBar.hide_bar).parameters) == ['self']

    def test_show_incoming_still_takes_caller_and_number(self):
        sig = inspect.signature(call_ui.CallUI.show_incoming)
        assert list(sig.parameters)[:3] == ['self', 'caller', 'number']

    def test_default_fns_delegate_to_modem_monitor(self, monkeypatch):
        seen = []
        monkeypatch.setattr(modem_monitor, 'accept_call',
                            lambda p: seen.append(('accept', p)) or True)
        monkeypatch.setattr(modem_monitor, 'hangup_call',
                            lambda p: seen.append(('hangup', p)) or False)
        assert call_ui._default_accept('/mm1/Call/1') is True
        assert call_ui._default_hangup('/mm1/Call/2') is False
        assert seen == [('accept', '/mm1/Call/1'), ('hangup', '/mm1/Call/2')]

    def test_resolve_call_path_reads_monitor(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, '_last_monitor', None)
        assert call_ui._resolve_call_path() is None


class TestAcceptFlow:
    def test_accept_success_transitions_and_stops_sound(self):
        paths = []
        ui = _make_call_ui(accept_fn=lambda p: paths.append(p) or True)
        ui.show_incoming('Mom', '+15550001111', '/org/freedesktop/ModemManager1/Call/3')
        ui._on_accept_clicked(None)
        assert paths == ['/org/freedesktop/ModemManager1/Call/3']
        assert ui._events == ['accept']
        assert ui._stack.visible == 'active'
        assert ui.hidden == 0

    def test_show_incoming_resolves_path_from_monitor(self, monkeypatch):
        class StubMonitor:
            _active_call_path = '/org/freedesktop/ModemManager1/Call/9'
        monkeypatch.setattr(modem_monitor, '_last_monitor', StubMonitor())
        ui = _make_call_ui()
        ui.show_incoming('', '+15550002222')
        assert ui._incoming_call_path == '/org/freedesktop/ModemManager1/Call/9'
        assert ui._inc_caller.get_text() == '+15550002222'

    def test_accept_failure_stays_incoming(self):
        ui = _make_call_ui(accept_fn=lambda p: False)
        ui.show_incoming('Mom', '+15550001111')
        ui._on_accept_clicked(None)
        assert 'accept' not in ui._events
        assert ui._stack.visible == 'incoming'
        assert ui.presented >= 1

    def test_accept_without_path_never_calls_transport(self):
        called = []
        ui = _make_call_ui(accept_fn=called.append)
        ui._incoming_call_path = None
        ui._on_accept_clicked(None)
        assert called == []
        assert ui._events == []


class TestDeclineFlow:
    def test_decline_success_hides_and_stops_sound(self):
        paths = []
        ui = _make_call_ui(hangup_fn=lambda p: paths.append(p) or True)
        ui._on_decline_clicked(None)
        assert paths == ['/org/freedesktop/ModemManager1/Call/3']
        assert ui._events == ['decline']
        assert ui.hidden == 1
        assert ui._incoming_call_path is None

    def test_decline_failure_keeps_surface(self):
        ui = _make_call_ui(hangup_fn=lambda p: False)
        ui._on_decline_clicked(None)
        assert ui._events == []
        assert ui.hidden == 0
        assert ui._incoming_call_path == '/org/freedesktop/ModemManager1/Call/3'


class TestHangupFlow:
    def test_hangup_success_ends_call(self, monkeypatch):
        monkeypatch.setattr(call_ui, '_set_audio_route', lambda earpiece: None)
        paths = []
        ui = _make_call_ui(hangup_fn=lambda p: paths.append(p) or True)
        ui._on_hangup_clicked(None)
        assert paths == ['/org/freedesktop/ModemManager1/Call/3']
        assert ui._events == ['hangup']
        assert ui.hidden == 1
        assert ui._incoming_call_path is None

    def test_hangup_failure_stays_active(self):
        ui = _make_call_ui(hangup_fn=lambda p: False)
        ui._on_hangup_clicked(None)
        assert ui._events == []
        assert ui.hidden == 0


class TestCallBar:
    def test_show_bar_renders_number_once(self):
        bar = _make_call_bar()
        bar.show_bar('+15551230000')
        assert bar._label.get_text() == '+15551230000'
        assert bar.presented == 1
        bar.show_bar('+15551230000')
        assert bar.presented == 1

    def test_show_bar_updates_text_while_shown(self):
        bar = _make_call_bar()
        bar.show_bar('111')
        bar.show_bar('222')
        assert bar._label.get_text() == '222'
        assert bar.presented == 1

    def test_hide_bar_idempotent(self):
        bar = _make_call_bar()
        bar.hide_bar()
        assert bar.hidden == 0
        bar.show_bar('555')
        bar.hide_bar()
        bar.hide_bar()
        assert bar.hidden == 1
        assert bar._shown is False

    def test_show_after_hide_presents_again(self):
        bar = _make_call_bar()
        bar.show_bar('555')
        bar.hide_bar()
        bar.show_bar('556')
        assert bar.presented == 2
        assert bar._label.get_text() == '556'

    def test_empty_number_falls_back_to_label(self):
        bar = _make_call_bar()
        bar.show_bar('')
        assert bar._label.get_text() == 'In call'
