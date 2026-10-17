"""The GUI starts while the player service runs (bus-name trap).

If the daemon owned the GUI app id, Gtk.Application would treat the GUI
as a secondary instance and exit. Runs tests/bus_trap_driver.py under
dbus-run-session; skips when dbus-run-session is missing.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("dbus-run-session") is None,
    reason="dbus-run-session is missing")

DRIVER = Path(__file__).parent / "bus_trap_driver.py"
SRC_DIR = str(Path(__file__).parent.parent / "src")


def test_gui_app_id_stays_free_while_service_runs():
    env = dict(os.environ)
    env["SRC_DIR"] = SRC_DIR
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(DRIVER)],
        capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode == 0, (
        f"driver failed:\n{proc.stdout}\n{proc.stderr}")
    assert "PASS" in proc.stdout
