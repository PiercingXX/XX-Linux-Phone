"""Driver for the lane-2 D-Bus integration test (runs under dbus-run-session).

Starts the REAL xx-captions service (fake engine + fake capture via
environment test hooks), then drives it as a shell client would:
correct reply_type VariantTypes, first-out-arg unpacking, six-argument
signal handler. Prints DRIVER_OK on success; any failure raises.
"""
from __future__ import annotations

import subprocess
import sys
import time

SRC_DIR = sys.argv[1]
SERVICE_HOME = sys.argv[2]

import os

os.environ["PYTHONPATH"] = SRC_DIR + os.pathsep + os.environ.get(
    "PYTHONPATH", "")
os.environ["HOME"] = SERVICE_HOME
os.environ["XX_CAPTIONS_FAKE_ENGINE"] = "1"
os.environ["XX_CAPTIONS_FAKE_CAPTURE"] = "1"

from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "io.piercingxx.Captions1"
OBJECT_PATH = "/io/piercingxx/Captions1"
INTERFACE = "io.piercingxx.Captions1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"


def call(bus, method, params=None, reply_type=None):
    reply = bus.call_sync(
        BUS_NAME, OBJECT_PATH, INTERFACE, method, params, reply_type,
        Gio.DBusCallFlags.NONE, 5000, None)
    if reply_type is None:
        return None
    return reply.unpack()[0]


def _unwrap(value):
    unpack = getattr(value, "unpack", None)
    return unpack() if callable(unpack) else value


def main() -> int:
    service = subprocess.Popen(
        [sys.executable, "-m", "xx_captions"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ})
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        owned = False
        for _ in range(150):
            try:
                reply = bus.call_sync(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "GetNameOwner",
                    GLib.Variant("(s)", (BUS_NAME,)),
                    GLib.VariantType.new("(s)"),
                    Gio.DBusCallFlags.NONE, 2000, None)
                assert reply.unpack()[0]
                owned = True
                break
            except GLib.Error:
                if service.poll() is not None:
                    raise RuntimeError(
                        "service exited early: "
                        + (service.communicate()[0] or ""))
                time.sleep(0.1)
        assert owned, "service never owned its bus name"

        seen: list[tuple[str, bool]] = []
        loop = GLib.MainLoop()

        def on_text(conn, sender, path, iface, signal,
                    params) -> None:
            text, final = params.unpack()
            seen.append((str(text), bool(final)))
            if bool(final):
                loop.quit()

        bus.signal_subscribe(BUS_NAME, INTERFACE, "Text", OBJECT_PATH,
                             None, Gio.DBusSignalFlags.NONE, on_text)

        try:
            call(bus, "Start", GLib.Variant("(s)", ("bogus",)))
            raise AssertionError("bad source was accepted")
        except GLib.Error as exc:
            assert "InvalidArgs" in str(exc), str(exc)

        try:
            call(bus, "Start", GLib.Variant("(s)", ("call",)))
            raise AssertionError("call started without opt-in")
        except GLib.Error as exc:
            assert "NotPermitted" in str(exc), str(exc)

        call(bus, "Start", GLib.Variant("(s)", ("playback",)))

        GLib.timeout_add_seconds(20, loop.quit)
        loop.run()
        assert ("hello", False) in seen, seen
        assert ("hello world", True) in seen, seen

        active = _unwrap(bus.call_sync(
            BUS_NAME, OBJECT_PATH, PROPS_IFACE, "Get",
            GLib.Variant("(ss)", (INTERFACE, "Active")),
            GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE, 5000, None).unpack()[0])
        assert active is True
        source = _unwrap(bus.call_sync(
            BUS_NAME, OBJECT_PATH, PROPS_IFACE, "Get",
            GLib.Variant("(ss)", (INTERFACE, "Source")),
            GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE, 5000, None).unpack()[0])
        assert source == "playback"

        rows = call(bus, "ListTranscripts",
                    reply_type=GLib.VariantType.new("(a(ssxx))"))
        assert rows == [], rows

        try:
            call(bus, "GetTranscript",
                 GLib.Variant("(s)", ("no-such-call",)),
                 GLib.VariantType.new("(sxxa(sx))"))
            raise AssertionError("unknown transcript was accepted")
        except GLib.Error as exc:
            assert "NotFound" in str(exc), str(exc)

        call(bus, "Stop")
        call(bus, "Stop")
        print(f"DRIVER_OK partials={sum(1 for _, f in seen if not f)}"
              f" finals={sum(1 for _, f in seen if f)}")
        return 0
    finally:
        service.terminate()
        try:
            service.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            service.kill()


if __name__ == "__main__":
    sys.exit(main())
