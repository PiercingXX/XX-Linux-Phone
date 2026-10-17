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
WRAPPER_IN = Path(__file__).parent.parent / 'launcher' / 'data' / 'xx-wm.in'
WRAPPER = WRAPPER_IN


def test_power_button_menu_gets_live_config():
    """The application-owned PowerMenu (the single instance: window.py's
    shade route reuses it) threads the same live ShellConfig object the
    window owns, matching window.py's own construction idiom, and locks
    through the shell's lock screen callback."""
    src = MAIN_PY.read_text(encoding='utf-8')
    assert 'PowerMenu(config=self._shell.config' in src
    assert 'on_lock=self._shell._show_lock_screen' in src


def test_no_bare_power_menu_construction_remains():
    """Every PowerMenu construction in main.py passes a config; the snapshot-
    theming no-arg form must not come back."""
    src = MAIN_PY.read_text(encoding='utf-8')
    assert 'PowerMenu()' not in src


def test_wrapper_supervises_main_py():
    """phoc -E must keep the wrapper as its client; the wrapper respawns the
    python child instead of execing it."""
    src = WRAPPER_IN.read_text(encoding='utf-8')
    assert 'while' in src
    assert 'trap' in src
    assert 'TERM' in src
    assert 'exec @python@' not in src


def test_wrapper_respawns_on_sigusr1():
    """Deploy SIGUSR1s the python child; wait status 138 respawns at once."""
    src = WRAPPER_IN.read_text(encoding='utf-8')
    assert '138' in src
    assert '137' in src


def test_main_py_has_no_execv_restart():
    """Restart is the wrapper's job; main.py must not execv/SIGHUP itself."""

def test_wrapper_supervises_python_instead_of_exec():
    src = WRAPPER.read_text(encoding='utf-8')
    assert 'while :;' in src
    assert 'wait "$_child"' in src
    # SIGUSR1 (138) respawns at once for deploy; SIGKILL (137) backs off
    # like any other crash instead of tight-looping under the OOM killer.
    assert '[ "$_st" -eq 138 ]' in src
    assert '[ "$_st" -eq 138 ] || [ "$_st" -eq 137 ]' not in src
    assert '137' in src
    assert '[ "$_st" -eq 0 ]' in src and 'exit 0' in src
    assert 'exec @python@' not in src


def test_wrapper_stops_when_compositor_dies():
    """The wrapper is phoc's client: a respawn after phoc died would
    attach to the next login's compositor, so every pass checks the
    parent is still alive first."""
    src = WRAPPER.read_text(encoding='utf-8')
    assert '_phoc=$PPID' in src
    assert 'kill -0 "$_phoc"' in src


def test_main_does_not_restart_via_execv_or_sighup():
    src = MAIN_PY.read_text(encoding='utf-8')
    assert 'os.execv' not in src
    assert 'SIGHUP' not in src


def test_config_migrate_runs_before_lisgd():
    """First login must migrate piercing-shell before lisgd reads gestures."""
    src = WRAPPER_IN.read_text(encoding='utf-8')
    assert 'piercing-shell' in src
    assert 'mv' in src
    migrate_at = src.find('piercing-shell')
    lisgd_at = src.find('lisgd')
    bindings_at = src.find('gesture_bindings.py')
    assert migrate_at != -1 and lisgd_at != -1
    assert migrate_at < lisgd_at
    assert migrate_at < bindings_at

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
