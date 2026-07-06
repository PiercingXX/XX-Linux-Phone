from __future__ import annotations

import gi
import re
import subprocess

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gio, Gtk
from contacts import Contact, ContactBook

_DIALER_CSS = b"""
.dialer-root {
    background: #000000;
    color: #f4f4f4;
    font-family: 'Space Mono', monospace;
}
.dialer-display {
    font-size: 28pt;
    font-weight: 300;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.05em;
    color: #f4f4f4;
    min-height: 72px;
}
.dialer-button {
    font-size: 20pt;
    font-weight: 300;
    font-family: 'Space Mono', monospace;
    min-width: 100px;
    min-height: 80px;
    border-radius: 50%;
    background: #111111;
    color: #f4f4f4;
    border: none;
    padding: 0;
}
.dialer-button:hover { background: #1e1e1e; }
.dialer-sub {
    font-size: 8pt;
    color: #9a9a9a;
    margin-top: -2px;
}
.call-button {
    font-size: 14pt;
    font-family: 'Space Mono', monospace;
    min-width: 100px;
    min-height: 80px;
    border-radius: 50%;
    background: #f4f4f4;
    color: #000000;
    border: none;
}
.call-button:hover { background: #e0e0e0; }
.del-button {
    font-size: 14pt;
    min-width: 100px;
    min-height: 80px;
    border-radius: 50%;
    background: transparent;
    color: #9a9a9a;
    border: none;
}
.del-button:hover { background: #111111; }
.contact-row {
    font-size: 14pt;
    padding: 12px 16px;
}
.contact-name { font-size: 14pt; color: #f4f4f4; }
.contact-number { font-size: 10pt; color: #9a9a9a; }
"""

_KEYPAD: list[tuple[str, str]] = [
    ('1', ''),   ('2', 'ABC'), ('3', 'DEF'),
    ('4', 'GHI'),('5', 'JKL'), ('6', 'MNO'),
    ('7', 'PQRS'),('8', 'TUV'),('9', 'WXYZ'),
    ('*', ''),   ('0', '+'),   ('#', ''),
]


