"""Device-rule contract for the mailbox stack (headless static checks).

The runtime traps in XXWM_RULES.md bit on a real phone while unit
tests stayed green, so this file pins the trap surface directly on
the sources: no get_display on the Application, no programmed focus
(the OSK pops only on tap), no minimize/maximize buttons, themed
list backgrounds, ListView-backed long lists, ellipsized rows, and no
blocking calls on the main-loop side.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "xx_email"
UI = SRC / "ui"

KNOWN_SIGNALS = {
    "activate",
    "bind",
    "changed",
    "clicked",
    "pressed",
    "setup",
    "swipe",
}


def _sources():
    files = [SRC / "main.py", UI / "common.py", UI / "mailbox_page.py",
             UI / "thread_page.py", UI / "search_page.py",
             UI / "setup_page.py"]
    for path in files:
        assert path.exists(), path
    return {path: path.read_text(encoding="utf-8") for path in files}


def test_no_get_display_on_application():
    sources = _sources()
    for path, text in sources.items():
        assert ".get_display()" not in text, path
    assert "Gdk.Display.get_default()" in sources[SRC / "main.py"]


def test_no_programmed_focus_at_startup():
    for path, text in _sources().items():
        assert "grab_focus" not in text, path
        assert "grab-focus" not in text, path


def test_phone_chrome_hides_minimize_maximize():
    common = (UI / "common.py").read_text(encoding="utf-8")
    assert "set_show_start_title_buttons(False)" in common
    assert "set_show_end_title_buttons(False)" in common


def test_long_lists_are_listview_backed():
    common = (UI / "common.py").read_text(encoding="utf-8")
    assert "Gtk.ListView" in common
    assert "SignalListItemFactory" in common
    assert "NoSelection" in common
    for name in ("mailbox_page.py", "thread_page.py", "search_page.py"):
        text = (UI / name).read_text(encoding="utf-8")
        assert "ListView" in text or "thread_list_view" in text, name


def test_rows_ellipsize_or_wrap():
    common = (UI / "common.py").read_text(encoding="utf-8")
    assert "set_ellipsize" in common
    assert "set_wrap" in common


def test_search_entry_sits_at_the_bottom_unfocused():
    text = (UI / "search_page.py").read_text(encoding="utf-8")
    assert text.index("self.append(scrolled)") < \
        text.index("self.append(entry_row)")


def test_no_blocking_calls_on_ui_side():
    banned = ("subprocess", "time.sleep", "call_sync",
              "lookup_sync", "store_sync", "clear_sync", "bus_get_sync")
    for path, text in _sources().items():
        for marker in banned:
            assert marker not in text, (path, marker)


def test_only_known_signal_names_connected():
    connected = set()
    for path, text in _sources().items():
        for match in re.finditer(r"\.connect\(\"([^\"]+)\"", text):
            connected.add(match.group(1))
    unknown = connected - KNOWN_SIGNALS
    assert not unknown, unknown


def test_folder_sheet_and_tabs_use_adaptive_widgets():
    mailbox = (UI / "mailbox_page.py").read_text(encoding="utf-8")
    assert "Adw.Dialog" in mailbox
    assert "Adw.ViewStack" in mailbox
    assert "Adw.ViewSwitcherBar" in mailbox
    assert "Gtk.GestureSwipe" in (UI / "common.py").read_text(
        encoding="utf-8")
    assert "Gtk.GestureLongPress" in (UI / "common.py").read_text(
        encoding="utf-8")


def test_theme_paints_list_backgrounds():
    from xx_email import theme as theme_mod
    css = theme_mod.theme_css(theme_mod.THEME_PRESETS["amoled"],
                              "Sans", 1.0)
    assert "listview" in css
    assert "#ffffff" not in css.lower()
    assert "#fff" not in css.lower().replace("#ffffff", "")
