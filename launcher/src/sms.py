from __future__ import annotations

import gi
import subprocess
import threading
from datetime import datetime, timezone

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gtk
from contacts import ContactBook, load_sms_history

_SMS_CSS = b"""
.sms-root {
    background: #000000;
    color: #f4f4f4;
    font-family: 'Space Mono', monospace;
}
.sms-header {
    font-size: 13pt;
    font-weight: 600;
    color: #f4f4f4;
    padding: 16px 20px;
    border-bottom: 1px solid #1a1a1a;
}
.sms-list {
    background: transparent;
    padding: 12px;
}
.bubble-out {
    background: #f4f4f4;
    color: #000000;
    border-radius: 18px 18px 4px 18px;
    padding: 12px 16px;
    margin-left: 60px;
    margin-bottom: 4px;
    font-size: 13pt;
}
.bubble-in {
    background: #1a1a1a;
    color: #f4f4f4;
    border-radius: 18px 18px 18px 4px;
    padding: 12px 16px;
    margin-right: 60px;
    margin-bottom: 4px;
    font-size: 13pt;
}
.bubble-time {
    font-size: 9pt;
    color: #9a9a9a;
    margin-bottom: 8px;
}
.sms-input {
    font-size: 13pt;
    font-family: 'Space Mono', monospace;
    min-height: 52px;
    background: #111111;
    color: #f4f4f4;
    border-radius: 26px;
    border: 1px solid #2a2a2a;
    padding: 0 16px;
}
.sms-send {
    font-size: 13pt;
    font-family: 'Space Mono', monospace;
    min-width: 64px;
    min-height: 52px;
    border-radius: 26px;
    background: #f4f4f4;
    color: #000000;
    border: none;
}
.sms-send:hover { background: #e0e0e0; }
"""


class Message:
    __slots__ = ('text', 'outgoing', 'timestamp')

    def __init__(self, text: str, outgoing: bool, timestamp: datetime | None = None) -> None:
        self.text = text
        self.outgoing = outgoing
        self.timestamp = timestamp or datetime.now()


class SMSConversation(Gtk.Window):
    """
    SMS conversation view for a single contact/number.
    Backend: mmcli (ModemManager) for send/receive.
    Use open_conversation(number, display_name) to show.
    """

    def __init__(self) -> None:
        super().__init__(title='Messages')
        self.set_default_size(420, 860)

        self._number = ''
        self._messages: list[Message] = []
        self._contact_book = ContactBook()

        provider = Gtk.CssProvider()
        provider.load_from_data(_SMS_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 4,
        )

        self.set_child(self._build())

    def _build(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class('sms-root')

        self._header_label = Gtk.Label(label='', xalign=0)
        self._header_label.add_css_class('sms-header')
        self._header_label.set_hexpand(True)

        self._msg_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._msg_list.add_css_class('sms-list')
        self._msg_list.set_vexpand(True)

        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self._msg_list)

        self._input = Gtk.Entry()
        self._input.add_css_class('sms-input')
        self._input.set_placeholder_text('Message')
        self._input.set_hexpand(True)
        self._input.connect('activate', self._on_send)

        send_btn = Gtk.Button(label='Send')
        send_btn.add_css_class('sms-send')
        send_btn.connect('clicked', self._on_send)

        input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        input_row.set_margin_start(12)
        input_row.set_margin_end(12)
        input_row.set_margin_top(8)
        input_row.set_margin_bottom(20)
        input_row.append(self._input)
        input_row.append(send_btn)

        root.append(self._header_label)
        root.append(scroller)
        root.append(input_row)

        self._scroller = scroller
        return root

    def open_conversation(self, number: str, display_name: str = '') -> None:
        self._number = number
        # Prefer caller-supplied name, then contact book lookup, then raw number
        resolved = display_name or self._contact_book.lookup_number(number) or number
        self._header_label.set_text(resolved)
        self._messages.clear()
        self._rebuild_messages()
        self._load_history()
        self.present()

    def receive_message(self, text: str) -> None:
        self._messages.append(Message(text, outgoing=False))
        self._append_bubble(self._messages[-1])
        self._scroll_to_bottom()

    def _on_send(self, _widget: Gtk.Widget) -> None:
        text = self._input.get_text().strip()
        if not text or not self._number:
            return
        self._input.set_text('')
        self._send_sms(self._number, text)
        msg = Message(text, outgoing=True)
        self._messages.append(msg)
        self._append_bubble(msg)
        self._scroll_to_bottom()

    def _send_sms(self, number: str, text: str) -> None:
        try:
            subprocess.Popen(
                ['mmcli', '-m', '0', f'--messaging-create-sms=number={number},text={text}'],
                close_fds=True,
            )
        except FileNotFoundError:
            pass

    def _load_history(self) -> None:
        # Run mmcli history fetch in a background thread — avoids blocking the UI
        # while mmcli queries ModemManager over DBus for potentially many SMS records
        number = self._number

        def _fetch() -> None:
            all_msgs = load_sms_history()
            # Filter to this conversation's number using tail-7 matching
            import re as _re
            stripped = _re.sub(r'[^\d]', '', number)
            tail = min(7, len(stripped))
            history: list[Message] = []
            for m in all_msgs:
                ns = _re.sub(r'[^\d]', '', m.get('number', ''))
                if tail and stripped[-tail:] == ns[-tail:]:
                    ts = datetime.now()
                    iso = m.get('timestamp_iso', '')
                    if iso:
                        try:
                            ts = datetime.fromisoformat(iso.replace('Z', '+00:00'))
                        except ValueError:
                            pass
                    history.append(Message(m['text'], m['outgoing'], ts))
            # Sort chronologically
            history.sort(key=lambda msg: msg.timestamp)
            # Prepend to conversation on the GLib main thread
            GLib.idle_add(self._prepend_history, history)

        threading.Thread(target=_fetch, daemon=True).start()

    def _prepend_history(self, history: list[Message]) -> bool:
        if not history:
            return False
        # Prepend historical messages before any already-shown new messages
        existing = list(self._messages)
        self._messages = history + existing
        self._rebuild_messages()
        # Scroll to bottom to show most recent
        self._scroll_to_bottom()
        return False

    def _rebuild_messages(self) -> None:
        child = self._msg_list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._msg_list.remove(child)
            child = nxt
        for msg in self._messages:
            self._append_bubble(msg)

    def _append_bubble(self, msg: Message) -> None:
        bubble = Gtk.Label(label=msg.text, wrap=True, xalign=0 if not msg.outgoing else 1)
        bubble.add_css_class('bubble-out' if msg.outgoing else 'bubble-in')

        time_label = Gtk.Label(
            label=msg.timestamp.strftime('%H:%M'),
            xalign=1 if msg.outgoing else 0,
        )
        time_label.add_css_class('bubble-time')

        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row.append(bubble)
        row.append(time_label)
        self._msg_list.append(row)

    def _scroll_to_bottom(self) -> None:
        def _do_scroll() -> bool:
            adj = self._scroller.get_vadjustment()
            adj.set_value(adj.get_upper())
            return False
        GLib.idle_add(_do_scroll)
