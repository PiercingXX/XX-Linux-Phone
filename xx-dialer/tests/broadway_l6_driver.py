"""Broadway driver for the Lane 6 smoke test (run as a subprocess).

Builds the REAL dialer view against the seeded store the parent
pytest prepared at XX_DIALER_TEST_HOME, presents it on the Broadway
display the parent provides, pumps the main loop, visits the new
Backup + Logs pages through their real widgets, and prints KEY:
value markers. Any unexpected exception exits nonzero so the parent
fails instead of passing on empty output. Not collected by pytest.
"""
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from xx_dialer.main import DialerDeps, build_dialer_view  # noqa: E402

ERRORS: list[str] = []

ADA = "+14155550100"


def _hook(kind, value, tb) -> None:
    text = "".join(traceback.format_exception(kind, value, tb))
    ERRORS.append(text)
    sys.stderr.write(text)
    sys.stderr.flush()


sys.excepthook = _hook


def _pump(seconds: float) -> None:
    loop = GLib.MainLoop()
    GLib.timeout_add(int(seconds * 1000), loop.quit)
    loop.run()


class _Voice:
    def __init__(self):
        self.placed: list[str] = []

    def place(self, number, on_done):
        self.placed.append(number)
        on_done(True, "/call/0")


class _Client:
    is_present = True
    revision = 3

    def __init__(self):
        self.calls: list[tuple] = []

    def set_star(self, uid, starred):
        self.calls.append(("star", uid, starred))

    def set_business(self, uid, business):
        self.calls.append(("business", uid, business))

    def set_blocked(self, uid, blocked):
        self.calls.append(("blocked", uid, blocked))
        return []


def main() -> int:
    from xx_dialer import applog, codecs  # noqa: E402

    home = Path(os.environ["XX_DIALER_TEST_HOME"])
    db_file = home / ".local" / "share" / "xx-dialer" / "calls.db"
    voice = _Voice()
    client = _Client()
    deps = DialerDeps(db_file=db_file, home=home, voice=voice,
                      contacts=client)
    view = build_dialer_view(deps)
    window = Gtk.Window()
    window.set_child(view.widget)
    window.present()
    _pump(1.0)

    names = ("recents", "keypad", "people", "rules", "setup", "backup",
             "logs")
    pages = sorted(name for name in names
                   if view.stack.get_child_by_name(name) is not None)
    print("PAGES:", len(pages))

    view.stack.set_visible_child_name("backup")
    _pump(0.3)
    backup_view = view.backup
    print("BACKUP_STATUS:", backup_view._export_status.get_text())
    print("BACKUP_IMPORT_STATUS:", repr(backup_view._import_status
                                        .get_text()))
    policy_text = backup_view.controller.export_policy_text()
    print("EXPORT_POLICY_OK:", codecs.decode_policy(policy_text)
          is not None)
    calllog_text = backup_view.controller.export_calllog_text()
    print("EXPORT_CALLLOG_ROWS:", len(codecs.decode_calllog(calllog_text)))
    print("IMPORT_POLICY:",
          "observe mode" in backup_view.controller.import_policy_text(
              policy_text))
    print("IMPORT_GARBAGE:",
          "Could not read" in backup_view.controller.import_calllog_text(
              "{ nope"))
    _pump(0.2)

    applog.write("cards", "posted id=41 Silenced call %s" % ADA, home)
    view.stack.set_visible_child_name("logs")
    _pump(0.3)
    logs_view = view.logs
    logs_view.refresh()
    _pump(0.2)
    buffer = logs_view._preview.get_buffer()
    preview = buffer.get_text(buffer.get_start_iter(),
                              buffer.get_end_iter(), False)
    print("LOGS_PREVIEW_MASKED:", "*********00" in preview)
    print("LOGS_PREVIEW_RAW:", ADA in preview)
    rows = []
    row = logs_view._file_list.get_row_at_index(0)
    index = 0
    while row is not None:
        rows.append(row)
        index += 1
        row = logs_view._file_list.get_row_at_index(index)
    print("LOGS_ROWS:", len(rows))
    logs_view._clear_button.emit("clicked")
    _pump(0.2)
    print("LOGS_AFTER_CLEAR:", len(logs_view.controller.files()))

    view.stack.set_visible_child_name("recents")
    _pump(0.2)

    window.destroy()
    _pump(0.2)
    print("EXCEPTIONS:", len(ERRORS))
    for text in ERRORS:
        sys.stderr.write(text)
    return 1 if ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
