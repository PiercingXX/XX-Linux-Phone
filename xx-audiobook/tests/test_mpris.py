"""MPRIS property/Seeked tests against the real service on a private bus.

Runs tests/mpris_driver.py under dbus-run-session, so nothing touches the
host session bus. Skips when dbus-run-session is missing.
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

DRIVER = Path(__file__).parent / "mpris_driver.py"
SRC_DIR = str(Path(__file__).parent.parent / "src")


def test_mpris_surface_under_private_bus():
    env = dict(os.environ)
    env["SRC_DIR"] = SRC_DIR
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(DRIVER)],
        capture_output=True, text=True, timeout=90, env=env)
    assert proc.returncode == 0, (
        f"driver failed:\n{proc.stdout}\n{proc.stderr}")
    assert "PASS" in proc.stdout
