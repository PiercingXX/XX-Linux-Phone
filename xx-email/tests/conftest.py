"""Shared test setup: make src/ importable without installing."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent /
                       "common"))


@pytest.fixture(autouse=True)
def _hermetic_env(tmp_path, monkeypatch):
    """Keep every test off the real machine.

    Tests must never touch the owner's login keyring, session bus,
    network, or home directory. Point HOME/XDG dirs at per-test tmp
    dirs and aim the session bus at a path that cannot exist, so a
    Secret Service client genuinely cannot connect even when one is
    installed.
    """
    home = tmp_path / "home"
    run_dir = tmp_path / "run"
    config = tmp_path / "config"
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    state = tmp_path / "state"
    for directory in (home, run_dir, config, data, cache, state):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run_dir))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS",
                       "unix:path=/nonexistent-bus-for-tests")
