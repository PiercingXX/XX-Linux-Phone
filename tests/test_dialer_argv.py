"""Dialer mmcli argv contract (P2-D).

Dialing must be a two-step mmcli sequence — create the call object, then
start it via the returned object path selected with ``-o`` (upstream
mmcli has no ``--voice-call`` verb; verified against cli/mmcli-common.c
and the mmcli(8) CALL OPTIONS section). Failures surface in the dialer
status label instead of being discarded.
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
    glib.idle_add = lambda *a, **k: None
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None

    gdk = types.ModuleType('gi.repository.Gdk')
    gdk.Display = types.SimpleNamespace(get_default=lambda: None)

    gtk = types.ModuleType('gi.repository.Gtk')
    gtk.Window = type('Window', (), {})

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

import dialer  # noqa: E402  (needs fake gi modules installed first)


class FakeLabel:
    def __init__(self, text=''):
        self.text = text

    def set_text(self, text):
        self.text = text

    def get_text(self):
        return self.text


class SyncThread:
    """Runs the worker inline so tests are deterministic."""

    def __init__(self, target=None, args=(), daemon=False):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


def _make_dialer(monkeypatch):
    d = dialer.Dialer.__new__(dialer.Dialer)
    d._digits = '5551234567'
    d._status = FakeLabel()
    monkeypatch.setattr(dialer.threading, 'Thread', SyncThread)
    return d


def _script_mmcli(monkeypatch, responses):
    calls = []

    def _run(args):
        calls.append(list(args))
        ok, out = responses.pop(0)
        return ok, out

    monkeypatch.setattr(dialer, '_run_mmcli', _run)
    return calls


def _flush_idle(monkeypatch):
    glib = dialer.GLib
    pending = []

    def idle_add(cb, *args):
        pending.append((cb, args))
        return len(pending)
    monkeypatch.setattr(glib, 'idle_add', idle_add)
    return lambda: [cb(*args) for cb, args in list(pending)]


class TestDialArgv:
    def test_create_then_start_sequence(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        d = _make_dialer(monkeypatch)
        calls = _script_mmcli(monkeypatch, [
            (True, 'Successfully created new call: /org/freedesktop/ModemManager1/Call/1\n'),
            (True, 'successfully started the call\n'),
        ])
        d._initiate_call('5551234567')
        assert calls == [
            ['-m', '0', '--voice-create-call=number=5551234567'],
            ['-m', '0', '-o', '/org/freedesktop/ModemManager1/Call/1', '--start'],
        ]
        flush()
        assert d._status.text == 'Dialing…'

    def test_create_failure_surfaces_stderr(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        d = _make_dialer(monkeypatch)
        calls = _script_mmcli(monkeypatch, [
            (False, "error: couldn't find modem\n"),
        ])
        d._initiate_call('5551234567')
        assert len(calls) == 1
        flush()
        assert "Call failed: error: couldn't find modem" in d._status.text

    def test_missing_call_path_surfaces(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        d = _make_dialer(monkeypatch)
        _script_mmcli(monkeypatch, [(True, 'unexpected output\n')])
        d._initiate_call('5551234567')
        flush()
        assert d._status.text == 'Call failed: modem returned no call path'

    def test_start_failure_surfaces(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        d = _make_dialer(monkeypatch)
        _script_mmcli(monkeypatch, [
            (True, '/org/freedesktop/ModemManager1/Call/2\n'),
            (False, 'error: call state change failed\n'),
        ])
        d._initiate_call('5551234567')
        flush()
        assert d._status.text == 'Call failed: error: call state change failed'

    def test_missing_binary_degrades_gracefully(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        d = _make_dialer(monkeypatch)

        def _boom(*a, **k):
            raise FileNotFoundError('mmcli')

        monkeypatch.setattr(dialer.subprocess, 'run', _boom)
        d._initiate_call('5551234567')
        flush()
        assert d._status.text == 'Call failed: mmcli not installed'


class TestRunMmcli:
    def test_success_returns_stdout(self, monkeypatch):
        proc = subprocess.CompletedProcess(['mmcli'], 0,
                                           stdout='ok path /org/x\n', stderr='')
        monkeypatch.setattr(dialer.subprocess, 'run', lambda *a, **k: proc)
        assert dialer._run_mmcli(['-m', '0']) == (True, 'ok path /org/x\n')

    def test_nonzero_returns_stderr_detail(self, monkeypatch):
        proc = subprocess.CompletedProcess(['mmcli'], 4, stdout='', stderr='boom\n')
        monkeypatch.setattr(dialer.subprocess, 'run', lambda *a, **k: proc)
        assert dialer._run_mmcli(['-m', '0']) == (False, 'boom')

    def test_nonzero_without_stderr_reports_status(self, monkeypatch):
        proc = subprocess.CompletedProcess(['mmcli'], 9, stdout='', stderr='')
        monkeypatch.setattr(dialer.subprocess, 'run', lambda *a, **k: proc)
        assert dialer._run_mmcli([]) == (False, 'mmcli exited with status 9')


class TestStatusLabelWiring:
    def test_calling_status_visible_during_worker(self, monkeypatch):
        flush = _flush_idle(monkeypatch)
        d = _make_dialer(monkeypatch)
        seen_at_call = []

        def _run(args):
            seen_at_call.append(d._status.text)
            return True, '/org/freedesktop/ModemManager1/Call/1'

        monkeypatch.setattr(dialer, '_run_mmcli', _run)
        d._initiate_call('5551234567')
        assert seen_at_call[0] == 'Calling (555) 123-4567…'
        flush()
        assert d._status.text == 'Dialing…'
