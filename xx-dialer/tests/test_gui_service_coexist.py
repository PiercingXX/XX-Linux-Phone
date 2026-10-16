"""GUI + service coexistence on a private bus (Broadway).

Regression test for the bus-name collision: the policy service owns
``io.piercingxx.XXDialer.Policy`` while the REAL GUI application
registers ``io.piercingxx.XXDialer`` and activates a window, and the
service keeps answering ``CheckNumber``. Skips when
``dbus-run-session`` or ``gtk4-broadwayd`` is missing.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_DRIVER = str(Path(__file__).parent / "broadway_coexist_driver.py")

WALL_MS = int(time.time() * 1000)
ADA = "+14155550100"


def _socket_path(display: str, runtime: str) -> str:
    return os.path.join(runtime, f"broadway{int(display[1:]) + 1}.socket")


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _display_live(display: str, runtime: str) -> bool:
    path = _socket_path(display, runtime)
    if not os.path.exists(path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(path)
    except OSError:
        return False
    return True


def _seed(home: Path) -> None:
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from xx_dialer.store import (
        CALL_TYPE_INCOMING,
        CallRow,
        MirrorRow,
        Store,
    )

    handle = Store(home / ".local" / "share" / "xx-dialer" / "calls.db")
    handle.upsert_mirror([
        MirrorRow("uid-ada", ADA, "Ada", True, None, False, WALL_MS),
    ])
    handle.add_call(CallRow(date=WALL_MS - 1_000, number=ADA,
                            call_type=CALL_TYPE_INCOMING, duration=0))
    handle.close()


@pytest.fixture(scope="module")
def coexist_env(tmp_path_factory):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is missing")
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    home = tmp_path_factory.mktemp("coexist-home")
    runtime = str(tmp_path_factory.mktemp("coexist-runtime"))
    _seed(home)
    daemon_env = dict(os.environ)
    daemon_env["XDG_RUNTIME_DIR"] = runtime
    for number in range(61, 90):
        display = f":{number}"
        if _display_live(display, runtime):
            continue
        try:
            os.unlink(_socket_path(display, runtime))
        except OSError:
            pass
        proc = subprocess.Popen(
            ["gtk4-broadwayd", "--port", str(_free_tcp_port()),
             display],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=daemon_env,
        )
        for _ in range(200):
            if os.path.exists(_socket_path(display, runtime)):
                break
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        else:
            proc.terminate()
            continue
        if proc.poll() is not None or not os.path.exists(
                _socket_path(display, runtime)):
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
            continue
        env = dict(os.environ)
        env["GDK_BACKEND"] = "broadway"
        env["BROADWAY_DISPLAY"] = display
        env["XDG_RUNTIME_DIR"] = runtime
        env["HOME"] = str(home)
        env["XX_DIALER_TEST_HOME"] = str(home)
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


def test_gui_activates_while_service_runs(coexist_env):
    completed = subprocess.run(
        ["dbus-run-session", "--", sys.executable, _DRIVER],
        env=coexist_env, capture_output=True, text=True, timeout=120,
    )
    assert "Failed to register" not in completed.stderr, (
        "GUI failed to register its app id: %s" % completed.stderr[-2000:])
    assert completed.returncode == 0, (
        "driver failed: %s" % completed.stderr[-3000:])
    markers = {}
    for line in completed.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers.get("SERVICE_OWNED") == "True", markers
    assert markers.get("REGISTERED") == "True", markers
    assert markers.get("IS_REMOTE") == "False", markers
    assert markers.get("WINDOW") == "True", markers
    assert markers.get("SERVICE_VERDICT") in (
        "ring", "silence", "block"), markers
    assert markers.get("EXCEPTIONS") == "0", markers
