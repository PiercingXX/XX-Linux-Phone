"""Shared test setup: sources importable, XDG isolated, network off.

Tests never touch the network: every engine test uses the fake engine or
recorded fixtures. The guard below fails any attempt to open an IP socket
(unix sockets stay allowed for D-Bus and the Broadway display).
"""

from pathlib import Path

import socket
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))


import pytest  # noqa: E402

_IP_FAMILIES = {socket.AF_INET, socket.AF_INET6}


@pytest.fixture(autouse=True)
def _isolated_xdg(tmp_path_factory, monkeypatch):
    """Never read or write the developer's real HOME, theme or caches."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    for var, sub in (("XDG_CONFIG_HOME", ".config"),
                     ("XDG_DATA_HOME", ".local/share"),
                     ("XDG_CACHE_HOME", ".cache"),
                     ("XDG_STATE_HOME", ".local/state")):
        monkeypatch.setenv(var, str(home / sub))
    monkeypatch.delenv("XX_RADIO_ENGINE", raising=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guard(sock, *args, real=real_connect):
        if sock.family in _IP_FAMILIES:
            raise AssertionError("tests must not use the network")
        return real(sock, *args)

    def guard_ex(sock, *args):
        return guard(sock, *args, real=real_connect_ex)

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard_ex)
