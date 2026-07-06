from __future__ import annotations

import os
import socket
from collections.abc import Callable
from pathlib import Path

from gi.repository import GLib


def _socket_path() -> str:
    runtime = os.environ.get('XDG_RUNTIME_DIR', str(Path.home() / '.local' / 'share' / 'piercing-shell'))
    return os.path.join(runtime, 'piercing-shell.sock')


class IPCServer:
    """
    Lightweight Unix-socket IPC server. Line-based protocol:
      lock | unlock | shade.show | shade.hide | switcher.show | switcher.hide
    Other surfaces or external scripts (e.g. wake hook) connect, send one line, disconnect.
    """

    def __init__(self, handler: Callable[[str], None]) -> None:
        self._handler = handler
        self._sock: socket.socket | None = None
        self._start()

    def _start(self) -> None:
        path = _socket_path()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(path)
        sock.listen(8)
        sock.setblocking(False)
        self._sock = sock

        GLib.io_add_watch(sock.fileno(), GLib.IOCondition.IN, self._on_incoming, sock)

    def _on_incoming(self, _fd: int, _condition: GLib.IOCondition, srv: socket.socket) -> bool:
        try:
            conn, _ = srv.accept()
            data = conn.recv(256).decode('utf-8', errors='replace').strip()
            conn.close()
            if data:
                GLib.idle_add(self._dispatch, data)
        except OSError:
            pass
        return True

    def _dispatch(self, command: str) -> bool:
        try:
            self._handler(command)
        except Exception:
            pass
        return False

    def stop(self) -> None:
        if self._sock:
            try:
                self._sock.close()
                os.unlink(_socket_path())
            except OSError:
                pass
            self._sock = None


def ipc_send(command: str) -> None:
    """Send a command to the running shell IPC server. No-op if server isn't up."""
    path = _socket_path()
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        sock.connect(path)
        sock.send(command.encode('utf-8'))
        sock.close()
    except OSError:
        pass
