"""Overlay client against the real service (skips cleanly without a bus).

Runs overlay_client_driver.py under dbus-run-session: the driver
starts the real `python -m xx_captions` service and drives it through
CaptionsClient, the exact class the overlay window uses.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
DRIVER = Path(__file__).resolve().parent / "overlay_client_driver.py"

pytestmark = pytest.mark.skipif(
    shutil.which("dbus-run-session") is None,
    reason="dbus-run-session not on PATH")


def test_overlay_client_start_text_stop(tmp_path: Path) -> None:
    pytest.importorskip("gi")
    home = tmp_path / "home"
    home.mkdir()
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(DRIVER),
         str(SRC_DIR), str(home)],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert "CLIENT_OK" in proc.stdout, (
        f"rc={proc.returncode}\n--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}")
    assert proc.returncode == 0
