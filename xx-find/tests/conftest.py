"""Shared test bootstrap: import xx_find from src/, never the system bus,
network, GPS or keyring."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _forbid(*args, **kwargs):
    raise AssertionError("test must not touch the network")


def _no_bus(*args, **kwargs):
    raise AssertionError("test must not touch D-Bus")


@pytest.fixture(autouse=True)
def _no_real_world(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "socket", _forbid)
    monkeypatch.setattr(socket, "create_connection", _forbid)
    yield
