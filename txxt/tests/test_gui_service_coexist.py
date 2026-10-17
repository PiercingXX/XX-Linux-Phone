"""GUI starts while the service runs (private bus + Broadway).

The daemon owns ``io.piercingxx.TxxT.Daemon``; the real
``TxxtApplication`` keeps ``application_id`` ``io.piercingxx.TxxT``.
The helper owns the daemon name and registers the real ``Daemon``
object first, then registers and activates the real GUI application
and asserts it is local (not a remote secondary instance), owns the
app bus name, shows a window, and that the daemon still answers
``Send``. Skips unless both ``dbus-run-session`` and
``gtk4-broadwayd`` are available.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_HELPER = r'''
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from txxt import APP_ID, DAEMON_BUS_NAME  # noqa: E402
from txxt import daemon as daemon_mod  # noqa: E402
from txxt.daemon import TXXT_IFACE, TXXT_OBJECT_PATH, Daemon  # noqa: E402
from txxt.main import TxxtApplication  # noqa: E402
from txxt.store import Store  # noqa: E402
from txxt.txxt_client import TxxtClient  # noqa: E402

GUI_BUS = "io.piercingxx.TxxT"

assert APP_ID == GUI_BUS, APP_ID
assert DAEMON_BUS_NAME == "io.piercingxx.TxxT.Daemon", DAEMON_BUS_NAME
assert TXXT_OBJECT_PATH == "/io/piercingxx/TxxT/Daemon", TXXT_OBJECT_PATH
assert TXXT_IFACE == "io.piercingxx.TxxT1", TXXT_IFACE

state = {}
holder = {}
loop = GLib.MainLoop()
GLib.timeout_add(25000, loop.quit)


def fail(message):
    state["error"] = message
    loop.quit()


db_file = daemon_mod.db_path()
db_file.parent.mkdir(parents=True, exist_ok=True)
store = Store(str(db_file))
daemon = Daemon(
    store,
    sender=lambda bus, modem, address, body: (False, "no modem in test"),
    system_bus_fn=lambda: None,
    session_bus_fn=lambda: None,
)


def on_bus_acquired(connection, name):
    daemon.register_on(connection)


def on_name_acquired(connection, name):
    state["daemon_owned"] = True


daemon_mod.own_bus_name(on_bus_acquired, on_name_acquired)


def name_has_owner(name):
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        (owner,) = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetNameOwner",
            GLib.Variant("(s)", (name,)),
            GLib.VariantType.new("(s)"),
            Gio.DBusCallFlags.NONE, 5000, None,
        ).unpack()
        return bool(owner)
    except Exception:
        return False


def on_sent(mid, err):
    if err is not None:
        fail(f"daemon Send failed while the GUI runs: {err}")
        return
    try:
        row = store.get_message(int(mid))
    except Exception as exc:
        fail(f"daemon reply unreadable: {exc}")
        return
    if row is None or row.direction != "OUTGOING":
        fail("daemon Send reply did not persist the outgoing row")
        return
    state["answered"] = True
    loop.quit()


def poll():
    if "error" in state:
        return False
    if not state.get("daemon_owned"):
        return True
    app = holder.get("app")
    if app is None:
        try:
            app = TxxtApplication()
        except Exception as exc:
            fail(f"GUI application failed to construct: {exc}")
            return False
        if app.get_application_id() != GUI_BUS:
            fail(f"GUI application_id changed: {app.get_application_id()}")
            return False
        holder["app"] = app
        try:
            app.register(None)
        except Exception as exc:
            fail(f"Failed to register: {exc}")
            return False
        if app.get_is_remote():
            fail("GUI registered as a remote instance while the daemon runs")
            return False
        app.activate()
        return True
    if app.props.active_window is None:
        return True
    state["window"] = True
    if "checked" not in state:
        state["checked"] = True
        if not name_has_owner(DAEMON_BUS_NAME):
            fail("daemon lost its bus name after the GUI started")
            return False
        if not name_has_owner(GUI_BUS):
            fail("GUI does not own the app bus name after activation")
            return False
        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as exc:
            fail(f"no session bus for the UI client: {exc}")
            return False
        TxxtClient(connection).send("+15550100200", "still here", on_sent)
    return True


GLib.timeout_add(200, poll)
loop.run()

assert "error" not in state, state["error"]
assert state.get("daemon_owned"), state
assert state.get("window"), state
assert state.get("answered"), state
print("GUI-SERVICE-COEXIST-OK")
'''


def _free_display():
    for number in range(5, 100):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 8080 + number))
        except OSError:
            probe.close()
            continue
        probe.close()
        return number
    return None


def test_gui_starts_while_service_runs():
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is missing")
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    tmp = tempfile.TemporaryDirectory(prefix="txxt-coexist")
    old_xdg = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = tmp.name
    proc = None
    try:
        number = None
        for _ in range(5):
            candidate = _free_display()
            if candidate is None:
                break
            proc = subprocess.Popen(
                ["gtk4-broadwayd", f":{candidate}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(1.0)
            if proc.poll() is None:
                number = candidate
                break
            proc = None
        if number is None:
            pytest.fail("could not start gtk4-broadwayd on any display 5..99")
        helper = Path(tmp.name) / "coexist_helper.py"
        helper.write_text(_HELPER)
        env = dict(os.environ)
        env["GDK_BACKEND"] = "broadway"
        env["BROADWAY_DISPLAY"] = f":{number}"
        env["XDG_DATA_HOME"] = tmp.name
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + str(
            ROOT.parent / "common") + os.pathsep + env.get("PYTHONPATH", "")
        child = subprocess.run(
            ["dbus-run-session", "--", sys.executable, str(helper)],
            env=env, capture_output=True, text=True, timeout=120,
        )
        assert child.returncode == 0, (
            f"coexist helper failed (exit {child.returncode}):\n"
            f"--- stdout ---\n{child.stdout}\n--- stderr ---\n{child.stderr}")
        assert "GUI-SERVICE-COEXIST-OK" in child.stdout, (
            f"coexist marker missing:\n"
            f"--- stdout ---\n{child.stdout}\n--- stderr ---\n{child.stderr}")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if old_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_xdg
        tmp.cleanup()
