"""L4 phone-rule guards: static checks over the UI sources.

GTK behavior that headless tests cannot execute (focus, list
activation, title buttons, dialog arity, D-Bus reply types) is pinned
here as text guards so a regression fails loudly instead of popping
the on-screen keyboard on the device.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "txxt"

UI_WITH_LISTS = ("conversations.py", "thread_page.py")
UI_WRITERS = ("main.py", "conversations.py", "thread_page.py", "compose.py")


def read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


# ---- display / focus ----

def test_no_get_display_on_app_uses_default_display():
    src = read("main.py")
    assert "get_display()" not in src
    assert "Gdk.Display.get_default()" in src


def test_no_grab_focus_at_startup():
    assert "grab_focus" not in read("main.py")
    for name in UI_WITH_LISTS:
        current = ""
        for line in read(name).splitlines():
            match = re.match(r"\s*def (\w+)", line)
            if match:
                current = match.group(1)
            if "grab_focus" in line:
                assert "tap" in current or "click" in current, (name, current)


def test_entries_unfocusable_until_tapped():
    helper = read("conversations.py")
    for token in ("set_can_focus(False)", "PropagationPhase.CAPTURE",
                  "EventControllerFocus", "def tap_to_focus"):
        assert token in helper, token
    assert "set_focusable(False)" not in helper
    for name in UI_WITH_LISTS:
        src = read(name)
        assert "tap_to_focus" in src, name
        assert "set_can_focus(False)" in src or "tap_to_focus" in src, name


# ---- lists ----

def test_every_listview_single_click_no_selection():
    for name in UI_WITH_LISTS:
        src = read(name)
        assert src.count("Gtk.ListView.new") >= 1, name
        assert src.count("Gtk.ListView.new") == src.count(
            "set_single_click_activate(True)"), name
        assert "NoSelection.new" in src, name
    for name in UI_WITH_LISTS:
        assert "SingleSelection" not in read(name), name


def test_long_press_resolves_row_under_pointer():
    for name in ("conversations.py",):
        src = read(name)
        assert "Gtk.GestureLongPress.new" in src, name
        assert ".pick(" in src, name
        assert "get_parent()" in src, name


# ---- header bars / layout ----

def test_title_buttons_hidden_everywhere():
    for name in UI_WITH_LISTS:
        src = read(name)
        headers = src.count("Adw.HeaderBar(")
        assert headers >= 1, name
        assert src.count("set_show_start_title_buttons(False)") == headers, name
        assert src.count("set_show_end_title_buttons(False)") == headers, name


def test_phone_layout_bounds():
    assert "set_size_request(320" in read("main.py")
    assert "360, 720" in read("main.py")
    assert "set_ellipsize" in read("conversations.py")
    assert "set_wrap" in read("thread_page.py")


# ---- dialogs: parent first ----

def test_alert_dialogs_choose_parent_first():
    for name in ("conversations.py", "thread_page.py"):
        assert "Adw.AlertDialog.new" in read(name), name
    for path in sorted(SRC.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        assert re.search(r"\.choose\(\s*None", src) is None, path.name


# ---- D-Bus reply types: VariantType or None, never a string ----

def test_reply_types_are_variant_or_none():
    src = read("txxt_client.py")
    assert "VariantType.new(" in src
    assert "call_sync" not in src
    for path in sorted(SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert re.search(r"reply_type\s*=\s*['\"]", text) is None, path.name


# ---- no polling: refresh off MessagesChanged ----

def test_no_polling_signal_drives_refresh():
    for name in ("main.py", "conversations.py", "thread_page.py",
                 "txxt_client.py"):
        assert "timeout_add" not in read(name), name
    assert "signal_subscribe" in read("txxt_client.py")
    assert "MessagesChanged" in read("txxt_client.py")
    assert "MessagesChanged" in read("main.py")


# ---- Android-style ids need 64-bit properties ----

def test_android_ids_use_int64_properties():
    conv = read("conversations.py")
    assert "conversation_id = GObject.Property(type=int" not in conv
    assert "TYPE_INT64" in conv
    thread = read("thread_page.py")
    assert "message_id = GObject.Property(type=int" not in thread
    assert "TYPE_INT64" in thread


# ---- daemon-only message path ----

def test_ui_never_touches_modem_or_message_rows():
    banned = ("ModemManager", "mm_client", "persist_inbound",
              "persist_outgoing", "mark_sent", "create_and_send")
    for name in UI_WRITERS:
        src = read(name)
        for token in banned:
            assert token not in src, (name, token)


def test_send_and_mark_read_go_through_the_client():
    assert "self._client.send" in read("thread_page.py")
    assert "self._client.mark_read" in read("thread_page.py")
    assert "self._client.mark_read" in read("conversations.py")
    for name in ("main.py", "conversations.py", "thread_page.py"):
        assert "TxxtClient" in read(name) or "txxt_client" in read(name), name


# ---- theme: no white lists, lists and entries painted ----

def test_every_preset_paints_lists_without_white():
    from txxt import theme

    keys = list(theme.THEME_PRESETS) + ["custom"]
    assert len(keys) == 9
    for key in keys:
        active = theme.resolve_theme({"theme": key})
        css = theme.css_for_theme(active, "Space Mono, Monospace").lower()
        assert "listview" in css, key
        assert "listview row" in css, key
        assert "row:selected" in css, key
        assert "entry" in css, key
        assert "textview" in css, key
        assert "#fff" not in css, key
        assert "white" not in css, key
