"""Keypad page: large keys, T9 matches, label-driven number field.

The number field is a ``Gtk.Label`` driven by the keypad — never a
focusable text entry — so typing here can never pop the on-screen
keyboard. Long-press 0 yields ``+``, long-press 1 dials voicemail when
``voicemail_number`` is configured, and a long-press on Delete clears
the whole entry. A consumed long-press suppresses the click, so the
press never double-applies. Calls go out through ``modem.VoiceClient``;
the shell owns the in-call UI, so this page only places the call.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from xx_dialer import contacts_client, policy  # noqa: E402
from xx_dialer.store import Store  # noqa: E402

MAX_MATCHES = 6
MIN_SAVE_DIGITS = 3

_KEY_LETTERS = {
    "2": "ABC", "3": "DEF", "4": "GHI", "5": "JKL",
    "6": "MNO", "7": "PQRS", "8": "TUV", "9": "WXYZ",
}


@dataclass
class KeypadDeps:
    db_file: Path
    home: Path | None = None
    voice: object = None
    contacts: object = None
    notice: object = None


class KeypadBuffer:
    """The dialed digits; the label renders exactly this."""

    def __init__(self, digits: str = "") -> None:
        self.digits = digits

    def append(self, text: str) -> None:
        self.digits += text

    def backspace(self) -> None:
        self.digits = self.digits[:-1]

    def clear(self) -> None:
        self.digits = ""

    def set(self, digits: str) -> None:
        self.digits = digits

    @property
    def can_call(self) -> bool:
        return bool(self.digits)


def resolve_voicemail_number(config: dict) -> str | None:
    """Configured voicemail dial-in, or None when it is not set."""
    raw = (config.get("voicemail_number") or "").strip()
    return raw or None


def dial_target(raw: str) -> str:
    """What actually gets dialed: normalized, or the raw input as-is."""
    return policy.normalize_number(raw) or raw


class KeypadView:
    """The Keypad page widget plus its keypad logic."""

    def __init__(self, deps: KeypadDeps,
                 buffer: KeypadBuffer | None = None) -> None:
        self.deps = deps
        self.buffer = buffer or KeypadBuffer()
        self._suppress_click: set = set()
        self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             spacing=6)
        self._toast = Adw.ToastOverlay()
        self._build()
        self.refresh()

    @property
    def widget(self) -> Gtk.Widget:
        return self._toast

    def _notice(self, text: str) -> None:
        if self.deps.notice is not None:
            try:
                self.deps.notice(text)
                return
            except Exception:
                pass
        try:
            self._toast.add_toast(Adw.Toast.new(text))
        except Exception:
            pass

    def _build(self) -> None:
        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=6)
        self._number = Gtk.Label(label="", xalign=0.5, hexpand=True)
        self._number.set_ellipsize(Pango.EllipsizeMode.END)
        self._number.set_selectable(False)
        self._number.set_can_focus(False)
        self._number.set_focusable(False)
        self._number.add_css_class("xx-number")
        paste = Gtk.GestureLongPress.new()
        paste.connect("pressed", self._on_paste_press)
        self._number.add_controller(paste)
        entry_row.append(self._number)
        self._delete = Gtk.Button(label="Delete")
        self._delete.connect("clicked", self._on_delete_click)
        clear_press = Gtk.GestureLongPress.new()
        clear_press.connect("pressed", self._on_delete_long)
        clear_press.connect("cancel", self._on_press_cancel)
        self._delete.add_controller(clear_press)
        entry_row.append(self._delete)
        self._root.append(entry_row)

        match_scroll = Gtk.ScrolledWindow()
        match_scroll.set_policy(Gtk.PolicyType.NEVER,
                                Gtk.PolicyType.AUTOMATIC)
        match_scroll.set_max_content_height(168)
        match_scroll.set_propagate_natural_height(True)
        self._matches = Gtk.ListBox()
        self._matches.set_selection_mode(Gtk.SelectionMode.NONE)
        match_scroll.set_child(self._matches)
        self._root.append(match_scroll)
        self._match_scroll = match_scroll

        grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        grid.set_column_homogeneous(True)
        grid.set_row_homogeneous(True)
        self._keys: dict[str, Gtk.Button] = {}
        for position, digit in enumerate(
                ["1", "2", "3", "4", "5", "6", "7", "8", "9",
                 "*", "0", "#"]):
            button = Gtk.Button()
            letters = _KEY_LETTERS.get(digit, "")
            if letters:
                inner = Gtk.Label(
                    label="<big>%s</big>\n<small>%s</small>"
                    % (digit, letters),
                    use_markup=True, justify=Gtk.Justification.CENTER)
            else:
                inner = Gtk.Label(
                    label="<big>%s</big>" % digit, use_markup=True,
                    justify=Gtk.Justification.CENTER)
            inner.set_can_focus(False)
            button.set_child(inner)
            button.set_hexpand(True)
            button.set_vexpand(True)
            button.add_css_class("xx-key")
            button.connect("clicked", self._on_key, digit)
            if digit == "0":
                press = Gtk.GestureLongPress.new()
                press.connect("pressed", self._on_zero_long)
                press.connect("cancel", self._on_press_cancel)
                button.add_controller(press)
            if digit == "1":
                press = Gtk.GestureLongPress.new()
                press.connect("pressed", self._on_one_long)
                press.connect("cancel", self._on_press_cancel)
                button.add_controller(press)
            self._keys[digit] = button
            grid.attach(button, position % 3, position // 3, 1, 1)
        grid.set_vexpand(True)
        self._root.append(grid)

        self._call = Gtk.Button(label="Call")
        self._call.add_css_class("suggested-action")
        self._call.connect("clicked", self._on_call_click)
        self._root.append(self._call)
        self._toast.set_child(self._root)

    # ---- keys -------------------------------------------------------------

    def press_key(self, digit: str) -> None:
        self.buffer.append(digit)
        self.refresh()

    def _on_key(self, button: Gtk.Button, digit: str) -> None:
        if button in self._suppress_click:
            self._suppress_click.discard(button)
            return
        self.press_key(digit)

    def _on_press_cancel(self, gesture, _sequence) -> None:
        self._suppress_click.discard(gesture.get_widget())

    def _on_zero_long(self, gesture, _x: float, _y: float) -> None:
        button = gesture.get_widget()
        self._suppress_click.add(button)
        self.buffer.append("+")
        self.refresh()

    def _on_one_long(self, gesture, _x: float, _y: float) -> None:
        button = gesture.get_widget()
        self._suppress_click.add(button)
        self.dial_voicemail()

    def dial_voicemail(self) -> None:
        try:
            from xx_dialer import config as app_config

            number = resolve_voicemail_number(
                app_config.load(self.deps.home))
        except Exception:
            number = None
        if not number:
            self._notice("Voicemail number is not set.")
            return
        self.place(number)

    def _on_delete_click(self, _button: Gtk.Button) -> None:
        if _button in self._suppress_click:
            self._suppress_click.discard(_button)
            return
        self.buffer.backspace()
        self.refresh()

    def _on_delete_long(self, gesture, _x: float, _y: float) -> None:
        button = gesture.get_widget()
        self._suppress_click.add(button)
        self.buffer.clear()
        self.refresh()

    def _on_paste_press(self, _gesture, _x: float, _y: float) -> None:
        try:
            display = Gdk.Display.get_default()
            clipboard = display.get_clipboard() if display else None
        except Exception:
            clipboard = None
        if clipboard is None:
            self._notice("Clipboard is empty.")
            return

        def _done(_clipboard, result) -> None:
            try:
                pasted = clipboard.read_text_finish(result) or ""
            except Exception:
                pasted = ""
            cleaned = "".join(ch for ch in pasted
                              if ch.isdigit() or ch in "+*#")
            if not cleaned:
                self._notice("Clipboard is empty.")
                return
            self.buffer.append(cleaned)
            self.refresh()

        try:
            clipboard.read_text_async(None, _done)
        except Exception:
            self._notice("Clipboard is empty.")

    def _on_call_click(self, _button: Gtk.Button) -> None:
        if not self.buffer.can_call:
            return
        self.place(dial_target(self.buffer.digits))

    def place(self, number: str) -> None:
        voice = self.deps.voice
        if voice is None:
            self._notice("No modem is available to place the call.")
            return

        def _done(ok: bool, detail) -> None:
            if not ok:
                self._notice("Could not place the call: %s"
                             % (detail or "modem error"))

        try:
            voice.place(number, _done)
        except Exception as err:
            self._notice("Could not place the call: %s" % (err,))

    def set_digits(self, digits: str) -> None:
        self.buffer.set(digits or "")
        self.refresh()

    # ---- T9 matches ---------------------------------------------------------

    def match_rows(self) -> list[tuple[str, str, str]]:
        """Current (uid, number, name) T9 hits, capped for the overlay."""
        query = self.buffer.digits
        if not query:
            return []
        try:
            with Store(self.deps.db_file) as store:
                hits = contacts_client.keypad_search(
                    store, query, MAX_MATCHES)
        except Exception:
            return []
        return [(uid, number, name) for uid, number, name, _hit in hits]

    def refresh(self) -> None:
        self._number.set_text(self.buffer.digits or "Enter number")
        self._call.set_sensitive(self.buffer.can_call)
        while True:
            row = self._matches.get_row_at_index(0)
            if row is None:
                break
            self._matches.remove(row)
        hits = self.match_rows()
        for uid, number, name in hits:
            button = Gtk.Button()
            inner = Gtk.Label(
                label="<b>%s</b>\n%s" % (
                    GLib.markup_escape_text(name or number),
                    GLib.markup_escape_text(number)),
                use_markup=True, xalign=0.0,
                ellipsize=Pango.EllipsizeMode.END)
            inner.set_can_focus(False)
            button.set_child(inner)

            def _picked(_btn, picked=number) -> None:
                self.buffer.set(picked)
                self.refresh()

            button.connect("clicked", _picked)
            self._matches.append(button)
        digit_count = sum(ch.isdigit() for ch in self.buffer.digits)
        if not hits and digit_count >= MIN_SAVE_DIGITS:
            number = dial_target(self.buffer.digits)
            save = Gtk.Button(
                label="Save %s to contacts" % number)
            save.connect("clicked", self._on_save, number)
            self._matches.append(save)
        self._match_scroll.set_visible(
            bool(hits) or digit_count >= MIN_SAVE_DIGITS)

    def _on_save(self, _button: Gtk.Button, number: str) -> None:
        client = self.deps.contacts
        if client is None:
            self._notice("Contacts app is not installed.")
            return

        def _done(_uid: str) -> None:
            self._notice("Contact saved.")

        def _failed(_err) -> None:
            self._notice("Could not save the contact.")

        try:
            client.create_async(
                contacts_client.new_contact_vcard(number), _done,
                _failed)
        except Exception:
            self._notice("Could not save the contact.")


def build_keypad(deps: KeypadDeps,
                 buffer: KeypadBuffer | None = None) -> KeypadView:
    return KeypadView(deps, buffer)
