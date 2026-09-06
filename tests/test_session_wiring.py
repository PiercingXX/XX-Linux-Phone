"""Session-start wiring checks for main.py (W1-C).

``_show_shell`` constructs a second ``PowerMenu`` for the hardware
power-button path (DisplayManager long-press), separate from the one
window.py builds lazily for its own route. Both must receive the shell
window's LIVE ``ShellConfig``: a no-arg construction snapshots its own
``ShellConfig()`` while the window-owned surfaces share the reloaded
config object (WS27 surface idiom).

main.py imports GTK at module level and cannot load headlessly, so the
wiring is asserted by reading the call-site text (test_preload_gate
idiom).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

MAIN_PY = Path(__file__).parent.parent / 'launcher' / 'src' / 'main.py'
WRAPPER = Path(__file__).parent.parent / 'launcher' / 'data' / 'xx-wm.in'


def test_power_button_menu_gets_live_config():
    """The hardware power-button PowerMenu threads the same live ShellConfig
    object the window owns, matching window.py's own construction idiom."""
    src = MAIN_PY.read_text(encoding='utf-8')
    assert 'PowerMenu(config=self._shell.config)' in src


def test_no_bare_power_menu_construction_remains():
    """Every PowerMenu construction in main.py passes a config; the snapshot-
    theming no-arg form must not come back."""
    src = MAIN_PY.read_text(encoding='utf-8')
    assert 'PowerMenu()' not in src


def test_wrapper_supervises_python_instead_of_exec():
    src = WRAPPER.read_text(encoding='utf-8')
    assert 'while' in src
    assert 'trap' in src
    assert 'TERM' in src
    assert '138' in src
    assert '137' in src
    assert 'exec @python@' not in src


def test_main_does_not_restart_via_execv_or_sighup():
    src = MAIN_PY.read_text(encoding='utf-8')
    assert 'os.execv' not in src
    assert 'SIGHUP' not in src


def test_wrapper_migrates_piercing_shell_before_lisgd():
    src = WRAPPER.read_text(encoding='utf-8')
    mv_at = src.find('mv "$_legacy" "$_xxcfg"')
    lisgd_at = src.find('gesture_bindings.py --bindir')
    assert mv_at != -1
    assert lisgd_at != -1
    assert mv_at < lisgd_at


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
