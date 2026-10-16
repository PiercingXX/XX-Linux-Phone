"""L3 fix-up round 3: the search entry must not autofocus (Broadway).

Regression: ``set_focusable(False)`` left the ``SearchEntry``'s inner
``GtkText`` focusable, so launch and ``Adw.NavigationView`` pop focused
it and popped the on-screen keyboard. Each test runs one scenario of
``tests/broadway_focus_driver.py`` in a fresh process against a real
GTK display via ``gtk4-broadwayd`` (a subprocess keeps this suite's own
process — whose GTK modules may already be imported — from pinning the
backend first) and skips when the daemon binary is missing.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_DRIVER = str(Path(__file__).parent / "broadway_focus_driver.py")


def _socket_path(display: str) -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return os.path.join(runtime, f"broadway{int(display[1:]) + 1}.socket")


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _display_live(display: str) -> bool:
    path = _socket_path(display)
    if not os.path.exists(path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(path)
    except OSError:
        return False
    return True


@pytest.fixture(scope="module")
def broadway_env():
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    for number in range(23, 40):
        display = f":{number}"
        if _display_live(display):
            continue
        try:
            os.unlink(_socket_path(display))
        except OSError:
            pass
        proc = subprocess.Popen(
            ["gtk4-broadwayd", "--port", str(_free_tcp_port()), display],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(200):
            if os.path.exists(_socket_path(display)):
                break
            time.sleep(0.05)
        else:
            proc.terminate()
            continue
        env = dict(os.environ)
        env["GDK_BACKEND"] = "broadway"
        env["BROADWAY_DISPLAY"] = display
        try:
            yield env
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        return
    pytest.skip("no free broadway display")


def _run_scenario(env: dict, name: str) -> dict:
    completed = subprocess.run(
        [sys.executable, _DRIVER, name],
        env=env, capture_output=True, text=True, timeout=90,
    )
    assert completed.returncode == 0, (
        f"driver {name} failed: {completed.stderr[-2000:]}")
    markers = {}
    for line in completed.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers, f"driver {name} printed no markers"
    return markers


def test_startup_search_entry_takes_no_focus(broadway_env):
    markers = _run_scenario(broadway_env, "startup")
    assert markers["STARTUP_CAN_FOCUS"] == "False"
    assert markers["STARTUP_INSIDE"] == "False"


def test_tap_puts_caret_into_entry_text(broadway_env):
    markers = _run_scenario(broadway_env, "tap")
    assert markers["TAP_BEFORE"] == "False"
    assert markers["TAP_CAN_FOCUS"] == "True"
    assert markers["TAP_INSIDE"] == "True"
    chain = markers["TAP_CHAIN"].split(">")
    assert "SearchEntry" in chain
    assert chain[0] == "Text"


def test_focus_elsewhere_locks_entry_again(broadway_env):
    markers = _run_scenario(broadway_env, "relock")
    assert markers["RELOCK_TAPPED_INSIDE"] == "True"
    assert markers["RELOCK_INSIDE"] == "False"
    assert markers["RELOCK_CAN_FOCUS"] == "False"


def test_nav_pop_does_not_refocus_entry(broadway_env):
    markers = _run_scenario(broadway_env, "navpop")
    assert markers["NAVPOP_STARTUP_INSIDE"] == "False"
    assert markers["NAVPOP_TAPPED_INSIDE"] == "True"
    assert markers["NAVPOP_AFTER_POP_INSIDE"] == "False"
