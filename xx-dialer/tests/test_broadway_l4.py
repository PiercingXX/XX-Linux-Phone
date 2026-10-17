"""Lane 4 Broadway smoke test: the real pages against seeded data.

Builds the real dialer view (Recents + Keypad from ``main``) in a
subprocess on a private Broadway display, pumps the main loop, drives
both pages through their real widgets, and asserts the markers. Fails
— never skips — when ``gtk4-broadwayd`` is available; skips only when
the daemon binary itself is missing.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_DRIVER = str(Path(__file__).parent / "broadway_l4_driver.py")

WALL_MS = int(time.time() * 1000)
ADA = "+14155550100"
BO = "+14155550200"


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
    from xx_dialer import config as app_config
    from xx_dialer.store import (
        CALL_TYPE_INCOMING,
        CALL_TYPE_OUTGOING,
        CallRow,
        MirrorRow,
        ScreenRow,
        Store,
    )

    settings = app_config.load(home)
    settings["voicemail_number"] = "+14155550199"
    app_config.save(settings, home)
    handle = Store(home / ".local" / "share" / "xx-dialer"
                   / "calls.db")
    handle.upsert_mirror([
        MirrorRow("uid-ada", ADA, "Ada", True, None, False, WALL_MS),
        MirrorRow("uid-bo", BO, "Bo Dahl", False, None, False,
                  WALL_MS),
    ])
    handle.upsert_tier("uid-bo", "biz", WALL_MS)
    handle.add_call(CallRow(date=WALL_MS - 1_000, number=ADA,
                            call_type=CALL_TYPE_INCOMING, duration=0))
    handle.add_call(CallRow(date=WALL_MS - 60_000, number=ADA,
                            call_type=CALL_TYPE_INCOMING, duration=45))
    handle.add_call(CallRow(date=WALL_MS - 120_000, number=BO,
                            call_type=CALL_TYPE_OUTGOING, duration=30))
    handle.log_screen(ScreenRow(at=WALL_MS - 1_000, verdict="Silence",
                                reason="unknown caller", e164=ADA,
                                mode="enforcing"))
    handle.log_screen(ScreenRow(at=WALL_MS - 60_000, verdict="Ring",
                                reason="saved contact", e164=ADA,
                                mode="enforcing", answered=True))
    handle.close()


@pytest.fixture(scope="module")
def broadway_env(tmp_path_factory, private_bus):
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    home = tmp_path_factory.mktemp("l4-home")
    runtime = str(tmp_path_factory.mktemp("l4-runtime"))
    _seed(home)
    daemon_env = dict(os.environ)
    daemon_env["XDG_RUNTIME_DIR"] = runtime
    for number in range(25, 60):
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
        env["DBUS_SESSION_BUS_ADDRESS"] = private_bus
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


def _run_driver(env: dict) -> dict:
    completed = subprocess.run(
        [sys.executable, _DRIVER],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, (
        "driver failed: %s" % completed.stderr[-3000:])
    markers = {}
    for line in completed.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers, "driver printed no markers"
    return markers


def test_broadway_builds_real_pages_without_exceptions(broadway_env):
    markers = _run_driver(broadway_env)
    assert markers["PAGES"] == "2"
    assert markers["RECENT_ROWS"] == "2"
    assert markers["ROWS_EXPANDED"] == "4"
    assert markers["STAR_COUNT"] == "1"
    assert markers["BADGE_BEFORE"] == "1"
    assert markers["BADGE_AFTER"] == "0"
    assert markers["EXCEPTIONS"] == "0"


def test_broadway_keypad_types_matches_and_places(broadway_env):
    markers = _run_driver(broadway_env)
    assert markers["NUMBER_CAN_FOCUS"] == "False"
    assert markers["KEYPAD_LABEL"] == "2"
    assert int(markers["MATCH_ROWS"]) >= 1
    assert markers["ZERO_LONG_SUPPRESSED"] == "True"
    assert markers["LABEL_AFTER_CLEAR"] == "Enter number"
    assert markers["VOICEMAIL_PLACED"] == "+14155550199"
    assert markers["CALL_PLACED"] == ADA


def test_broadway_long_press_menu_resolves_row(broadway_env):
    markers = _run_driver(broadway_env)
    assert markers["MENU_VIA"] == "gesture"
    assert markers["MENU_ROW"] == ADA
    assert markers["MENU_BUTTONS"] == "8"
