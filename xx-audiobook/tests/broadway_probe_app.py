"""Fresh-interpreter driver for the Broadway smoke test.

Run in a subprocess with GDK_BACKEND=broadway, BROADWAY_DISPLAY and
XDG_RUNTIME_DIR already pointing at a live gtk4-broadwayd (see
test_broadway_smoke.py). Builds the REAL production window, pumps the main
loop, then prints LABEL:/FOCUS:/SIZE:/CRITICAL: lines and exits 0 only when
every expectation holds. A fresh interpreter is deliberate: it mirrors a
real launch and is immune to whatever the test runner imported first.
"""

from __future__ import annotations

import sys
import threading


def main(argv: list[str]) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    from xx_audiobook import main as main_module

    if Gtk.init_check() is not True or Gdk.Display.get_default() is None:
        print("FAIL: no Broadway display")
        return 1

    errors: list = []
    criticals: list = []
    old_hook, old_thread_hook = sys.excepthook, threading.excepthook
    sys.excepthook = lambda exc_type, value, _tb: errors.append(repr(value))
    threading.excepthook = lambda args: errors.append(repr(args.exc_value))

    def log_handler(_domain, level, message, _data) -> None:
        if level & (GLib.LogLevelFlags.LEVEL_CRITICAL
                    | GLib.LogLevelFlags.LEVEL_ERROR):
            criticals.append(str(message))

    levels = (GLib.LogLevelFlags.LEVEL_CRITICAL
              | GLib.LogLevelFlags.LEVEL_ERROR)
    handler_ids = [
        GLib.log_set_handler(domain, levels, log_handler, None)
        for domain in ("Gtk", "Gdk", "Adwaita", "GLib", "Gio")
    ]
    try:
        app = Adw.Application(
            application_id="io.piercingxx.XXAudiobook",
            flags=Gio.ApplicationFlags.NON_UNIQUE)
        if app.register(None) is not True:
            print("FAIL: app registration failed")
            return 1
        window = main_module.build_window(app)
        window.present()

        loop = GLib.MainLoop()
        GLib.timeout_add(1000, lambda: (loop.quit(), False)[1])
        loop.run()

        for text in _label_texts(window):
            print(f"LABEL:{text}")
        focus = window.get_focus()
        focus_name = type(focus).__name__ if focus is not None else "None"
        print(f"FOCUS:{focus_name}")
        print(f"SIZE:{window.get_default_size().width}"
              f"x{window.get_default_size().height}")
        failed = bool(criticals or errors) or "Entry" in focus_name
        if "Entry" in focus_name:
            print("FAIL: a text entry holds focus at startup")
        for _tab, label, text in main_module.TABS:
            for line in (label, text):
                if line not in _label_texts(window):
                    print(f"FAIL: missing line {line!r}")
                    failed = True
        window.destroy()
    finally:
        for domain, handler_id in zip(
                ("Gtk", "Gdk", "Adwaita", "GLib", "Gio"), handler_ids):
            GLib.log_remove_handler(domain, handler_id)
        sys.excepthook = old_hook
        threading.excepthook = old_thread_hook

    for message in criticals:
        print(f"CRITICAL:{message}")
    for message in errors:
        print(f"ERROR:{message}")
    return 1 if failed else 0


def _label_texts(widget) -> list[str]:
    from gi.repository import Gtk

    texts: list[str] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            texts.append(child.get_text())
        texts.extend(_label_texts(child))
        child = child.get_next_sibling()
    return texts


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
