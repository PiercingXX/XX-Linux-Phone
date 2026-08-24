"""IPC server hardening: runtime-dir auto-creation, socket 0600, bounded
reads (silent client cannot stall the loop), and handler-error isolation.
The GLib watch callback is driven manually against a fake GLib, so no main
loop and no real PyGObject are needed."""
import logging
import os
import socket
import stat
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


class _FakeGLib:
    IOCondition = types.SimpleNamespace(IN=1)

    def __init__(self):
        self.pending_idles = []

    def io_add_watch(self, *_args):
        return 1

    def idle_add(self, cb, *args):
        self.pending_idles.append((cb, args))
        return 1

    def drain_idles(self):
        while self.pending_idles:
            cb, args = self.pending_idles.pop(0)
            cb(*args)


_GLIB = _FakeGLib()


def _install_fake_gi():
    glib_mod = types.ModuleType('gi.repository.GLib')
    glib_mod.IOCondition = _GLIB.IOCondition
    glib_mod.io_add_watch = _GLIB.io_add_watch
    glib_mod.idle_add = _GLIB.idle_add

    gi_mod = types.ModuleType('gi')
    gi_mod.require_version = lambda *_a, **_k: None
    gi_rep = types.ModuleType('gi.repository')
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib_mod


_install_fake_gi()

import ipc  # noqa: E402  (needs fake gi modules installed first)


@pytest.fixture(autouse=True)
def _glib():
    _GLIB.pending_idles.clear()
    return _GLIB


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    target = tmp_path / 'runtime'
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(target))
    return target


@pytest.fixture
def make_server(runtime):
    created = []

    def _make(handler):
        srv = ipc.IPCServer(handler)
        created.append(srv)
        return srv

    yield _make
    for srv in created:
        srv.stop()


def _connect_send(path, payload=None):
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(str(path))
    if payload is not None:
        conn.sendall(payload)
    return conn


def _pump(server):
    assert server._on_incoming(server._sock.fileno(), None, server._sock) is True
    _GLIB.drain_idles()


class TestSocketSetup:
    def test_missing_runtime_dir_created_before_bind(self, make_server, runtime):
        assert not runtime.exists()
        srv = make_server(lambda cmd: None)
        try:
            assert runtime.is_dir()
            assert (runtime / 'xx-wm.sock').exists()
        finally:
            srv.stop()

    def test_fallback_dir_created_when_xdg_unset(self, monkeypatch, tmp_path, make_server):
        monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        expected = tmp_path / 'home' / '.local' / 'share' / 'xx-wm' / 'xx-wm.sock'
        srv = make_server(lambda cmd: None)
        try:
            assert expected.exists()
        finally:
            srv.stop()

    def test_socket_mode_is_0600(self, make_server, runtime):
        make_server(lambda cmd: None)
        mode = stat.S_IMODE(os.stat(runtime / 'xx-wm.sock').st_mode)
        assert mode == 0o600


class TestDispatchLoop:
    def test_silent_client_does_not_hang_server(self, make_server, runtime):
        handled = []
        server = make_server(handled.append)

        silent = _connect_send(runtime / 'xx-wm.sock')
        try:
            time.sleep(ipc.CONN_TIMEOUT_S + 0.1)
            started = time.monotonic()
            assert server._on_incoming(server._sock.fileno(), None, server._sock) is True
            elapsed = time.monotonic() - started
            assert elapsed < 2.0
        finally:
            silent.close()
        _GLIB.drain_idles()
        assert handled == []

        client = _connect_send(runtime / 'xx-wm.sock', b'lock\n')
        client.close()
        _pump(server)
        assert handled == ['lock']

    def test_handler_exception_logged_and_server_survives(
        self, make_server, runtime, caplog,
    ):
        handled = []

        def handler(command):
            if command == 'boom':
                raise RuntimeError('kaboom')
            handled.append(command)

        server = make_server(handler)

        client = _connect_send(runtime / 'xx-wm.sock', b'boom\n')
        client.close()
        with caplog.at_level(logging.ERROR, logger='piercing.ipc'):
            _pump(server)
        assert handled == []
        assert any(
            record.levelname == 'ERROR' and 'boom' in record.getMessage()
            for record in caplog.records
        )

        client = _connect_send(runtime / 'xx-wm.sock', b'welcome\n')
        client.close()
        _pump(server)
        assert handled == ['welcome']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
