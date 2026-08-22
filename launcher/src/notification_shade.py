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

from gi.repository import Gdk, Gio, GLib, Gtk

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

from quick_actions import QuickActionsPanel

_NOTIF_IFACE = 'org.freedesktop.Notifications'
_NOTIF_PATH = '/org/freedesktop/Notifications'

_SHADE_CSS = b"""
.shade-root {
    background: rgba(0, 0, 0, 0.88);
    color: #f4f4f4;
}
.shade-header {
    font-size: 11pt;
    font-weight: 700;
    letter-spacing: 0.18em;
    color: #9a9a9a;
}
.shade-power {
    font-size: 15pt;
    font-weight: 400;
    letter-spacing: 0;
    min-width: 40px;
    padding: 0 8px;
}
.shade-power:hover {
    color: #ff6b6b;
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
.shade-datetime {
    font-size: 13pt;
    font-weight: 500;
    color: #f4f4f4;
    letter-spacing: 0.02em;
}
.cal-header {
    font-size: 11pt;
    color: #9a9a9a;
}
.cal-day {
    font-size: 10.5pt;
    color: #c8c8c8;
    min-width: 34px;
    min-height: 30px;
}
.cal-day.cal-today {
    color: #000000;
    background: #f4f4f4;
    border-radius: 15px;
    font-weight: 700;
}
.cal-weekday {
    font-size: 9pt;
    color: #9a9a9a;
    min-width: 34px;
}
"""

_SWIPE_DISMISS_THRESHOLD = 140  # pixels to trigger dismiss


class Notification:
    __slots__ = ('id', 'app_name', 'summary', 'body', 'desktop_entry', 'timestamp', 'actions')

    def __init__(
        self,
        notif_id: int,
        app_name: str,
        summary: str,
        body: str,
        desktop_entry: str = '',
        actions: list[tuple[str, object]] | None = None,
    ) -> None:
        self.id = notif_id
        self.app_name = app_name
        self.summary = summary
        self.body = body
        self.desktop_entry = desktop_entry
        self.timestamp = datetime.now()
        self.actions = actions or []


