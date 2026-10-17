"""Headless-safe imports: the pure modules must load without gi."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))


@pytest.fixture
def store(tmp_path):
    from xx_dialer.store import Store

    handle = Store(tmp_path / "calls.db")
    yield handle
    handle.close()


@pytest.fixture(scope="module")
def private_bus(tmp_path_factory):
    """Private session bus for GTK drivers (never the host bus).

    Any test that runs a GTK driver or starts a D-Bus service points
    its children at the yielded address via DBUS_SESSION_BUS_ADDRESS,
    so a service one test started can neither leak into later tests
    nor into the developer's real session. Skips when dbus-daemon is
    missing. Kills only the daemon it started.
    """
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon is missing")
    runtime = tmp_path_factory.mktemp("dbus-runtime")
    socket_path = str(runtime / "private-bus.sock")
    proc = subprocess.Popen(
        ["dbus-daemon", "--session",
         "--address=unix:path=" + socket_path,
         "--print-address", "--nofork"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        address = proc.stdout.readline().strip()
        if not address or proc.poll() is not None:
            raise RuntimeError("dbus-daemon exited before printing")
        yield address
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


@pytest.fixture(autouse=True)
def _isolated_xdg_config(tmp_path_factory, monkeypatch):
    """Never read the developer's real ~/.config/xx-wm theme."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
