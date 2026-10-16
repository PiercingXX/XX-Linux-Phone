"""Real service over a real session bus (skips cleanly without one).

Runs tests/dbus_driver.py under dbus-run-session: the driver starts
`python -m xx_captions` (fake engine + fake capture hooks) and drives
the pinned io.piercingxx.Captions1 interface as a shell client would.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
DRIVER = Path(__file__).resolve().parent / "dbus_driver.py"

pytestmark = pytest.mark.skipif(
    shutil.which("dbus-run-session") is None,
    reason="dbus-run-session not on PATH")


def test_real_service_start_text_stop_flush(tmp_path: Path) -> None:
    pytest.importorskip("gi")
    home = tmp_path / "home"
    home.mkdir()
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(DRIVER),
         str(SRC_DIR), str(home)],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert "DRIVER_OK" in proc.stdout, (
        f"rc={proc.returncode}\n--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}")
    assert proc.returncode == 0
