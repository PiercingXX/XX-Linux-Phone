"""Logs page: ring buffer plus file tail, copyable, redacted.

Ports log/LogsUi.kt: the on-device dump (shared ring plus the state
log file tail) renders as selectable text, and Copy puts the same
redacted text on the clipboard. Nothing is uploaded. Thin view —
hooks load the text off the main loop.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk

from xx_email.ui import common as common_mod

EMPTY_TEXT = "(no lines yet)"


class LogsPage(Gtk.Box):
    """Scrollable redacted log with Copy (see hooks)."""

    def __init__(self, hooks, toast) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._hooks = hooks
        self._toast = toast
        self._text = ""

        header = common_mod.phone_header("Logs")
        back = common_mod.text_button("Back")
        back.connect("clicked", lambda _b: hooks.go_back())
        header.pack_start(back)
        copy = common_mod.text_button("Copy")
        copy.connect("clicked", lambda _b: self._copy())
        header.pack_end(copy)
        self.append(header)

        note = common_mod.wrapped(Gtk.Label(
            label="On-device log. Tokens, auth headers and addresses "
                  "are scrubbed before display — nothing leaves the "
                  "phone unless you paste it."))
        note.add_css_class("email-muted")
        note.set_margin_start(24)
        note.set_margin_end(24)
        self.append(note)

        self._buffer = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=self._buffer)
        view.set_editable(False)
        view.set_monospace(True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_hexpand(True)
        view.set_vexpand(True)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_child(view)
        scrolled.set_margin_start(12)
        scrolled.set_margin_end(12)
        scrolled.set_margin_bottom(12)
        self.append(scrolled)
        hooks.load_logs(self._on_loaded)

    def _on_loaded(self, result) -> None:
        if isinstance(result, Exception):
            self._toast("Couldn't load the log", None)
            return
        self._text = (result or "").strip() or EMPTY_TEXT
        self._buffer.set_text(self._text, -1)

    def _copy(self) -> None:
        text = self._text.strip() or EMPTY_TEXT
        display = Gdk.Display.get_default()
        if display is None:
            self._toast("Couldn't reach the clipboard", None)
            return
        try:
            display.get_clipboard().set(text)
        except Exception:
            self._toast("Couldn't reach the clipboard", None)
            return
        self._toast("Copied", None)


def logs_navigation_page(page: LogsPage) -> Adw.NavigationPage:
    navigation = Adw.NavigationPage(title="Logs")
    navigation.set_child(page)
    return navigation
