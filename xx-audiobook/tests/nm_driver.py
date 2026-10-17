"""Fake NetworkManager for test_download_nm.py (under dbus-run-session).

Owns org.freedesktop.NetworkManager on the private session bus and
answers Properties.GetAll with the MODE env's State/Metered/
Connectivity, then probes it exactly like the app does and prints
STATE:<name>. Prints PASS or raises.
"""

import os
import sys
import tempfile
import threading

HOME = tempfile.mkdtemp(prefix="xx-audiobook-nm-")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = os.path.join(HOME, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(HOME, "data")

sys.path.insert(0, os.environ["SRC_DIR"])

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from xx_audiobook import download as download_module  # noqa: E402

NM_XML = """\
<node>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="GetAll">
      <arg direction="in" type="s" name="interface"/>
      <arg direction="out" type="a{sv}" name="properties"/>
    </method>
  </interface>
</node>
"""

MODES = {
    "metered": (70, 1, 4),
    "online": (70, 2, 4),
    "offline": (20, 0, 1),
}


def main():
    mode = os.environ.get("MODE", "metered")
    state, metered, connectivity = MODES[mode]
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    node = Gio.DBusNodeInfo.new_for_xml(NM_XML)
    iface = node.interfaces[0]

    def handle(_conn, _sender, _path, _iface, method, params,
               invocation):
        if method != "GetAll":
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod", method)
            return
        props = {
            "State": GLib.Variant("u", state),
            "Metered": GLib.Variant("u", metered),
            "Connectivity": GLib.Variant("u", connectivity),
            "ActiveConnections": GLib.Variant("ao", []),
        }
        invocation.return_value(GLib.Variant("(a{sv})", (props,)))

    connection.register_object(
        download_module.NM_PATH, iface, handle)
    Gio.bus_own_name_on_connection(
        connection, download_module.NM_BUS,
        Gio.BusNameOwnerFlags.NONE, None, None)
    loop = GLib.MainLoop()
    result: dict = {}

    def work():
        try:
            import time

            deadline = time.monotonic() + 10.0
            owned = False
            while time.monotonic() < deadline:
                reply = connection.call_sync(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "ListNames", None,
                    GLib.VariantType.new("(as)"),
                    Gio.DBusCallFlags.NONE, 5000, None)
                (listed,) = reply.unpack()
                if download_module.NM_BUS in listed:
                    owned = True
                    break
                time.sleep(0.05)
            assert owned, "fake NM name not owned"
            seen = download_module.probe_nm(connection)
            result["state"] = seen.name
        except Exception as exc:  # noqa: BLE001
            import traceback

            result["error"] = "".join(
                traceback.format_exception(exc))
        finally:
            GLib.idle_add(loop.quit)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    loop.run()
    thread.join(timeout=10)
    if "error" in result:
        print("FAIL")
        print(result["error"])
        return 1
    print(f"STATE:{result.get('state')}")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
