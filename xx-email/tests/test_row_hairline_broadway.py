"""Broadway hairline test for the mailbox rows (needs a real display).

List rows carry a 1 px hairline except the final one (``last`` drops
it). ``replace_store`` refills incrementally while the ListView binds
eagerly, so a position check inside the bind factory marks every row
last unless the store rebinds once the refill lands. The driver below
renders three rows through the real refill path and counts the last
marks; skips cleanly only when gtk4-broadwayd is missing.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_DRIVER = str(Path(__file__).parent / "row_hairline_driver.py")


def _socket_path(display: str) -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR",
                             f"/run/user/{os.getuid()}")
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
    daemon_runtime = os.environ.get("XDG_RUNTIME_DIR",
                                    f"/run/user/{os.getuid()}")
    for number in range(71, 80):
        display = f":{number}"
        if _display_live(display):
            continue
        try:
            os.makedirs(os.path.dirname(_socket_path(display)),
                        exist_ok=True)
        except OSError:
            pass
        try:
            os.unlink(_socket_path(display))
        except OSError:
            pass
        proc = subprocess.Popen(
            ["gtk4-broadwayd", "--port", str(_free_tcp_port()),
             display],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(200):
            if os.path.exists(_socket_path(display)):
                break
            time.sleep(0.05)
        else:
            proc.terminate()
            continue
        try:
            yield (display, daemon_runtime)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        return
    pytest.skip("no free broadway display")


def _run_driver(broadway: tuple) -> dict:
    display, daemon_runtime = broadway
    env = dict(os.environ)
    env["GDK_BACKEND"] = "broadway"
    env["BROADWAY_DISPLAY"] = display
    env["XDG_RUNTIME_DIR"] = daemon_runtime
    env["PYTHONPATH"] = os.pathsep.join((
        str(Path(__file__).resolve().parent.parent / "src"),
        str(Path(__file__).resolve().parent.parent.parent /
            "common"),
    ))
    completed = subprocess.run(
        [sys.executable, _DRIVER],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, (
        f"hairline driver failed: {completed.stderr[-2000:]}")
    markers = {}
    for line in completed.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers, "hairline driver printed no markers"
    return markers


def test_only_final_row_drops_its_hairline(broadway_env):
    markers = _run_driver(broadway_env)
    assert markers.get("ERRORS") == "none"
    assert markers.get("ROWS") == "3"
    assert markers.get("LASTCOUNT") == "1", \
        "every row counts as last: hairlines never paint"
