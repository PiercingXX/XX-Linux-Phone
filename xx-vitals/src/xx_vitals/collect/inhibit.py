"""logind sleep inhibitor for the resident step sampler.

Linux phones suspend to RAM when the screen goes off, and while
suspended no user process runs — a software pedometer only counts
while the phone is awake. When the user opts in with "count steps
while screen is off", the sampler takes a logind `delay` inhibitor
for `sleep` on entry to the moving state and releases it when still
returns, so a walk with the screen off delays (not blocks) suspend
for a short while. Default is off: no inhibitor, no extra battery.

The real D-Bus call is `org.freedesktop.login1.Manager.Inhibit(
"sleep", "xx-vitals", "Counting steps", "delay")`, which hands back
a file descriptor inside a `Gio.UnixFDList`. The fd must stay open
while the motion lasts and be closed when motion stops — closing is
what releases the delay. Gio is imported inside the function so this
module stays headless-importable; tests inject a fake `take`.
"""

from __future__ import annotations

LOGIN1_BUS = "org.freedesktop.login1"
LOGIN1_PATH = "/org/freedesktop/login1"
LOGIN1_IFACE = "org.freedesktop.login1.Manager"

WHAT = "sleep"
WHO = "xx-vitals"
WHY = "Counting steps"
MODE = "delay"


class FdLease:
    """One duplicated inhibitor fd; closing releases the delay."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        import os

        try:
            os.close(self._fd)
        except OSError:
            pass
        self.closed = True


def take_logind_delay_inhibitor():
    """Take the real logind delay inhibitor, or None when unreachable.

    Blocking Gio call: collector process or worker thread only, never
    the GTK main loop. The reply packs out-args as a one-tuple, so
    `unpack()[0]` is the fd index into the returned fd list.
    """
    try:
        import os

        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        out = conn.call_with_unix_fd_list_sync(
            LOGIN1_BUS, LOGIN1_PATH, LOGIN1_IFACE, "Inhibit",
            GLib.Variant("(ssss)", (WHAT, WHO, WHY, MODE)),
            GLib.VariantType.new("(h)"),
            Gio.DBusCallFlags.NONE, 5000, None, None,
        )
        reply, fd_list = out[0], out[1]
        index = reply.unpack()[0]
        owned = os.dup(fd_list.get(index))
    except Exception:
        return None
    return FdLease(owned)


class SleepInhibitor:
    """Holds at most one inhibitor fd; released on still or on demand.

    `take` is injected in tests with a fake returning a closable; by
    default it takes the real logind delay inhibitor.
    """

    def __init__(self, take=None) -> None:
        self._take = take if take is not None else take_logind_delay_inhibitor
        self._held = None

    @property
    def held(self) -> bool:
        return self._held is not None

    def acquire(self) -> bool:
        """Take the inhibitor unless one is already held."""
        if self._held is not None:
            return True
        try:
            lease = self._take()
        except Exception:
            return False
        if lease is None:
            return False
        self._held = lease
        return True

    def release(self) -> None:
        """Close the held fd, releasing the delay."""
        held, self._held = self._held, None
        if held is None:
            return
        try:
            held.close()
        except Exception:
            pass