class Dialer(Gtk.Window):
    """Standalone dialer window — numeric keypad + contact search + call via ModemManager."""

    def __init__(self) -> None:
        super().__init__(title='Dialer')
        self.set_default_size(420, 860)

        self._digits = ''
        self._contact_book = ContactBook()

        provider = Gtk.CssProvider()
        provider.load_from_data(_DIALER_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 4,
        )

        self.set_child(self._build())
        self._update_display()

    def _build(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class('dialer-root')
        root.set_margin_top(16)
        root.set_margin_start(16)
        root.set_margin_end(16)
        root.set_margin_bottom(24)

        # Header row: back chevron
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        back_btn = Gtk.Button(label='‹ Back')
        back_btn.add_css_class('flat')
        back_btn.connect('clicked', lambda _b: self.close())
        header.append(back_btn)
        header.set_margin_bottom(8)
        root.append(header)

        # Swipe down anywhere to close
        swipe = Gtk.GestureSwipe.new()
        swipe.connect('swipe', lambda _g, vx, vy: self.close() if vy > 300 else None)
        root.add_controller(swipe)

        # Number display
        self._display = Gtk.Label(label='', xalign=1)
        self._display.add_css_class('dialer-display')
        self._display.set_hexpand(True)
        self._display.set_margin_bottom(16)
        self._display.set_margin_start(8)
        self._display.set_margin_end(8)

        # Contact suggestions (shown while typing)
        self._suggestions = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._suggestions.add_css_class('text-list')
        self._suggestions.set_visible(False)

        # Keypad
        keypad = Gtk.Grid(row_spacing=10, column_spacing=10)
        keypad.set_halign(Gtk.Align.CENTER)
        keypad.set_margin_bottom(16)

        for idx, (digit, sub) in enumerate(_KEYPAD):
            col, row = idx % 3, idx // 3
            btn = self._make_digit_btn(digit, sub)
            keypad.attach(btn, col, row, 1, 1)

        # Bottom action row: delete, call, (empty)
        action_row = Gtk.Grid(row_spacing=0, column_spacing=10)
        action_row.set_halign(Gtk.Align.CENTER)

        placeholder = Gtk.Box()
        placeholder.set_size_request(100, 80)

        del_btn = Gtk.Button(label='←')
        del_btn.add_css_class('del-button')
        del_btn.connect('clicked', self._on_delete)
        del_btn.connect('pressed', None)  # placeholder for long-press to clear all

        long_press = Gtk.GestureLongPress.new()
        long_press.connect('pressed', lambda *_: self._clear_all())
        del_btn.add_controller(long_press)

        call_btn = Gtk.Button(label='Call')
        call_btn.add_css_class('call-button')
        call_btn.connect('clicked', self._on_call)

        action_row.attach(placeholder, 0, 0, 1, 1)
        action_row.attach(call_btn, 1, 0, 1, 1)
        action_row.attach(del_btn, 2, 0, 1, 1)

        root.append(self._display)
        root.append(self._suggestions)
        root.append(keypad)
        root.append(action_row)
        return root

    def _make_digit_btn(self, digit: str, sub: str) -> Gtk.Button:
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)

        d_label = Gtk.Label(label=digit)
        inner.append(d_label)

        if sub:
            s_label = Gtk.Label(label=sub)
            s_label.add_css_class('dialer-sub')
            inner.append(s_label)

        btn = Gtk.Button()
        btn.add_css_class('dialer-button')
        btn.set_child(inner)
        btn.connect('clicked', self._on_digit, digit)
        return btn

    def _on_digit(self, _btn: Gtk.Button, digit: str) -> None:
        if len(self._digits) < 20:
            self._digits += digit
            self._update_display()
            self._refresh_suggestions()

    def _on_delete(self, _btn: Gtk.Button) -> None:
        if self._digits:
            self._digits = self._digits[:-1]
            self._update_display()
            self._refresh_suggestions()

    def _clear_all(self) -> None:
        self._digits = ''
        self._update_display()
        self._refresh_suggestions()

    def _update_display(self) -> None:
        self._display.set_text(self._format_number(self._digits))

    def _format_number(self, digits: str) -> str:
        if not digits:
            return ''
        if len(digits) <= 10 and digits.isdigit():
            # Format as (XXX) XXX-XXXX for 10-digit US numbers
            if len(digits) == 10:
                return f'({digits[:3]}) {digits[3:6]}-{digits[6:]}'
            if len(digits) == 7:
                return f'{digits[:3]}-{digits[3:]}'
        return digits

    def _refresh_suggestions(self) -> None:
        child = self._suggestions.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._suggestions.remove(child)
            child = nxt

        if not self._digits:
            self._suggestions.set_visible(False)
            return

        matches = self._contact_book.search(self._digits, limit=4)
        if not matches:
            self._suggestions.set_visible(False)
            return

        for contact in matches:
            row = self._make_suggestion_row(contact)
            self._suggestions.append(row)

        self._suggestions.set_visible(True)

    def _make_suggestion_row(self, contact: Contact) -> Gtk.ListBoxRow:
        name_lbl = Gtk.Label(label=contact.name, xalign=0)
        name_lbl.add_css_class('contact-name')

        num_lbl = Gtk.Label(label=contact.primary_number(), xalign=0)
        num_lbl.add_css_class('contact-number')

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        inner.set_margin_start(4)
        inner.append(name_lbl)
        inner.append(num_lbl)

        row = Gtk.ListBoxRow(selectable=False, activatable=True)
        row.add_css_class('contact-row')
        row.set_child(inner)
        row.connect('activate', lambda _r, c=contact: self._fill_from_contact(c))
        return row

    def _fill_from_contact(self, contact: Contact) -> None:
        num = re.sub(r'[^\d+]', '', contact.primary_number())
        self._digits = num
        self._update_display()
        self._suggestions.set_visible(False)

    def _on_call(self, _btn: Gtk.Button) -> None:
        if not self._digits:
            return
        self._initiate_call(self._digits)

    def _initiate_call(self, number: str) -> None:
        # Try ModemManager via mmcli
        try:
            subprocess.Popen(['mmcli', '-m', '0', f'--voice-call={number}'], close_fds=True)
        except FileNotFoundError:
            pass
