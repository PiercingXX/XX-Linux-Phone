"""Bus-name trap driver for test_player_bus.py (under dbus-run-session).

Starts the REAL player service, then proves the GUI app id is still free
by registering a Gio.Application with that id: if the daemon owned the
app id, the GUI would exit as a secondary instance. Prints PASS or raises.
"""

import os
import sys
import tempfile
import threading
import time

HOME = tempfile.mkdtemp(prefix="xx-audiobook-trap-")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = os.path.join(HOME, "config")

sys.path.insert(0, os.environ["SRC_DIR"])

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from xx_audiobook import __app_id__ as APP_ID  # noqa: E402
from xx_audiobook.player import FakeBackend  # noqa: E402
from xx_audiobook.service.player_service import (  # noqa: E402
    MPRIS_BUS,
    PLAYER_BUS,
    PlayerService,
)
from xx_audiobook.store import Database  # noqa: E402


def names(connection):
    reply = connection.call_sync(
        "org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", "ListNames", None,
        GLib.VariantType.new("(as)"), Gio.DBusCallFlags.NONE, 5000, None)
    (listed,) = reply.unpack()
    return set(listed)


def main():
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    service = PlayerService(Database.in_memory(), None, FakeBackend())
    service.register_on(connection)
    Gio.bus_own_name_on_connection(connection, PLAYER_BUS,
                                   Gio.BusNameOwnerFlags.NONE, None, None)
    Gio.bus_own_name_on_connection(connection, MPRIS_BUS,
                                   Gio.BusNameOwnerFlags.NONE, None, None)
    loop = GLib.MainLoop()
    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            current = names(connection)
            if PLAYER_BUS in current and MPRIS_BUS in current:
                break
            time.sleep(0.05)
        assert PLAYER_BUS in current, "player name not owned"
        assert MPRIS_BUS in current, "MPRIS name not owned"
        assert APP_ID not in current, "daemon must not own the app id"
        app = Gio.Application(application_id=APP_ID,
                              flags=Gio.ApplicationFlags.FLAGS_NONE)
        assert app.register(None) is True, (
            "GUI app id still registers while the service runs")
        assert APP_ID in names(connection), "GUI name visible on the bus"
        print("PASS")
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback

        print("FAIL")
        print("".join(traceback.format_exception(exc)))
        return 1
    finally:
        loop.quit()


if __name__ == "__main__":
    raise SystemExit(main())
