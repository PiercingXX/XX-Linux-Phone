"""SMS send pipeline (P2-D).

--messaging-create-sms only CREATES the message; the returned SMS object
must then be sent with ``-s <path> --send`` (upstream mmcli has no
--messaging-send-sms verb; verified against cli/mmcli-common.c and the
mmcli(8) examples). Payload values are single-quoted and sanitized
because mmcli splits key=value pairs on commas outside quotes and its
scanner cannot carry control chars or embedded quotes.
"""
import subprocess
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

    class FakeLabel:
        def __init__(self, label='', wrap=False, xalign=0, **k):
            self.text = label
            self.wrap = wrap
            self.xalign = xalign
            self.classes = []

        def add_css_class(self, css_class):
            self.classes.append(css_class)

    class FakeBox:
        def __init__(self, **k):
            self.children = []

        def append(self, child):
            self.children.append(child)

    gtk = types.ModuleType('gi.repository.Gtk')
    gtk.Window = type('Window', (), {})
    gtk.Label = FakeLabel
    gtk.Box = FakeBox
    gtk.Orientation = types.SimpleNamespace(VERTICAL=0, HORIZONTAL=1)

    gdk = types.ModuleType('gi.repository.Gdk')
    gdk.Display = types.SimpleNamespace(get_default=lambda: None)

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace != 'Gtk':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib
    sys.modules['gi.repository.Gdk'] = gdk
    sys.modules['gi.repository.Gtk'] = gtk


_install_fake_gi()

import sms  # noqa: E402  (needs fake gi modules installed first)


class SyncThread:
    def __init__(self, target=None, args=(), daemon=False):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


class FakeMsgList:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)


def _make_conversation(monkeypatch, number='+15550001111'):
    conv = sms.SMSConversation.__new__(sms.SMSConversation)
    conv._number = number
    conv._messages = []
    conv._msg_list = FakeMsgList()
    monkeypatch.setattr(sms.threading, 'Thread', SyncThread)
    return conv


def _script_mmcli(monkeypatch, responses):
    calls = []

    def _run(args):
        calls.append(list(args))
        ok, out = responses.pop(0)
        return ok, out

    monkeypatch.setattr(sms, '_run_mmcli', _run)
    return calls


def _flush_idle(monkeypatch):
    glib = sms.GLib
    pending = []

    def idle_add(cb, *args):
        pending.append((cb, args))
        return len(pending)
    monkeypatch.setattr(glib, 'idle_add', idle_add)
    return lambda: [cb(*args) for cb, args in list(pending)]


_SMS_OK = ('Successfully created new SMS:\n'
           '/org/freedesktop/ModemManager1/SMS/22 (unknown)\n')


