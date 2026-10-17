"""Shared test setup: the xx_calendar package is importable headless."""
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_xdg_config(tmp_path_factory, monkeypatch):
    """Never read the developer's real ~/.config/xx-wm theme."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
