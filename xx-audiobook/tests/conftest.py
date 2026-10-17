"""Shared test setup: package sources importable without PYTHONPATH."""

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_xdg(tmp_path_factory, monkeypatch):
    """Never read the developer's real HOME or ~/.config/xx-wm theme."""
    home = tmp_path_factory.mktemp("home")
    xdg = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
