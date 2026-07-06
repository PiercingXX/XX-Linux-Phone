from __future__ import annotations

import gi
import subprocess
from datetime import datetime

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

from quick_actions import QuickActionsPanel

_NOTIF_IFACE = 'org.freedesktop.Notifications'
_NOTIF_PATH = '/org/freedesktop/Notifications'

_SHADE_CSS = b"""
.shade-root {
    background: rgba(0, 0, 0, 0.88);
    color: #f4f4f4;
    font-family: 'Space Mono', monospace;
}
.shade-header {
    font-size: 11pt;
    font-weight: 700;
    letter-spacing: 0.18em;
    color: #9a9a9a;
}
.notif-app {
    font-size: 10pt;
    color: #9a9a9a;
}
.notif-title {
    font-size: 14pt;
    font-weight: 500;
    color: #f4f4f4;
}
.notif-body {
    font-size: 11.5pt;
    color: #c8c8c8;
}
.notif-row {
    background: #111111;
    border-radius: 16px;
    padding: 14px 18px;
    margin-bottom: 6px;
}
.notif-row:hover { background: #1a1a1a; }
.dismiss-button {
    font-size: 14pt;
    color: #9a9a9a;
    min-width: 36px;
    min-height: 36px;
    border-radius: 18px;
    padding: 0;
    background: transparent;
    border: none;
}
.dismiss-button:hover { background: #282828; }
"""

_SWIPE_DISMISS_THRESHOLD = 140  # pixels to trigger dismiss


class Notification:
    __slots__ = ('id', 'app_name', 'summary', 'body', 'desktop_entry', 'timestamp')

    def __init__(
        self,
        notif_id: int,
        app_name: str,
        summary: str,
        body: str,
        desktop_entry: str = '',
    ) -> None:
        self.id = notif_id
        self.app_name = app_name
        self.summary = summary
        self.body = body
        self.desktop_entry = desktop_entry
        self.timestamp = datetime.now()


