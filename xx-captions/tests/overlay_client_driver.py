"""Driver: the REAL overlay client against the REAL service.

Runs under dbus-run-session with fake engine + fake capture hooks:
starts `python -m xx_captions`, drives it through CaptionsClient (the
exact code the overlay and the shell tile use), and prints CLIENT_OK.
Any failure raises.
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

sys.path.insert(0, SRC_DIR)

from gi.repository import Gio, GLib  # noqa: E402

from xx_captions.client import CaptionsClient  # noqa: E402
from xx_captions.dbus_api import BUS_NAME  # noqa: E402


def main() -> int:
    service = subprocess.Popen(
        [sys.executable, "-m", "xx_captions"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ})
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        for _ in range(150):
            try:
                names = bus.call_sync(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "ListNames",
                    GLib.Variant("()", ()),
                    GLib.VariantType.new("(as)"),
                    Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
                if BUS_NAME in names:
                    break
            except GLib.Error:
                pass
            if service.poll() is not None:
                raise RuntimeError(
                    "service exited early: "
                    + (service.communicate()[0] or ""))
            time.sleep(0.1)
        else:
            raise AssertionError("service never owned its bus name")

        seen: list[tuple[str, bool]] = []
        props: list[tuple[bool, str]] = []
        loop = GLib.MainLoop()
        failures: list[str] = []

        cli = CaptionsClient(
            on_text=lambda t, f: (seen.append((t, f)),
                                  loop.quit() if f else None),
            on_props=lambda a, s: props.append((a, s)))
        assert cli.connect(), "client could not open the session bus"

        def _on_bogus_done(err: str | None) -> None:
            if err is None:
                failures.append("bad source was accepted")

        cli.start("bogus", done=_on_bogus_done)
        cli.start("playback")
        GLib.timeout_add_seconds(20, loop.quit)
        loop.run()

        assert ("hello", False) in seen, seen
        assert ("hello world", True) in seen, seen
        cli.refresh_props()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not cli.active:
            GLib.main_context_default().iteration(True)
        assert cli.active is True, "GetAll must report Active while on"
        assert cli.source == "playback", cli.source
        assert (True, "playback") in props, props

        stopped: list = []
        cli.stop(done=stopped.append)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not stopped:
            GLib.main_context_default().iteration(True)
        assert stopped == [None], stopped
        cli.refresh_props()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and cli.active:
            GLib.main_context_default().iteration(True)
        assert cli.active is False
        assert (False, "playback") in props, props
        assert not failures, failures
        cli.close()
        print(f"CLIENT_OK partials={sum(1 for _, f in seen if not f)}"
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
