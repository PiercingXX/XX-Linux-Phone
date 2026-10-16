"""Broadway driver for the Lane 4 smoke test (run as a subprocess).

Builds the REAL dialer view (Recents + Keypad) against the seeded store
the parent pytest prepared at XX_DIALER_TEST_HOME, presents it on the
Broadway display the parent provides, pumps the main loop, exercises
both pages through their real widgets, and prints KEY: value markers.
Any unexpected exception exits nonzero so the parent fails instead of
passing on empty output. Not collected by pytest.
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
from xx_dialer import ui_recents  # noqa: E402

ERRORS: list[str] = []


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


class _Gesture:
    def __init__(self, widget):
        self._widget = widget

    def get_widget(self):
        return self._widget


def main() -> int:
    home = Path(os.environ["XX_DIALER_TEST_HOME"])
    db_file = home / ".local" / "share" / "xx-dialer" / "calls.db"
    voice = _Voice()
    deps = DialerDeps(db_file=db_file, home=home, voice=voice,
                      contacts=object())
    view = build_dialer_view(deps)
    window = Gtk.Window()
    window.set_child(view.widget)
    window.present()
    _pump(1.0)

    pages = sum(1 for name in ("recents", "keypad")
                if view.stack.get_child_by_name(name) is not None)
    print("PAGES:", pages)
    store_items = view.recents_view._store.get_n_items()
    print("RECENT_ROWS:", store_items)
    print("STAR_COUNT:", len(view.recents.starred))
    print("BADGE_BEFORE:", view.recents.unread)

    first = view.recents.merged[0]
    print("FIRST_TITLE:", first.display_name or first.e164)
    print("FIRST_LINE:",
          ui_recents.RecentsController._dialable(first) is not None)

    view.recents.toggle_group(first.e164 or "")
    view.refresh_all()
    _pump(0.3)
    print("ROWS_EXPANDED:", view.recents_view._store.get_n_items())

    keypad = view.keypad
    print("NUMBER_CAN_FOCUS:", keypad._number.get_can_focus())
    keypad._keys["2"].emit("clicked")
    _pump(0.2)
    print("KEYPAD_LABEL:", keypad._number.get_text())
    match_count = 0
    while keypad._matches.get_row_at_index(match_count) is not None:
        match_count += 1
    print("MATCH_ROWS:", match_count)

    zero = keypad._keys["0"]
    keypad._on_zero_long(_Gesture(zero), 0.0, 0.0)
    after_long = keypad._number.get_text()
    zero.emit("clicked")
    print("ZERO_LONG_SUPPRESSED:",
          keypad._number.get_text() == after_long
          and after_long.endswith("+"))
    keypad._on_delete_long(_Gesture(keypad._delete), 0.0, 0.0)
    keypad._delete.emit("clicked")
    print("LABEL_AFTER_CLEAR:", keypad._number.get_text())

    keypad.dial_voicemail()
    print("VOICEMAIL_PLACED:", voice.placed[-1]
          if voice.placed else "none")
    keypad.set_digits("+14155550100")
    keypad._call.emit("clicked")
    _pump(0.2)
    print("CALL_PLACED:", voice.placed[-1] if voice.placed else "none")

    ada = [call for call in view.recents.merged
           if call.glyph == "silenced"][0]
    buttons = ui_recents._menu_buttons(ada, view.recents)
    print("MENU_BUTTONS:", len(buttons))

    gestures = [controller for controller in
                view.recents_view._list.observe_controllers()]
    presses = [controller for controller in gestures
               if isinstance(controller, Gtk.GestureLongPress)]
    found = None
    original = view.recents_view._open_menu
    seen: list[str] = []

    def _record(key, _anchor):
        seen.append(key)

    view.recents_view._open_menu = _record
    for gate in presses:
        for y in range(10, 700, 15):
            gate.emit("pressed", 10.0, float(y))
            if any(key.startswith("row:") for key in seen):
                found = seen[-1]
                break
        if found is not None:
            break
    view.recents_view._open_menu = original
    print("MENU_VIA:", "gesture" if found else "none")
    if found is not None:
        call = view.recents_view._resolve_key(found)
        anchor = view.recents_view._list
        original(found, anchor)
        _pump(0.3)
        print("MENU_ROW:", call.e164 if call else "none")

    view.stack.set_visible_child_name("keypad")
    _pump(0.2)
    view.stack.set_visible_child_name("recents")
    _pump(0.2)
    print("BADGE_AFTER:", view.recents.unread)

    window.destroy()
    _pump(0.2)
    print("EXCEPTIONS:", len(ERRORS))
    for text in ERRORS:
        sys.stderr.write(text)
    return 1 if ERRORS else 0


sys.exit(main())