class NotificationShade(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title='PiercingOS Shade')

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.TOP)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, False)
            LayerShell.set_exclusive_zone(self, 0)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        else:
            self.set_default_size(420, 500)

        self._notifications: list[Notification] = []
        self._next_id = 1

        provider = Gtk.CssProvider()
        provider.load_from_data(_SHADE_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )

        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list_box.add_css_class('text-list')
        self.quick_actions = QuickActionsPanel()

        self._revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=250,
            reveal_child=False,
        )
        self._revealer.set_child(self._build_content())
        self.set_child(self._revealer)

        self._subscribe_dbus()

    def _build_content(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class('shade-root')
        root.set_margin_top(8)
        root.set_margin_start(12)
        root.set_margin_end(12)
        root.set_margin_bottom(12)

        qa_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        qa_header.set_margin_top(8)
        qa_header.set_margin_bottom(4)

        qa_label = Gtk.Label(label='QUICK SETTINGS', xalign=0)
        qa_label.add_css_class('shade-header')
        qa_label.set_hexpand(True)
        qa_label.set_margin_start(6)

        self._expand_btn = Gtk.Button(label='↓')
        self._expand_btn.add_css_class('flat')
        self._expand_btn.add_css_class('shade-header')
        self._expand_btn.connect('clicked', self._toggle_expand)

        qa_header.append(qa_label)
        qa_header.append(self._expand_btn)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(8)
        sep.set_margin_bottom(8)

        notif_header = Gtk.Label(label='NOTIFICATIONS', xalign=0)
        notif_header.add_css_class('shade-header')
        notif_header.set_margin_bottom(8)
        notif_header.set_margin_start(6)

        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(360)
        scroller.set_propagate_natural_height(True)
        scroller.set_child(self.list_box)

        # Swipe UP anywhere in the shade to close it
        swipe = Gtk.GestureSwipe.new()
        swipe.connect('swipe', lambda _g, _vx, vy: self.hide_shade() if vy < -200 else None)
        root.add_controller(swipe)

        close_btn = Gtk.Button(label='▲ Close')
        close_btn.add_css_class('flat')
        close_btn.add_css_class('shade-header')
        close_btn.set_halign(Gtk.Align.CENTER)
        close_btn.set_margin_top(8)
        close_btn.connect('clicked', lambda _b: self.hide_shade())

        root.append(qa_header)
        root.append(self.quick_actions)
        root.append(sep)
        root.append(notif_header)
        root.append(scroller)
        root.append(close_btn)
        return root

    def _toggle_expand(self, _btn: Gtk.Button) -> None:
        expanded = not self.quick_actions.tier2_grid.get_visible()
        self.quick_actions.expand(expanded)
        self._expand_btn.set_label('↑' if expanded else '↓')

    def _subscribe_dbus(self) -> None:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.signal_subscribe(
                None, _NOTIF_IFACE, 'Notify', _NOTIF_PATH,
                None, Gio.DBusSignalFlags.NONE, self._on_dbus_notify, None,
            )
            bus.signal_subscribe(
                None, _NOTIF_IFACE, 'NotificationClosed', _NOTIF_PATH,
                None, Gio.DBusSignalFlags.NONE, self._on_dbus_closed, None,
            )
        except GLib.Error:
            pass

    def _on_dbus_notify(self, _c, _s, _p, _i, _sig, params, _ud) -> None:
        try:
            parts = params.unpack()
            app_name = str(parts[0])
            replaces_id = int(parts[1]) if parts[1] else 0
            summary = str(parts[3])
            body = str(parts[4])
            hints = parts[6] if len(parts) > 6 else {}
            desktop_entry = str(hints.get('desktop-entry', ''))
            notif_id = replaces_id if replaces_id else self._next_id
            self._next_id = max(self._next_id, notif_id) + 1
            self.add_notification(notif_id, app_name, summary, body, desktop_entry)
        except Exception:
            pass

    def _on_dbus_closed(self, _c, _s, _p, _i, _sig, params, _ud) -> None:
        try:
            self.dismiss(int(params.unpack()[0]))
        except Exception:
            pass

    def add_notification(
        self,
        notif_id: int,
        app_name: str,
        summary: str,
        body: str,
        desktop_entry: str = '',
    ) -> None:
        self.dismiss(notif_id)
        notif = Notification(notif_id, app_name, summary, body, desktop_entry)
        self._notifications.append(notif)
        self.list_box.append(self._make_row(notif))

    def dismiss(self, notif_id: int) -> None:
        self._notifications = [n for n in self._notifications if n.id != notif_id]
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        child = self.list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.list_box.remove(child)
            child = nxt
        for notif in self._notifications:
            self.list_box.append(self._make_row(notif))

    def _make_row(self, notif: Notification) -> Gtk.ListBoxRow:
        app_label = Gtk.Label(label=notif.app_name.upper(), xalign=0)
        app_label.add_css_class('notif-app')

        title_label = Gtk.Label(label=notif.summary, xalign=0, wrap=True, max_width_chars=36)
        title_label.add_css_class('notif-title')

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content.set_hexpand(True)
        content.append(app_label)
        content.append(title_label)

        if notif.body.strip():
            body_label = Gtk.Label(label=notif.body, xalign=0, wrap=True, max_width_chars=36)
            body_label.add_css_class('notif-body')
            content.append(body_label)

        dismiss_btn = Gtk.Button(label='×')
        dismiss_btn.add_css_class('dismiss-button')
        dismiss_btn.connect('clicked', lambda _b, nid=notif.id: self.dismiss(nid))

        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row_box.add_css_class('notif-row')
        row_box.append(content)
        row_box.append(dismiss_btn)

        # Tap to launch app
        if notif.desktop_entry:
            tap = Gtk.GestureClick(n_points=1)
            tap.connect(
                'released',
                lambda _g, _n, _x, _y, de=notif.desktop_entry, nid=notif.id:
                self._launch_notif_app(de, nid),
            )
            content.add_controller(tap)

        # Swipe to dismiss
        drag = Gtk.GestureDrag()
        drag.connect('drag-update', lambda _g, dx, _dy, nid=notif.id, rb=row_box:
                     self._on_notif_drag(rb, dx, nid))
        drag.connect('drag-end', lambda _g, dx, _dy, nid=notif.id, rb=row_box:
                     self._on_notif_drag_end(rb, dx, nid))
        row_box.add_controller(drag)

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(row_box)
        return row

    def _launch_notif_app(self, desktop_entry: str, notif_id: int) -> None:
        try:
            app_info = Gio.DesktopAppInfo.new(f'{desktop_entry}.desktop')
            if app_info is not None:
                app_info.launch([], None)
                self.dismiss(notif_id)
                self.hide_shade()
                return
        except (GLib.Error, Exception):
            pass
        # Fallback: gtk-launch
        try:
            subprocess.Popen(['gtk-launch', desktop_entry], close_fds=True)
            self.dismiss(notif_id)
            self.hide_shade()
        except FileNotFoundError:
            pass

    def _on_notif_drag(self, row_box: Gtk.Box, dx: float, _notif_id: int) -> None:
        # Visually offset the row during drag
        row_box.set_margin_start(max(0, int(abs(dx))) if dx > 0 else 0)
        row_box.set_margin_end(max(0, int(abs(dx))) if dx < 0 else 0)

    def _on_notif_drag_end(self, row_box: Gtk.Box, dx: float, notif_id: int) -> None:
        if abs(dx) >= _SWIPE_DISMISS_THRESHOLD:
            self.dismiss(notif_id)
        else:
            row_box.set_margin_start(0)
            row_box.set_margin_end(0)

    def show_shade(self) -> None:
        self.present()
        self._revealer.set_reveal_child(True)

    def hide_shade(self) -> None:
        self._revealer.set_reveal_child(False)
        GLib.timeout_add(260, self.hide)

    def clear_all(self) -> None:
        self._notifications.clear()
        self._rebuild_list()