class TestSendArgv:
    def test_create_then_send_with_parsed_path(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        conv = _make_conversation(monkeypatch)
        calls = _script_mmcli(monkeypatch, [
            (True, _SMS_OK),
            (True, 'successfully sent the SMS\n'),
        ])
        msg = sms.Message('hello, world', outgoing=True)
        conv._deliver(msg, '+15550001111')
        assert calls == [
            ['-m', '0',
             "--messaging-create-sms=number='+15550001111',text='hello, world'"],
            ['-m', '0', '-s', '/org/freedesktop/ModemManager1/SMS/22', '--send'],
        ]
        flush()
        assert msg.failed is False

    def test_comma_and_newline_text_stays_intact_inside_quotes(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        conv = _make_conversation(monkeypatch)
        calls = _script_mmcli(monkeypatch, [(True, _SMS_OK), (True, 'sent\n')])
        msg = sms.Message("line1\nline2, still one message", outgoing=True)
        conv._deliver(msg, '+15550001111')
        payload = calls[0][2]
        assert payload.startswith('--messaging-create-sms=')
        kv = payload.split('=', 1)[1]
        assert '\n' not in kv
        assert "number='+15550001111'" in kv
        assert "text='line1 line2, still one message'" in kv
        flush()
        assert msg.failed is False

    def test_apostrophe_swapped_to_keep_quoting(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        conv = _make_conversation(monkeypatch)
        calls = _script_mmcli(monkeypatch, [(True, _SMS_OK), (True, 'sent\n')])
        msg = sms.Message("it's alive", outgoing=True)
        conv._deliver(msg, '+15550001111')
        kv = calls[0][2].split('=', 1)[1]
        assert "'" not in sms._payload_value("it's")
        assert "text='it\u2019s alive'" in kv
        flush()
        assert msg.failed is False

    def test_create_failure_never_attempts_send(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        conv = _make_conversation(monkeypatch)
        calls = _script_mmcli(monkeypatch, [
            (False, "error: couldn't find the ModemManager process\n"),
        ])
        msg = sms.Message('hi', outgoing=True)
        conv._deliver(msg, '+15550001111')
        assert len(calls) == 1
        flush()
        assert msg.failed is True

    def test_create_success_without_path_marks_failed(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        conv = _make_conversation(monkeypatch)
        calls = _script_mmcli(monkeypatch, [(True, 'unexpected output\n')])
        msg = sms.Message('hi', outgoing=True)
        conv._deliver(msg, '+15550001111')
        assert len(calls) == 1
        flush()
        assert msg.failed is True

    def test_send_failure_marks_message_failed(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        conv = _make_conversation(monkeypatch)
        rebuilds = []
        conv._rebuild_messages = lambda: rebuilds.append(1)
        _script_mmcli(monkeypatch, [
            (True, _SMS_OK),
            (False, 'error: modem is busy\n'),
        ])
        msg = sms.Message('hi', outgoing=True)
        conv._messages.append(msg)
        conv._deliver(msg, '+15550001111')
        flush()
        assert msg.failed is True
        assert rebuilds == [1]

    def test_mark_sent_ignores_cleared_conversation(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        conv = _make_conversation(monkeypatch)
        rebuilds = []
        conv._rebuild_messages = lambda: rebuilds.append(1)
        msg = sms.Message('stale', outgoing=True)
        conv._mark_sent(msg, False)
        flush()
        assert msg.failed is True
        assert rebuilds == []


class TestBubbleState:
    def test_failed_bubble_renders_not_sent(self, monkeypatch):
        conv = _make_conversation(monkeypatch)
        msg = sms.Message('nope', outgoing=True, failed=True)
        conv._append_bubble(msg)
        row = conv._msg_list.rows[0]
        bubble, stamp = row.children
        assert 'bubble-out' in bubble.classes
        assert 'bubble-failed' in bubble.classes
        assert stamp.text.endswith('· not sent')

    def test_sent_bubble_has_no_failure_marker(self, monkeypatch):
        conv = _make_conversation(monkeypatch)
        msg = sms.Message('fine', outgoing=True)
        conv._append_bubble(msg)
        row = conv._msg_list.rows[0]
        bubble, stamp = row.children
        assert 'bubble-failed' not in bubble.classes
        assert 'not sent' not in stamp.text


class TestPayloadValue:
    def test_control_chars_flattened_to_space(self):
        assert sms._payload_value('a\r\nb\tc') == 'a b c'

    def test_plain_text_untouched(self):
        assert sms._payload_value('plain text') == 'plain text'


class TestRunMmcli:
    def test_missing_binary_reported(self, monkeypatch):
        def _boom(*a, **k):
            raise FileNotFoundError('mmcli')
        monkeypatch.setattr(sms.subprocess, 'run', _boom)
        assert sms._run_mmcli(['-m', '0']) == (False, 'mmcli not installed')

    def test_nonzero_returns_stderr(self, monkeypatch):
        proc = subprocess.CompletedProcess(['mmcli'], 3, stdout='', stderr='denied\n')
        monkeypatch.setattr(sms.subprocess, 'run', lambda *a, **k: proc)
        assert sms._run_mmcli([]) == (False, 'denied')


class TestMessageDefaults:
    def test_new_message_not_failed(self):
        msg = sms.Message('x', outgoing=True)
        assert msg.failed is False
        assert msg.outgoing is True
