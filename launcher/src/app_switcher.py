from __future__ import annotations

import gi
import os
import signal as _signal
import subprocess

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gtk

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

_SWITCHER_CSS = b"""
.switcher-root {
    background: rgba(0, 0, 0, 0.92);
    color: #f4f4f4;
    font-family: 'Space Mono', monospace;
}
.switcher-header {
    font-size: 11pt;
    font-weight: 700;
    letter-spacing: 0.18em;
    color: #9a9a9a;
}
.app-card {
    background: #111111;
    border-radius: 20px;
    padding: 20px 16px;
    min-width: 140px;
    min-height: 180px;
}
.app-card:hover, .app-card:focus { background: #1a1a1a; }
.card-name {
    font-size: 13pt;
    font-weight: 400;
    color: #f4f4f4;
}
.card-kill {
    font-size: 12pt;
    color: #9a9a9a;
    min-width: 32px;
    min-height: 32px;
    border-radius: 16px;
    padding: 0;
    background: transparent;
    border: none;
}
.card-kill:hover {
    background: #2a1010;
    color: #ff6b6b;
}
"""

_SWIPE_DISMISS_THRESHOLD = 120  # px upward drag to dismiss a card


class AppInfo:
    __slots__ = ('app_id', 'title', 'pid')

    def __init__(self, app_id: str, title: str, pid: int | None = None) -> None:
        self.app_id = app_id
        self.title = title
        self.pid = pid


class AppSwitcher(Gtk.Window):
    """
    Slides up from the bottom edge on long swipe-up gesture.
    Card swipe-up dismisses that app. Reveal/hide uses Gtk.Revealer (SLIDE_UP).
    """

    def __init__(self) -> None:
        super().__init__(title='PiercingOS Switcher')

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.TOP)
            LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, True)
            LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, False)
            LayerShell.set_exclusive_zone(self, 0)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        else:
            self.set_default_size(420, 320)

        self._apps: list[AppInfo] = []

        provider = Gtk.CssProvider()
        provider.load_from_data(_SWITCHER_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )

        self.card_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12, homogeneous=False,
        )

        self._revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            transition_duration=250,
            reveal_child=False,
        )
        self._revealer.set_child(self._build_content())
        self.set_child(self._revealer)

        # Swipe down anywhere to dismiss
        swipe = Gtk.GestureSwipe.new()
        swipe.connect('swipe', self._on_swipe)
        self.add_controller(swipe)

        # Escape key to dismiss
        key = Gtk.EventControllerKey.new()
        key.connect('key-pressed', self._on_key)
        self.add_controller(key)

    def _build_content(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class('switcher-root')
        root.set_margin_top(12)
        root.set_margin_start(16)
        root.set_margin_end(16)
        root.set_margin_bottom(20)

        header = Gtk.Label(label='OPEN APPS', xalign=0)
        header.add_css_class('switcher-header')
        header.set_margin_bottom(12)
        header.set_margin_start(4)

        scroller = Gtk.ScrolledWindow(hexpand=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_child(self.card_box)
        scroller.set_min_content_height(200)

        root.append(header)
        root.append(scroller)
        return root

    def refresh(self, apps: list[AppInfo] | None = None) -> None:
        if apps is not None:
            self._apps = apps
        self._rebuild_cards()

    def _rebuild_cards(self) -> None:
        child = self.card_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.card_box.remove(child)
            child = nxt

        if not self._apps:
            empty = Gtk.Label(label='No open apps', xalign=0)
            empty.add_css_class('switcher-header')
            empty.set_margin_start(4)
            self.card_box.append(empty)
            return

        for app in self._apps:
            self.card_box.append(self._make_card(app))

    def _make_card(self, app: AppInfo) -> Gtk.Widget:
        name_label = Gtk.Label(label=app.title, wrap=True, max_width_chars=12)
        name_label.add_css_class('card-name')
        name_label.set_valign(Gtk.Align.END)
        name_label.set_vexpand(True)

        kill_btn = Gtk.Button(label='×')
        kill_btn.add_css_class('card-kill')
        kill_btn.connect('clicked', lambda _b, a=app: self._kill_app(a))
        kill_btn.set_halign(Gtk.Align.END)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.add_css_class('app-card')
        inner.append(kill_btn)
        inner.append(name_label)

        # Swipe up to dismiss card
        drag = Gtk.GestureDrag()
        drag.connect(
            'drag-update',
            lambda _g, _dx, dy, card=inner: self._on_card_drag(card, dy),
        )
        drag.connect(
            'drag-end',
            lambda _g, _dx, dy, a=app: self._on_card_drag_end(dy, a),
        )
        inner.add_controller(drag)

        focus_btn = Gtk.Button()
        focus_btn.add_css_class('flat')
        focus_btn.set_child(inner)
        focus_btn.connect('clicked', lambda _b, a=app: self._focus_app(a))

        return focus_btn

    def _on_card_drag(self, card: Gtk.Box, dy: float) -> None:
        if dy < 0:
            card.set_margin_bottom(max(0, int(abs(dy))))

    def _on_card_drag_end(self, dy: float, app: AppInfo) -> None:
        if dy < -_SWIPE_DISMISS_THRESHOLD:
            self._kill_app(app)
        else:
            self._rebuild_cards()

    def _focus_app(self, app: AppInfo) -> None:
        if app.pid:
            try:
                subprocess.Popen(['wmctrl', '-ia', str(app.app_id)], close_fds=True)
            except FileNotFoundError:
                pass
        self.hide_switcher()

    def _kill_app(self, app: AppInfo) -> None:
        if app.pid:
            try:
                os.kill(app.pid, _signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        self._apps = [a for a in self._apps if a.app_id != app.app_id]
        self._rebuild_cards()

    def _on_swipe(self, _g: Gtk.GestureSwipe, vel_x: float, vel_y: float) -> None:
        if vel_y > 200:
            self.hide_switcher()

    def _on_key(self, _g: Gtk.EventControllerKey, keyval: int, *_) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.hide_switcher()
            return True
        return False

    def show_switcher(self) -> None:
        self.present()
        self._revealer.set_reveal_child(True)

    def hide_switcher(self) -> None:
        self._revealer.set_reveal_child(False)
        GLib.timeout_add(260, self.hide)
