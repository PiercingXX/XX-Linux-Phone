"""Phone-trap guards for the share window: static source checks.

These mirror XXWM_RULES.md's runtime traps so a regression fails here
instead of on the device. No GTK instantiation, so they run headless.
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
UI = (SRC / "ui.py").read_text(encoding="utf-8")


def test_no_application_get_display():
    for line in UI.splitlines():
        if "get_display()" in line:
            assert "Display.get_default()" in line, line


def test_display_level_theming():
    assert "add_provider_for_display" in UI
    assert "Gdk.Display.get_default()" in UI


def test_lists_follow_theme():
    css = UI[UI.index("def theme_css"):]
    for token in ("listview", "background-color"):
        assert token in css
    assert "listview" in css and "surface" in css


def test_no_entry_autofocus_at_startup():
    for guilty in ("alias.grab_focus", "pin.grab_focus",
                   "send_pin.grab_focus", "entry.grab_focus"):
        assert guilty not in UI
    assert "self.send_button.grab_focus()" in UI


def test_no_minimize_maximize_buttons():
    assert "set_show_start_title_buttons(False)" in UI
    assert "set_show_end_title_buttons(False)" in UI
    assert '":close"' in UI


def test_long_lists_use_list_view():
    assert "Gtk.ListView" in UI
    assert "Gtk.SignalListItemFactory" in UI
    assert "Gtk.SingleSelection" in UI


def test_file_picker_uses_modern_dialog():
    assert "Gtk.FileDialog" in UI
    assert "open_multiple" in UI
    assert "open_multiple_finish" in UI
    assert "FileChooserDialog" not in UI


def test_share_window_scope_present():
    for token in ("Cancel", "cancel_send", "Clear history", "history_list",
                  "Favorite selected", "Auto-accept", "auto-off",
                  "Receiver PIN", "TransferDone", "IncomingRequest"):
        assert token in UI, token


def test_service_wires_bus_signals_and_history():
    service = (SRC / "service.py").read_text(encoding="utf-8")
    for token in ("HistoryStore", "TransferManager", "IncomingRequest",
                  "TransferProgress", "TransferDone", "start_send",
                  "cancel_send", "visibility_note"):
        assert token in service, token


def test_desktop_entry_and_unit():
    root = SRC.parent
    desktop = (root / "data" / "io.piercingxx.XXShare.desktop.in").read_text()
    assert "Send with xx-share" in desktop
    assert "--send" in desktop
    unit = (root / "data" / "systemd" / "xx-share.service.in").read_text()
    assert "--service" in unit
    main = (SRC / "main.py").read_text(encoding="utf-8")
    assert "BusServer" in main
    assert "MainLoop" in main
    assert "BusBackend" in main
