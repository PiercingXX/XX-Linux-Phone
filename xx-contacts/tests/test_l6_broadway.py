"""L6 Broadway smoke test: picker, prefilled editor, vcf-open pages.

Builds the real contacts window plus the new pick-mode pages (one per
kind, driven through the real service ``Pick`` path and its real GTK
presenter), a ``--tel``-prefilled editor, and an import page fed with
a ``.vcf`` file, against a seeded temp database; pumps the main loop;
asserts no exceptions. Runs against ``gtk4-broadwayd`` in a subprocess
so this suite's own process never pins the backend first.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_DRIVER = str(Path(__file__).parent / "broadway_l6_driver.py")


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
    numbers = list(range(41, 80))
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


def test_l6_pages_build_and_pump_without_exceptions(broadway_env,
                                                    tmp_path):
    base = str(tmp_path / "l6seed")
    os.makedirs(base, exist_ok=True)
    from broadway_run import run_driver

    completed = run_driver(
        [sys.executable, _DRIVER, base],
        env=broadway_env, timeout=120,
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
    assert markers.get("PICK_CONTACT_ROWS") == "3", markers
    assert markers.get("PICK_PHONE_ROWS") == "3", markers
    assert markers.get("PICK_EMAIL_ROWS") == "1", markers
    assert markers.get("PICK_CONTACT_WINDOW") == "True", markers
    assert markers.get("PICK_PHONE_WINDOW") == "True", markers
    assert markers.get("PICK_EMAIL_WINDOW") == "True", markers
    assert "+1 555 0100" in markers.get("PICK_PHONE_REPLY", ""), markers
    assert "ada@example.org" in markers.get("PICK_EMAIL_REPLY", ""), (
        markers)
    assert markers.get("PICK_CANCEL_REPLY") == "('', '')", markers
    for kind in ("CONTACT", "PHONE", "EMAIL"):
        focus = markers.get(f"PICK_{kind}_FOCUS", "")
        assert focus and "Entry" not in focus and "Text" not in focus, (
            markers)
    assert markers.get("PREFILL_NUMBER") == "+1 555 0100", markers
    assert markers.get("VCF_PAGE") == "ImportExportPage", markers
    assert markers.get("VCF_PENDING") == "True", markers
    assert markers.get("VCF_UNCHANGED") == "True", markers
    assert markers.get("LAUNCH_NEW_PAGE") == "ContactEditorPage", markers
    assert markers.get("LAUNCH_NEW_NUMBER") == "+1 555 0404", markers
