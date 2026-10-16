"""L2 refresh tests: external-change pickup without a display.

``ChangeWatcher`` needs no widgets, so the directory-monitor filter and
the D-Bus ``Changed`` parsing run headless against real Gio objects.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gio, GLib, Gtk  # noqa: E402

from xx_contacts.list_model import action_uri, call_uri, email_uri  # noqa: E402
from xx_contacts.list_model import ListRow, message_uri  # noqa: E402
from xx_contacts.list_ui import ChangeWatcher  # noqa: E402


def _watcher(seen, db_path="/tmp/xx/contacts.db"):
    return ChangeWatcher(db_path, lambda **kwargs: seen.append(kwargs))


def test_dir_monitor_watches_db_and_sidecars_only():
    seen = []
    watcher = _watcher(seen)
    new_file = Gio.File.new_for_path("/tmp/xx/contacts.db-wal")
    watcher._on_dir_changed(None, new_file, None, 0)
    assert seen == [{}]
    seen.clear()
    other = Gio.File.new_for_path("/tmp/xx/config.json")
    watcher._on_dir_changed(None, other, None, 0)
    assert seen == []
    seen.clear()
    watcher._on_dir_changed(None, None, None, 0)
    assert seen == []


def test_changed_signal_forwards_revision_and_uids():
    seen = []
    watcher = _watcher(seen)
    params = GLib.Variant("(tas)", (7, ["uid-1", "uid-2"]))
    watcher._on_changed_signal(None, None, "/io/piercingxx/Contacts",
                               "io.piercingxx.Contacts1", "Changed", params)
    assert seen == [{"revision": 7, "uids": ["uid-1", "uid-2"]}]


def test_changed_signal_ignores_garbage():
    seen = []
    watcher = _watcher(seen)
    watcher._on_changed_signal(None, None, "", "", "Changed",
                               GLib.Variant("(s)", ("nope",)))
    assert seen == []


def test_gtk_accepts_row_action_uris():
    row = ListRow(uid="u", display_name="Ada",
                  primary_number="+1 555 0100",
                  primary_email="ada@example.org")
    for uri in (call_uri(row.primary_number),
                message_uri(row.primary_number),
                email_uri(row.primary_email)):
        assert Gtk.UriLauncher.new(uri).get_uri() == uri
    assert action_uri("call", row).startswith("tel:")
    assert action_uri("message", row).startswith("sms:")
    assert action_uri("email", row).startswith("mailto:")
