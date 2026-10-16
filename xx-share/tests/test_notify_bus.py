"""Real-bus coverage for the receive prompt (needs dbus-run-session).

A fake ``org.freedesktop.Notifications`` service (real Gio object with
the real introspection XML) is driven by
:file:`tests/dbus_notify_probe.py` on a private session bus. This proves
``ReceivePrompt.show()`` really issues a ``Notify`` call (a string
``reply_type`` raises ``TypeError`` on real PyGObject and ``show()``
would return ``False``), that ``ActionInvoked`` reaches ``on_action``,
and that ``close()`` issues ``CloseNotification``.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
PROBE = Path(__file__).resolve().parent / "dbus_notify_probe.py"


def test_receive_prompt_real_bus(tmp_path):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session not available")
    try:
        import gi  # noqa: F401

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio  # noqa: F401
    except (ImportError, ValueError) as error:
        pytest.skip(f"PyGObject not available: {error}")
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(PROBE), str(SRC)],
        capture_output=True, text=True, timeout=90, cwd=tmp_path,
    )
    assert proc.returncode == 0, (
        f"probe failed rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "PROBE-NOTIFY-OK" in proc.stdout, proc.stdout + proc.stderr
    assert "PROBE-ACTION-OK" in proc.stdout, proc.stdout + proc.stderr
    assert "PROBE-CLOSE-OK" in proc.stdout, proc.stdout + proc.stderr
    assert "PROBE-OK" in proc.stdout, proc.stdout + proc.stderr