class NotificationShade(Gtk.Window):
    def __init__(self, dnd_state: object | None = None,
                 focus_state: object | None = None,
                 on_open_settings: object | None = None,
                 on_power: object | None = None,
                 hud: object | None = None) -> None:
        super().__init__(title='PiercingXX Shade')
        self._dnd = dnd_state
        self._focus = focus_state
        self._on_open_settings = on_open_settings
        self._on_power = on_power
        self._hud = hud
        self._clock_timer_id: int | None = None
        self._cal_year_month: tuple[int, int] | None = None

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
        self.quick_actions = QuickActionsPanel(dnd_state=dnd_state, focus_state=focus_state, hud=hud)

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

        # Header: date + time on the left (tap → inline month calendar),
        # Settings on the right (design.md "Notification shade & quick settings")
        self._datetime_btn = Gtk.Button()
        self._datetime_btn.add_css_class('flat')
        self._datetime_label = Gtk.Label(xalign=0)
        self._datetime_label.add_css_class('shade-datetime')
        self._datetime_btn.set_child(self._datetime_label)
        self._datetime_btn.connect('clicked', lambda _b: self._toggle_calendar())

        settings_btn = Gtk.Button(label='Settings')
        settings_btn.add_css_class('flat')
        settings_btn.add_css_class('shade-header')
        settings_btn.connect('clicked', self._on_settings_clicked)

        # Power button, top-right — suspend / restart / power off via PowerMenu
        power_btn = Gtk.Button(label='⏻')
        power_btn.add_css_class('flat')
        power_btn.add_css_class('shade-header')
        power_btn.add_css_class('shade-power')
        power_btn.set_tooltip_text('Power')
        power_btn.connect('clicked', self._on_power_clicked)

        top_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top_header.set_margin_top(6)
        self._datetime_btn.set_hexpand(True)
        self._datetime_btn.set_halign(Gtk.Align.START)
        top_header.append(self._datetime_btn)
        top_header.append(settings_btn)
        top_header.append(power_btn)

        self._calendar_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=180,
            reveal_child=False,
        )
        self._calendar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._calendar_revealer.set_child(self._calendar_box)
        self._refresh_datetime()

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

        notif_label = Gtk.Label(label='NOTIFICATIONS', xalign=0)
        notif_label.add_css_class('shade-header')
        notif_label.set_hexpand(True)
        notif_label.set_margin_start(6)

        clear_btn = Gtk.Button(label='Clear all')
        clear_btn.add_css_class('flat')
        clear_btn.add_css_class('shade-header')
        clear_btn.connect('clicked', lambda _b: self.clear_all())

        notif_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        notif_header.set_margin_bottom(8)
        notif_header.append(notif_label)
        notif_header.append(clear_btn)

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

        root.append(top_header)
        root.append(self._calendar_revealer)
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

    def _refresh_datetime(self) -> bool:
        now = datetime.now()
        date_part = now.strftime('%a, %b %d').replace(' 0', ' ')
        self._datetime_label.set_text(f'{date_part}   {now.strftime("%H:%M")}')
        return True

    def _on_settings_clicked(self, _btn: Gtk.Button) -> None:
        self.hide_shade()
        if callable(self._on_open_settings):
            self._on_open_settings()

    def _on_power_clicked(self, _btn: Gtk.Button) -> None:
        self.hide_shade()
        if callable(self._on_power):
            self._on_power()

    def _toggle_calendar(self) -> None:
        showing = self._calendar_revealer.get_reveal_child()
        if showing:
            self._calendar_revealer.set_reveal_child(False)
            return
        now = datetime.now()
        self._show_month(now.year, now.month)
        self._calendar_revealer.set_reveal_child(True)

    def _show_month(self, year: int, month: int) -> None:
        from calendar_grid import WEEKDAY_HEADERS, add_months, month_grid, month_title
        self._cal_year_month = (year, month)

        child = self._calendar_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._calendar_box.remove(child)
            child = nxt

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        prev_btn = Gtk.Button(label='‹')
        prev_btn.add_css_class('flat')
        prev_btn.add_css_class('cal-header')
        prev_btn.connect('clicked', lambda _b: self._show_month(*add_months(year, month, -1)))
        title = Gtk.Label(label=month_title(year, month))
        title.add_css_class('cal-header')
        title.set_hexpand(True)
        next_btn = Gtk.Button(label='›')
        next_btn.add_css_class('flat')
        next_btn.add_css_class('cal-header')
        next_btn.connect('clicked', lambda _b: self._show_month(*add_months(year, month, +1)))
        header.append(prev_btn)
        header.append(title)
        header.append(next_btn)
        self._calendar_box.append(header)

        grid = Gtk.Grid(column_homogeneous=True, row_spacing=2)
        for col, name in enumerate(WEEKDAY_HEADERS):
            lbl = Gtk.Label(label=name)
            lbl.add_css_class('cal-weekday')
            grid.attach(lbl, col, 0, 1, 1)

        today = datetime.now()
        for row_idx, week in enumerate(month_grid(year, month), start=1):
            for col, day in enumerate(week):
                lbl = Gtk.Label(label=str(day) if day else '')
                lbl.add_css_class('cal-day')
                if (day and year == today.year and month == today.month
                        and day == today.day):
                    lbl.add_css_class('cal-today')
                grid.attach(lbl, col, row_idx, 1, 1)
        self._calendar_box.append(grid)

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
        actions: list[tuple[str, object]] | None = None,
    ) -> None:
        self.dismiss(notif_id)
        notif = Notification(notif_id, app_name, summary, body, desktop_entry, actions)
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

        if notif.actions:
            action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            action_row.set_margin_top(6)
            for label, callback in notif.actions:
                btn = Gtk.Button(label=label)
                btn.add_css_class('flat')
                btn.add_css_class('action-link')
                btn.connect(
                    'clicked',
                    lambda _b, cb=callback, nid=notif.id: self._run_action(cb, nid),
                )
                action_row.append(btn)
            content.append(action_row)

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

    def _run_action(self, callback: object, notif_id: int) -> None:
        self.dismiss(notif_id)
        self.hide_shade()
        if callable(callback):
            callback()

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
        self._refresh_datetime()
        if self._clock_timer_id is None:
            self._clock_timer_id = GLib.timeout_add_seconds(10, self._refresh_datetime)
        self.present()
        self._revealer.set_reveal_child(True)

    def hide_shade(self) -> None:
        if self._clock_timer_id is not None:
            GLib.source_remove(self._clock_timer_id)
            self._clock_timer_id = None
        self._calendar_revealer.set_reveal_child(False)
        self._revealer.set_reveal_child(False)
        GLib.timeout_add(260, self.hide)

    def clear_all(self) -> None:
        self._notifications.clear()
        self._rebuild_list()

    def notifications_snapshot(self) -> list[tuple[str, str]]:
        """(app_name, summary) pairs for the lock screen's text list."""
        return [(n.app_name, n.summary) for n in self._notifications]
