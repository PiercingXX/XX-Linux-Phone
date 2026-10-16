"""Shared test setup: package importable, HOME never the real one."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest  # noqa: E402


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def shell_config_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"theme": "forest", "font": "space-mono"}',
                    encoding="utf-8")
    return str(path)
