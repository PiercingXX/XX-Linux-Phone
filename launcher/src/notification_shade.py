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
from config import DANGER_RED, ShellConfig, ThemePreset

_NOTIF_IFACE = 'org.freedesktop.Notifications'
_NOTIF_PATH = '/org/freedesktop/Notifications'


def _session_bus() -> Gio.DBusConnection | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


def _external_daemon_owns_notifications() -> bool:
    """True when another process already owns org.freedesktop.Notifications.

    Notify is a method call, never a broadcast: with mako/dunst owning the
    name our in-process capture can never fire, so the shade must say so
    instead of silently showing nothing. Our own unique name is not external.
    """
    bus = _session_bus()
    if bus is None:
        return False
    try:
        owner = bus.call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetNameOwner',
            GLib.Variant('(s)', (_NOTIF_IFACE,)),
            GLib.VariantType('(s)'),
            Gio.DBusCallFlags.NONE, 800, None,
        ).unpack()[0]
    except GLib.Error:
        return False
    if not owner:
        return False
    ours = bus.get_unique_name() if hasattr(bus, 'get_unique_name') else None
    return bool(owner) and owner != ours


def theme_css(preset: ThemePreset) -> str:
    return f"""
window.shade-window {{
    background: alpha({preset.background}, 0.55);
}}
.shade-root {{
    background: alpha({preset.background}, 0.88);
    color: {preset.foreground};
}}
.shade-dismiss {{
    background: transparent;
    border: none;
    box-shadow: none;
    min-height: 0;
}}
.shade-header {{
    font-size: 11pt;
    font-weight: 700;
    letter-spacing: 0.18em;
    color: {preset.muted};
}}
.shade-power {{
    font-size: 15pt;
    font-weight: 400;
    letter-spacing: 0;
    min-width: 40px;
    padding: 0 8px;
}}
.shade-power:hover {{
    color: {DANGER_RED};
}}
.notif-app {{
    font-size: 10pt;
    color: {preset.muted};
}}
.notif-title {{
    font-size: 14pt;
    font-weight: 500;
    color: {preset.foreground};
}}
.notif-body {{
    font-size: 11.5pt;
    color: alpha({preset.foreground}, 0.85);
}}
.notif-row {{
    background: {preset.surface};
    border-radius: 16px;
    padding: 14px 18px;
    margin-bottom: 6px;
}}
.notif-row:hover {{ background: {preset.surface_alt}; }}
.dismiss-button {{
    font-size: 14pt;
    color: {preset.muted};
    min-width: 36px;
    min-height: 36px;
    border-radius: 18px;
    padding: 0;
    background: transparent;
    border: none;
}}
.dismiss-button:hover {{ background: {preset.surface_alt}; }}
.shade-datetime {{
    font-size: 13pt;
    font-weight: 500;
    color: {preset.foreground};
    letter-spacing: 0.02em;
}}
.cal-header {{
    font-size: 11pt;
    color: {preset.muted};
}}
.cal-day {{
    font-size: 10.5pt;
    color: alpha({preset.foreground}, 0.85);
    min-width: 34px;
    min-height: 30px;
}}
.cal-day.cal-today {{
    color: {preset.background};
    background: {preset.accent};
    border-radius: 15px;
    font-weight: 700;
}}
.cal-weekday {{
    font-size: 9pt;
    color: {preset.muted};
    min-width: 34px;
}}
"""

