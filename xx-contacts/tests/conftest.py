"""Shared test setup: package importable, HOME never the real one."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".local" / "share"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))


@pytest.fixture(autouse=True)
def _pin_display_country(monkeypatch):
    """Display tests pin to the passed region: live network/SIM/locale
    resolution is covered by common/tests/test_xx_phone.py."""
    import xx_phone

    monkeypatch.setattr(xx_phone, "user_country", lambda *a, **k: "")


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
