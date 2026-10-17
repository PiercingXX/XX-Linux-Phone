"""Shared pytest setup: put src/ and ../common on sys.path so the
package and the shared xx_ui design system import without an install.
Headless by construction (no gi, no network)."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))
sys.path.insert(0, str(_HERE.parent.parent / "common"))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_xdg_config(tmp_path_factory, monkeypatch):
    """Never read the developer's real ~/.config/xx-wm theme."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
