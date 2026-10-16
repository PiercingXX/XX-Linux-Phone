"""Shared test setup: src/ importable, HOME never the real one."""
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    return home


@pytest.fixture()
def cert_pair(tmp_path):
    from certs import ensure_certpair

    return ensure_certpair(tmp_path / "certs")
