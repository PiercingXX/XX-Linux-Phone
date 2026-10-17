"""Shared test setup: package sources importable without PYTHONPATH."""

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
sys.path.insert(0, str(Path(__file__).parent))


import pytest  # noqa: E402

from fake_server import FakeServer  # noqa: F401
from fake_server import fake_server  # noqa: F401


@pytest.fixture(autouse=True)
def _isolated_xdg(tmp_path_factory, monkeypatch):
    """Never read the developer's real HOME or ~/.config/xx-wm theme."""
    home = tmp_path_factory.mktemp("home")
    xdg = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch):
    """Tests never touch the network beyond the loopback fake server."""
    import socket

    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection

    def _allowed(address) -> bool:
        if isinstance(address, bytes):
            return False
        if not isinstance(address, tuple):
            return True
        host = address[0]
        return host in _LOOPBACK_HOSTS

    def guarded_connect(self, address):
        if self.family not in (socket.AF_INET, socket.AF_INET6):
            return real_connect(self, address)
        if not _allowed(address):
            raise OSError(
                "network guard: tests may only dial 127.0.0.1, "
                f"got {address[0]!r}")
        return real_connect(self, address)

    def guarded_create_connection(address, *args, **kwargs):
        if not _allowed(address):
            raise OSError(
                "network guard: tests may only dial 127.0.0.1, "
                f"got {address[0]!r}")
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection",
                        guarded_create_connection)
