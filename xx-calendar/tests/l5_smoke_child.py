"""Broadway smoke child: builds the real window and reports markers.

Runs in its own process (started by ``test_l5_broadway.py``) so the
Broadway display connection can never poison the pytest process: when
the daemon dies, only this child is at risk. Prints ``MARKER=value``
lines; exits nonzero with a traceback on any failure.
"""

import datetime as dt
import os
import sys
import threading
import time

UTC = dt.timezone.utc


def _pump_until(predicate, timeout=8.0):
    from gi.repository import GLib

    deadline = time.time() + timeout
    while time.time() < deadline:
        while GLib.main_context_default().pending():
            GLib.main_context_default().iteration(False)
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def main(db_path: str) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk, Pango

    from xx_calendar.main import CalendarApp, CalendarWindow, EventItem

    errors: list = []
    old_hook = sys.excepthook
    old_thread_hook = threading.excepthook

    def on_error(*args):
        errors.append(args)

    sys.excepthook = on_error  # type: ignore[assignment]
    threading.excepthook = on_error  # type: ignore[assignment]
    app = CalendarApp(launch={"date": None, "today": False,
                              "new_event": False, "opens": []},
                      db_path=db_path)
    window = None
    try:
        app.register(None)
        window = CalendarWindow(app, db_path)
        window.set_default_size(360, 720)
        window.present()
        if not _pump_until(
                lambda: window._schedule_model.get_n_items() > 0):
            raise AssertionError("schedule never loaded")
        titles = [window._schedule_model.get_item(i).props.title
                  for i in range(window._schedule_model.get_n_items())
                  if isinstance(window._schedule_model.get_item(i),
                                EventItem)]
        print(f"SCHEDULE_TITLES={len(titles):d}", flush=True)
        if not any("ellipsize" in title for title in titles):
            raise AssertionError("long seeded title never rendered")
        if not any("Standup" in title for title in titles):
            raise AssertionError("recurring seeded event never rendered")
        window.show_view("month")
        if not _pump_until(
                lambda: window._month_grid.get_child_at(0, 0) is not None):
            raise AssertionError("month grid never rendered")
        rows = 0
        while window._month_grid.get_child_at(0, rows) is not None:
            rows += 1
        print(f"MONTH_ROWS={rows:d}", flush=True)
        if rows * 7 < 28:
            raise AssertionError("month grid too small")
        window.show_view("schedule")
        if not _pump_until(
                lambda: window._schedule_model.get_n_items() > 0):
            raise AssertionError("schedule never reloaded")
        window.open_create_sheet(dt.date.today())
        if not _pump_until(lambda: window._last_dialog is not None):
            raise AssertionError("create sheet never opened")
        if not isinstance(window._last_dialog, Adw.Dialog):
            raise AssertionError("create sheet is not a dialog")
        print("SHEET=ok", flush=True)
        window._last_dialog.close()
        window.set_default_size(320, 640)
        if not _pump_until(lambda: True):
            raise AssertionError("unreachable")
        ellipsized = []

        def check_labels(widget) -> None:
            if isinstance(widget, Gtk.Label):
                if widget.get_ellipsize() != Pango.EllipsizeMode.NONE:
                    ellipsized.append(widget)
            child = widget.get_first_child()
            while child is not None:
                check_labels(child)
                child = child.get_next_sibling()

        check_labels(window)
        print(f"ELLIPSIZE={len(ellipsized):d}", flush=True)
        if not ellipsized:
            raise AssertionError("no ellipsizing label in the window")
    finally:
        sys.excepthook = old_hook
        threading.excepthook = old_thread_hook
        if window is not None:
            try:
                window.close()
            except Exception:
                pass
            try:
                window._store.close()
            except Exception:
                pass
            try:
                window._service.stop()
            except Exception:
                pass
        while GLib.main_context_default().pending():
            GLib.main_context_default().iteration(False)
    print(f"ERRORS={len(errors):d}", flush=True)
    if errors:
        raise AssertionError(f"{len(errors):d} main-loop errors: "
                             f"{errors[0][1]!r}")
    print("SMOKE=ok", flush=True)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("GDK_BACKEND", "broadway")
    raise SystemExit(main(sys.argv[1]))
