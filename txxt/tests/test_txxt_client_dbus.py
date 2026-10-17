"""UI client against a fake daemon exporting the real TxxT1 XML.

Runs under ``dbus-run-session``: the helper owns
``io.piercingxx.TxxT.Daemon``, registers an object with the exact
introspection XML from ``txxt.daemon``, and drives the real
``TxxtClient`` (Send / MarkRead / OpenThread) plus one
``MessagesChanged`` round trip. Skipped without ``dbus-run-session``.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_HELPER = r'''
import sys

from gi.repository import GLib, Gio

from txxt import daemon as daemon_mod
from txxt.txxt_client import TxxtClient

BUS = "io.piercingxx.TxxT.Daemon"
PATH = "/io/piercingxx/TxxT/Daemon"
IFACE = "io.piercingxx.TxxT1"

state = {}
seen_signals = []
loop = GLib.MainLoop()
GLib.timeout_add(20000, loop.quit)


def fail(message):
    state["error"] = message
    loop.quit()


def handle_method(connection, sender, obj_path, iface_name,
                  method_name, parameters, invocation):
    try:
        if method_name == "Send":
            address, body = parameters.unpack()
            assert (address, body) == ("+15550100200", "hello from the UI"), \
                (address, body)
            invocation.return_value(GLib.Variant("(x)", (4242,)))
            connection.emit_signal(
                None, PATH, IFACE, "MessagesChanged",
                GLib.Variant("(as)", (["4242"],)),
            )
        elif method_name in ("MarkRead", "OpenThread"):
            (cid,) = parameters.unpack()
            assert int(cid) == 4242, (method_name, cid)
            invocation.return_value(GLib.Variant("()", ()))
        else:
            invocation.return_dbus_error(
                IFACE + ".InvalidArgument", f"unknown: {method_name}")
    except Exception as exc:
        fail(f"daemon handler: {exc}")


def on_bus_acquired(connection, name):
    node = Gio.DBusNodeInfo.new_for_xml(daemon_mod.INTROSPECTION_XML)
    connection.register_object(PATH, node.interfaces[0], handle_method)
    state["connection"] = connection


def on_name_acquired(connection, name):
    state["owned"] = True


daemon_mod.own_bus_name(on_bus_acquired, on_name_acquired)

client_holder = {}


def on_changed(ids):
    seen_signals.append(ids)
    if state.get("opened") and any("4242" in ids for ids in seen_signals):
        loop.quit()


def on_open(ok, err):
    if not ok:
        fail(f"OpenThread failed: {err}")
        return
    state["opened"] = True
    if any("4242" in ids for ids in seen_signals):
        loop.quit()


def on_marked(ok, err):
    if not ok:
        fail(f"MarkRead failed: {err}")
        return
    client_holder["client"].open_thread(4242, on_open)


def on_sent(mid, err):
    if err is not None or int(mid) != 4242:
        fail(f"Send failed: mid={mid} err={err}")
        return
    state["sent"] = int(mid)
    client_holder["client"].mark_read(4242, on_marked)


def start():
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception as exc:
        fail(f"no session bus: {exc}")
        return False
    client = TxxtClient(connection)
    client_holder["client"] = client
    sub = client.subscribe_messages_changed(on_changed)
    if not sub:
        fail("MessagesChanged subscribe failed")
        return False
    GLib.timeout_add(100, _wait_owned)
    return False


def _wait_owned():
    if "error" in state:
        return False
    if not state.get("owned"):
        return True
    client_holder["client"].send("+15550100200", "hello from the UI", on_sent)
    return False


GLib.idle_add(start)
loop.run()

assert "error" not in state, state["error"]
assert state.get("sent") == 4242, state
assert state.get("opened") is True, state
assert any("4242" in ids for ids in seen_signals), seen_signals
print("CLIENT-UI-OK")
'''


def test_ui_client_through_real_bus(tmp_path):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is not installed")
    helper = tmp_path / "ui_client_bus_helper.py"
    helper.write_text(_HELPER)
    env = {**os.environ,
           "PYTHONPATH": os.pathsep.join(
               (str(ROOT / "src"), str(ROOT.parent / "common")))}
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(helper)],
        env=env, capture_output=True, text=True, timeout=120)
    assert "CLIENT-UI-OK" in proc.stdout, (
        f"helper failed (rc={proc.returncode}):\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
    assert proc.returncode == 0
