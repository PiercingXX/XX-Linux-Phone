"""Tests for the 20.4 session-start app preload gate.

Preloading warms swipe-bound apps into RAM so a gesture opens a resident
process instead of cold-starting it. It is counter to the minimalism
directive on weak hardware, so it is opt-in (``preload_gesture_apps``,
default ``false``) AND only takes effect in a real XX-WM session (never under
a host shell over Phosh).

The gate decision lives in ``config.should_preload_gesture_apps``, which
``main.py``'s ``_show_shell`` calls at the real call site. main.py imports
GTK at module level and cannot load headlessly, so the wiring is asserted by
reading the call site text; the decision function itself is exercised
directly.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import DEFAULT_CONFIG, ShellConfig, should_preload_gesture_apps

MAIN_PY = Path(__file__).parent.parent / 'launcher' / 'src' / 'main.py'


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # ShellConfig() reads ~/.config/xx-wm/config.json in its constructor;
    # point HOME at a temp dir so the dev box's real shell config never leaks.
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))


def _config_with(preload: bool) -> ShellConfig:
    config = ShellConfig()
    config.set_preload_gesture_apps(preload)
    return config


def test_default_is_off():
    """The key must default to false — preloading is opt-in."""
    assert DEFAULT_CONFIG['preload_gesture_apps'] is False
    assert ShellConfig().preload_gesture_apps is False


def test_property_reflects_config():
    assert _config_with(True).preload_gesture_apps is True
    assert _config_with(False).preload_gesture_apps is False


def test_gate_requires_real_session():
    """Outside a real XX-WM session the preload never runs, even if enabled."""
    assert should_preload_gesture_apps(_config_with(True), real_session=False) is False


def test_gate_requires_opt_in():
    """In a real session the preload still needs the config key set."""
    assert should_preload_gesture_apps(_config_with(False), real_session=True) is False


def test_gate_allows_only_session_and_opt_in():
    """Both conditions together are the sole path to preloading."""
    assert should_preload_gesture_apps(_config_with(True), real_session=True) is True


def test_gate_wired_into_main_call_site():
    """main.py's _show_shell must route the preload through the gate function
    (not a bare env check), so the running shell actually honors the key."""
    src = MAIN_PY.read_text(encoding='utf-8')
    assert 'should_preload_gesture_apps' in src
    assert 'self._shell.config' in src
    assert 'preload_gesture_apps' in src