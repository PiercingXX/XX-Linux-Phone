from __future__ import annotations

import os
import socket
from collections.abc import Callable
from pathlib import Path

from gi.repository import GLib

from shell_log import get_logger

_log = get_logger('ipc')

# A client that connects but never sends must not stall the GLib main loop
# (the watch callback runs on the UI thread), so reads are bounded.
CONN_TIMEOUT_S = 0.25


def _socket_path() -> str:
    runtime = os.environ.get('XDG_RUNTIME_DIR', str(Path.home() / '.local' / 'share' / 'xx-wm'))
    return os.path.join(runtime, 'xx-wm.sock')


class IPCServer:
    """
    Lightweight Unix-socket IPC server. Line-based protocol:
      lock | shade.show | shade.hide | switcher.show | switcher.hide |
      gesture.back | gesture.home | gesture.shade | gesture.keyboard |
      gesture.switcher | welcome
    There is deliberately no unlock verb: the socket must never bypass the
    lock screen. Other surfaces or external scripts (e.g. wake hook) connect,
    send one line, disconnect.
    """

    def __init__(self, handler: Callable[[str], None]) -> None:
        self._handler = handler
        self._sock: socket.socket | None = None
        self._watch_id: int | None = None
        self._start()

    def _start(self) -> None:
        path = _socket_path()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(path)
        # Default umask would leave the socket traversable by other users.
        os.chmod(path, 0o600)
        sock.listen(8)
        sock.setblocking(False)
        self._sock = sock

        self._watch_id = GLib.io_add_watch(
            sock.fileno(), GLib.IOCondition.IN, self._on_incoming, sock)

    def _on_incoming(self, _fd: int, _condition: GLib.IOCondition, srv: socket.socket) -> bool:
        try:
            conn, _ = srv.accept()
        except OSError:
            return True
        try:
            conn.settimeout(CONN_TIMEOUT_S)
            data = conn.recv(256).decode('utf-8', errors='replace').strip()
        except OSError:
            data = ''
        finally:
            conn.close()
        if data:
            GLib.idle_add(self._dispatch, data)
        return True

    def _dispatch(self, command: str) -> bool:
        try:
            self._handler(command)
        except Exception:
            _log.exception('ipc handler failed for %r', command)
        return False

    def stop(self) -> None:
        # Remove the watch before closing the fd so no callback can fire on
        # a closed socket; the None reset guards a double stop.
        if self._watch_id:
            GLib.source_remove(self._watch_id)
            self._watch_id = None
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
