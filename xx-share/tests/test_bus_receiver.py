"""bus_receiver_running asks the bus daemon, the only API that exists."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_bus_receiver_running_works_on_a_real_bus(tmp_path):
    # Gio.DBusConnection has no get_name_owner: the old call raised
    # AttributeError and the GUI never saw the running receiver.
    if subprocess.run(["which", "dbus-run-session"], capture_output=True).returncode:
        pytest.skip("dbus-run-session is missing")
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from main import bus_receiver_running\n"
        "print('RUNNING', bus_receiver_running())\n" % str(ROOT / "src"))
    out = subprocess.run(["dbus-run-session", "--", sys.executable, "-c", code],
                         capture_output=True, text=True, timeout=60)
    assert "RUNNING False" in out.stdout, out.stderr
