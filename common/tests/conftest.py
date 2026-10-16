"""Shared test setup: package importable, HOME never the real one."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    return tmp_path


def write_config(home: Path, payload: dict) -> Path:
    path = home / '.config' / 'xx-wm' / 'config.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    import json
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path