_SWIPE_DISMISS_THRESHOLD = 140  # pixels to trigger dismiss
_SHEET_CLOSE_DY = 100  # px upward drag to close the shade


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
                 hud: object | None = None,
                 config: ShellConfig | None = None) -> None:
        super().__init__(title='PiercingXX Shade')
        self.add_css_class('shade-window')
        self._dnd = dnd_state
        self._focus = focus_state
        self._on_open_settings = on_open_settings
        self._on_power = on_power
        self._hud = hud
        # The shell window threads its LIVE config so hot reloads reach this
        # surface; the fallback keeps direct no-arg construction working.
        self._config = config if config is not None else ShellConfig()
        self._clock_timer_id: int | None = None
        self._cal_year_month: tuple[int, int] | None = None

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            # OVERLAY, not TOP: a 4-edge TOP surface sat under the
            # maximized xdg app on this phoc, so tap-outside never drew.
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, True)
            LayerShell.set_exclusive_zone(self, -1)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        else:
            self.set_default_size(420, 500)

        self._notifications: list[Notification] = []

        self._theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )
        self.apply_theme()

        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list_box.add_css_class('text-list')
        # The embedded panel shares this shade's LIVE config so its tiles and
        # sliders resolve the same preset (theme == 'custom' included).
        self.quick_actions = QuickActionsPanel(
            dnd_state=dnd_state, focus_state=focus_state, hud=hud,
            config=self._config)

        self._revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=250,
            reveal_child=False,
        )
        self._revealer.set_child(self._build_content())

        dismiss = Gtk.Box()
        dismiss.add_css_class('shade-dismiss')
        dismiss.set_hexpand(True)
        dismiss.set_vexpand(True)
        tap = Gtk.GestureClick.new()
        tap.connect('released', lambda *_: self.hide_shade())
        dismiss.add_controller(tap)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_hexpand(True)
        outer.set_vexpand(True)
        outer.append(self._revealer)
        outer.append(dismiss)
        self.set_child(outer)

        swipe = Gtk.GestureSwipe.new()
        swipe.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        swipe.connect('swipe', self._on_swipe)
        outer.add_controller(swipe)

        sheet_drag = Gtk.GestureDrag.new()
        sheet_drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        sheet_drag.connect('drag-update', self._on_sheet_drag_update)
        sheet_drag.connect('drag-end', self._on_sheet_drag_end)
        outer.add_controller(sheet_drag)

        key = Gtk.EventControllerKey.new()
        key.connect('key-pressed', self._on_key)
        self.add_controller(key)

        self._subscribe_dbus()
        self._check_external_notif_daemon()

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """(Re)load this shade's display-level sheet. The shell window passes
        the freshly resolved preset on hot-reload fan-out; standalone falls
        back to the injected config's own preset."""
        data = theme_css(preset if preset is not None else self._config.theme)
        self._theme_provider.load_from_data(data.encode('utf-8'))
        qa = getattr(self, 'quick_actions', None)
        if qa is not None:
            qa.apply_theme(preset)

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

        # Honest empty state when an external daemon owns the notification
        # name: one muted line reusing .notif-app instead of a silent void.
        self._external_hint = Gtk.Label(
            label='Notifications handled by external daemon', xalign=0)
        self._external_hint.add_css_class('notif-app')
        self._external_hint.set_margin_start(6)
        self._external_hint.set_visible(False)

        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(360)
        scroller.set_propagate_natural_height(True)
        scroller.set_child(self.list_box)

        root.append(top_header)
        root.append(self._calendar_revealer)
        root.append(qa_header)
        root.append(self.quick_actions)
        root.append(sep)
        root.append(notif_header)
        root.append(self._external_hint)
        root.append(scroller)
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
        # Hide immediately so the BOTTOM launcher (hopped to TOP) is not
        # covered by this TOP shade for the revealer timeout.
        if self._clock_timer_id is not None:
            GLib.source_remove(self._clock_timer_id)
            self._clock_timer_id = None
        self._revealer.set_reveal_child(False)
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        self.hide()
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
        # Only NotificationClosed is a real broadcast: per the freedesktop
        # spec Notify is a METHOD call aimed at whichever daemon owns
        # org.freedesktop.Notifications, so it is never seen as a signal.
        bus = _session_bus()
        if bus is None:
            return
        try:
            bus.signal_subscribe(
                None, _NOTIF_IFACE, 'NotificationClosed', _NOTIF_PATH,
                None, Gio.DBusSignalFlags.NONE, self._on_dbus_closed, None,
            )
        except GLib.Error:
            pass

    def _on_dbus_closed(self, _c, _s, _p, _i, _sig, params, _ud) -> None:
        try:
            self.dismiss(int(params.unpack()[0]))
        except Exception:
            pass

    def _check_external_notif_daemon(self) -> None:
        if not _external_daemon_owns_notifications():
            return
        self._external_hint.set_visible(True)
        from shell_log import get_logger
        get_logger('notification_shade').info(
            'org.freedesktop.Notifications owned by an external daemon — '
            'shade cannot capture notifications')

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

    def _on_sheet_drag_update(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        # Claim only an upward pull so horizontal notif-row swipes still fire.
        if dy < -40 and abs(dy) > abs(dx):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_sheet_drag_end(self, _g: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if dy < -_SHEET_CLOSE_DY and abs(dy) > abs(dx):
            self.hide_shade()

    def _on_swipe(self, _g: Gtk.GestureSwipe, vel_x: float, vel_y: float) -> None:
        if vel_y < -200 and abs(vel_y) > abs(vel_x):
            self.hide_shade()

    def _on_key(self, _g: Gtk.EventControllerKey, keyval: int, *_) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.hide_shade()
            return True
        return False

    def show_shade(self) -> None:
        self._refresh_datetime()
        if self._clock_timer_id is None:
            self._clock_timer_id = GLib.timeout_add_seconds(10, self._refresh_datetime)
        qa = getattr(self, 'quick_actions', None)
        if qa is not None and hasattr(qa, 'sync_sliders'):
            qa.sync_sliders()
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
        self.set_visible(True)
        self.present()
        self._revealer.set_reveal_child(True)

    def hide_shade(self) -> None:
        if self._clock_timer_id is not None:
            GLib.source_remove(self._clock_timer_id)
            self._clock_timer_id = None
        self._calendar_revealer.set_reveal_child(False)
        self._revealer.set_reveal_child(False)
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        GLib.timeout_add(260, self.hide)

    def clear_all(self) -> None:
        self._notifications.clear()
        self._rebuild_list()

    def notifications_snapshot(self) -> list[tuple[str, str]]:
        """(app_name, summary) pairs for the lock screen's text list."""
        return [(n.app_name, n.summary) for n in self._notifications]
