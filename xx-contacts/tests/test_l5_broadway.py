"""L5 Broadway smoke test: real import/export page over seeded data.

Builds the real contacts window plus the new import/export page against
a seeded temp database, pumps the main loop, and asserts no exceptions.
Runs against ``gtk4-broadwayd`` in a subprocess so this suite's own
process never pins the backend first.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_DRIVER = str(Path(__file__).parent / "broadway_l5_driver.py")


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
    import random

    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        os.makedirs(runtime, mode=0o700, exist_ok=True)
    except OSError:
        pytest.skip("no writable runtime dir for broadway")
    numbers = list(range(81, 99))
    random.shuffle(numbers)
    for number in numbers:
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


def test_l5_import_export_page_builds_and_pumps(broadway_env, tmp_path):
    base = str(tmp_path / "l5seed")
    os.makedirs(base, exist_ok=True)
    completed = subprocess.run(
        [sys.executable, _DRIVER, base],
        env=broadway_env, capture_output=True, text=True, timeout=120,
    )
    assert "Traceback" not in (completed.stderr or ""), (
        completed.stderr[-3000:])
    assert "CRITICAL" not in (completed.stderr or ""), (
        completed.stderr[-3000:])
    assert completed.returncode == 0, (
        f"driver failed: {(completed.stderr or '')[-3000:]}"
        f"{(completed.stdout or '')[-3000:]}")
    markers = {}
    for line in (completed.stdout or "").splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers.get("PAGES_OK") == "True", markers
    assert markers.get("EXCEPTIONS") == "0", markers
    assert markers.get("LIST_VISIBLE") == "True", markers
    assert markers.get("LIST_HAS_IMPORT_EXPORT") == "True", markers
    assert markers.get("IMPORT_TITLE") == "Import/export", markers
    assert markers.get("IMPORT_VISIBLE") == "True", markers
    assert markers.get("IMPORT_VCF") == "True", markers
    assert markers.get("IMPORT_BACKUP") == "True", markers
    assert markers.get("IMPORT_EDS_SENSITIVE") == "False", markers
    assert markers.get("EXPORT_READY") == "True", markers
    assert markers.get("PUBLISH_STATE") == "False", markers
    assert markers.get("STATUS_EMPTY") == "True", markers
