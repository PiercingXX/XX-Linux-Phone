"""Static guards for the first-launch crash and the screenshot fixes.

These read the sources (never construct widgets, so they run headless)
and pin the runtime traps that unit tests hid before: ``Adw.Application``
has no ``get_display()``, list headers must not select, the search entry
must not autofocus, phone chrome has no min/max buttons, and the list
takes the theme background instead of default white.
"""

import re
from pathlib import Path

import xx_contacts.main  # noqa: F401 (import stays display-free)

SRC = Path(__file__).parent.parent / "src" / "xx_contacts"


def _read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def test_app_themes_through_design_system():
    src = _read("main.py")
    receivers = re.findall(r"(\S+)\.get_display\s*\(", src)
    assert not receivers, f"get_display() calls: {receivers}"
    assert "import xx_ui" in src
    assert "xx_ui.apply(" in src
    assert "shell_theme" not in src


def test_list_header_has_no_min_max_buttons():
    src = _read("list_ui.py")
    assert "HeaderBar" not in src
    assert "set_decorated(False)" in src
    assert "page_header(" in src
    assert "Gtk.HeaderBar" not in src


def test_search_entry_never_grabs_focus_unless_tapped():
    src = _read("list_ui.py")
    assert "set_can_focus(False)" in src
    assert "set_focusable(False)" in src
    assert 'connect("map"' in src
    assert "set_focus(None)" in src
    assert src.count("grab_focus") == 1
    assert "CAPTURE" in src
    assert "EventControllerFocus" in src
    assert 'connect("leave"' in src
    assert 'connect("notify::has-focus"' not in src


def test_section_headers_are_not_selectable():
    src = _read("list_ui.py")
    assert "set_selectable(not is_header)" in src
    assert "set_focusable(not is_header)" in src
    assert "set_activatable(not is_header)" in src
    assert "Gtk.ListScrollFlags.NONE" in src
    assert "Gtk.ListScrollFlags.SELECT" not in src


def test_theme_css_covers_listview_and_headers(isolated_home):
    import json

    from xx_ui import css as xx_css
    from xx_ui import theme as xx_theme

    for key in xx_theme.preset_keys():
        path = (xx_theme.config_path().parent / "config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"theme": key}), encoding="utf-8")
        active = xx_theme.load()
        assert active.key == key
        out = xx_css.render(active)
        assert "listview" in out
        assert active.background in out
        assert active.foreground in out
    rendered = xx_css.render(xx_theme.load())
    lowered = rendered.lower()
    assert "#fff;" not in lowered
    assert "#fff " not in lowered
    assert "white" not in lowered
    assert ".xx-setting" in rendered


def test_list_shows_spaced_numbers():
    src = _read("list_ui.py")
    assert "xx_phone.display(" in src
    assert "format_display_number" not in src


def test_detail_page_has_no_text_entry_or_focus_grab():
    src = _read("detail_ui.py")
    assert "Gtk.Entry" not in src
    assert "SearchEntry" not in src
    assert "grab_focus" not in src
    assert "HeaderBar" not in src
    assert "compact_header(" in src


def test_dialog_choose_takes_cancellable_first():
    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw

    alert_args = [a.get_name()
                  for a in Adw.AlertDialog.choose.get_arguments()]
    assert alert_args[:3] == ["parent", "cancellable", "callback"]
    message_args = [a.get_name()
                    for a in Adw.MessageDialog.choose.get_arguments()]
    assert message_args[:2] == ["cancellable", "callback"]
    for name in ("list_ui.py", "detail_ui.py", "main.py"):
        src = _read(name)
        assert "MessageDialog" not in src, name
    for name in ("list_ui.py", "detail_ui.py"):
        src = _read(name)
        assert "AlertDialog" in src, name
        assert "choose(None" not in src, name
        assert ".choose(self" in src or ".choose(self._parent" in src, name


def test_detail_page_wires_store_share_and_delete():
    src = _read("detail_ui.py")
    assert "ChangeWatcher" in src
    assert "update_tags" in src
    assert "Gtk.FileDialog" in src
    assert "AlertDialog" in src
    assert "MessageDialog" not in src
    assert "launch_uri" in src
    assert "CALL_HISTORY_AVAILABLE" in src
