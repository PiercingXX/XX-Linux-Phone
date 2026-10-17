"""L3 fix-up round 2: tap-to-open, tap-to-focus, no autoselect, labels.

Covers the five phone lessons from the c3 screenshots: single-click
activate, the search entry only taking focus from an explicit tap,
NoSelection instead of an autoselecting SingleSelection, human vCard
TYPE labels on the detail page, and the destructive-action Delete style.
Widget construction segfaults without a display here, so the GTK test
only runs when a real display exists and skips otherwise.
"""

from pathlib import Path

import pytest

from xx_contacts.detail import build_detail_sections, human_type_label
from xx_contacts.list_ui import (
    _on_tap_to_focus_left,
    _on_tap_to_focus_pressed,
)
from xx_ui import css as xx_css
from xx_ui import theme as xx_theme
from xx_contacts.store import (
    AddressEntry,
    ContactData,
    EmailEntry,
    PhoneEntry,
)

SRC = Path(__file__).parent.parent / "src" / "xx_contacts"


def _read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


# ------------------------------------------------------- human type labels


def test_type_tokens_map_to_human_labels():
    assert human_type_label("CELL") == "Mobile"
    assert human_type_label("HOME") == "Home"
    assert human_type_label("WORK") == "Work"
    assert human_type_label("VOICE") == "Phone"
    assert human_type_label("FAX") == "Fax"
    assert human_type_label("PAGER") == "Pager"
    assert human_type_label("MAIN") == "Main"
    assert human_type_label("IPHONE") == "iPhone"
    assert human_type_label("OTHER") == "Other"


def test_type_matching_ignores_case_and_separators():
    assert human_type_label("cell") == "Mobile"
    assert human_type_label(" home ; voice ") == "Home"
    assert human_type_label("work,voice") == "Work"


def test_location_tokens_win_over_generic_voice():
    assert human_type_label("HOME,VOICE") == "Home"
    assert human_type_label("WORK,VOICE") == "Work"
    assert human_type_label("CELL,VOICE") == "Mobile"


def test_unknown_tokens_read_as_other():
    assert human_type_label("CAR") == "Other"
    assert human_type_label("HOME,CAR") == "Home"
    assert human_type_label("BOGUS") == "Other"


def test_flags_fall_back_instead_of_other():
    assert human_type_label("PREF", fallback="Phone") == "Phone"
    assert human_type_label("INTERNET", fallback="Email") == "Email"
    assert human_type_label("", fallback="Phone") == "Phone"
    assert human_type_label("  ", fallback="Address") == "Address"


def test_custom_labels_win_title_cased():
    assert human_type_label("CELL", "my satellite") == "My Satellite"
    assert human_type_label("X-CUSTOM") == "Custom"
    assert human_type_label("x-my_label") == "My Label"
    assert human_type_label("WORK", "X-NIGHT-LINE") == "Night Line"


def test_detail_sections_show_human_labels():
    data = ContactData(
        display_name="Ada Lovelace",
        phones=[PhoneEntry(number_raw="+15550100199", type="CELL")],
        emails=[EmailEntry(address="ada@example.org", type="HOME")],
        addresses=[AddressEntry(street="1 Main St", type="WORK")],
    )
    by_key = {s.key: s for s in build_detail_sections(data)}
    assert by_key["phones"].fields[0].label == "Mobile"
    assert by_key["emails"].fields[0].label == "Home"
    assert by_key["addresses"].fields[0].label == "Work"
    assert "CELL" not in (f.label for s in by_key.values()
                          for f in s.fields)


# ------------------------------------------------- destructive-action style


def test_danger_red_is_a_distinct_hex():
    assert xx_theme.DANGER_RED.startswith("#")
    assert len(xx_theme.DANGER_RED) == 7
    for preset in xx_theme.PRESETS.values():
        assert xx_theme.DANGER_RED.lower() != preset.background.lower()


def test_css_colors_destructive_action_from_preset(isolated_home):
    css = xx_css.render(xx_theme.load())
    assert ".destructive-action" in css
    assert "button.destructive-action" in css
    assert xx_theme.DANGER_RED in css


def test_delete_buttons_carry_danger_style():
    assert 'add_css_class("xx-danger-text")' in _read("detail_ui.py")
    list_src = _read("list_ui.py")
    assert 'add_css_class("xx-danger-text")' in list_src
    assert list_src.count("xx-danger-text") >= 1
    for name in ("list_ui.py", "detail_ui.py"):
        assert "DESTRUCTIVE" in _read(name)


# ------------------------------------------------- list guards (static)


def test_single_tap_activates_rows():
    src = _read("list_ui.py")
    assert "set_single_click_activate(True)" in src


def test_no_autoselecting_selection_model():
    src = _read("list_ui.py")
    assert "Gtk.NoSelection" in src
    assert "Gtk.SingleSelection" not in src


def test_search_entry_only_focuses_from_explicit_tap():
    src = _read("list_ui.py")
    assert "install_tap_to_focus(self._search)" in src
    assert "set_can_focus(False)" in src
    assert "set_focusable(False)" in src
    assert "PropagationPhase.CAPTURE" in src
    assert "EventControllerFocus" in src
    assert 'connect("leave"' in src
    assert 'connect("notify::has-focus"' not in src
    assert src.count("grab_focus") == 1
    start = src.index("def _on_tap_to_focus_pressed")
    end = src.index("def _on_tap_to_focus_left", start)
    assert "grab_focus" in src[start:end]


def test_row_menu_still_resolves_row_under_pointer():
    src = _read("list_ui.py")
    assert "self._list.pick(" in src
    assert "resolve_row_uid" in src
    assert "get_selected_item" not in src


# ------------------------------------------- tap-to-focus logic (headless)


class _FakeEntry:
    def __init__(self) -> None:
        self.focusable = True
        self.can_focus = True
        self.grabs = 0

    def set_focusable(self, value: bool) -> None:
        self.focusable = value

    def set_can_focus(self, value: bool) -> None:
        self.can_focus = value

    def grab_focus(self) -> None:
        self.grabs += 1


def test_tap_makes_entry_focusable_and_grabs_focus():
    entry = _FakeEntry()
    _on_tap_to_focus_pressed(None, 1, 0.0, 0.0, entry)
    assert entry.focusable is True
    assert entry.can_focus is True
    assert entry.grabs == 1


def test_leaving_entry_locks_the_whole_subtree_again():
    entry = _FakeEntry()
    _on_tap_to_focus_pressed(None, 1, 0.0, 0.0, entry)
    _on_tap_to_focus_left(None, entry)
    assert entry.focusable is False
    assert entry.can_focus is False


# ------------------------------------------------- GTK test (display only)


def test_search_tap_to_focus_with_real_entry():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk

    from xx_contacts.list_ui import install_tap_to_focus

    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        pytest.skip("no display available")
    entry = Gtk.SearchEntry()
    install_tap_to_focus(entry)
    assert not entry.get_focusable()
    assert not entry.get_can_focus()
    _on_tap_to_focus_pressed(None, 1, 0.0, 0.0, entry)
    assert entry.get_focusable()
    assert entry.get_can_focus()
    _on_tap_to_focus_left(None, entry)
    assert not entry.get_focusable()
    assert not entry.get_can_focus()
