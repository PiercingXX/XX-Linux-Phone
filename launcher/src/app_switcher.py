from __future__ import annotations

import logging

from toplevel_manager import ToplevelManager

log = logging.getLogger(__name__)

_LAYER_SHELL = False
_GTK_AVAILABLE = True
try:
    import gi

    gi.require_version('Gtk', '4.0')
    from gi.repository import Gdk, GLib, Gtk

    try:
        gi.require_version('Gtk4LayerShell', '1.0')
        from gi.repository import Gtk4LayerShell as LayerShell
        _LAYER_SHELL = True
    except ValueError:
        pass
except (ImportError, ValueError) as exc:
    # GTK/PyGObject is unavailable (ImportError) or the required version is
    # missing (ValueError). The switcher degrades to its static card and
    # empty-state seams so the logic stays testable headlessly; the window
    # itself is never constructed in this mode.
    log.info('GTK unavailable; switcher degrades to headless seams: %s', exc)
    _GTK_AVAILABLE = False
    Gdk = None  # type: ignore[assignment,misc]
    GLib = None
    Gtk = None
    LayerShell = None


class _WindowBase:
    """Base class used only when GTK is unavailable (headless seam tests)."""


_AppSwitcherBase = Gtk.Window if _GTK_AVAILABLE else _WindowBase

_SWITCHER_CSS = b"""
.switcher-root {
    background: rgba(0, 0, 0, 0.92);
    color: #f4f4f4;
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
_EMPTY_STATE_TEXT = 'No open apps'


class AppInfo:
    __slots__ = ('app_id', 'handle', 'title')

    def __init__(self, app_id: str, title: str, handle: object | None = None) -> None:
        self.app_id = app_id
        self.title = title
        self.handle = handle


class AppSwitcher(_AppSwitcherBase):
    """
    Slides up from the bottom edge on long swipe-up gesture.
    Card swipe-up dismisses that app. Reveal/hide uses Gtk.Revealer (SLIDE_UP).
    """

    def __init__(self, manager: ToplevelManager | None = None) -> None:
        super().__init__(title='PiercingXX Switcher')

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

        self._manager = manager if manager is not None else ToplevelManager()
        self._apps: list[AppInfo] = []
        if self._manager.available:
            self._wire_change(self._manager, self.refresh)
        self.refresh()

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
        if apps is None:
            apps = self._apps_from_manager(self._manager)
        self._apps = apps
        self._rebuild_cards()

    @staticmethod
    def _apps_from_manager(manager: ToplevelManager) -> list[AppInfo]:
        """Headless seam: manager.list() -> the AppInfo records cards render from."""
        return [
            AppInfo(t.app_id, t.title, handle=t.handle)
            for t in manager.list()
        ]

    @staticmethod
    def _empty_state_label() -> str:
        """Headless seam: the message shown when there are no open apps."""
        return _EMPTY_STATE_TEXT

    @staticmethod
    def _card_title(app: AppInfo) -> str:
        """Headless seam: the text-only label a card renders for an app."""
        return app.title

    @staticmethod
    def _card_labels(apps: list[AppInfo]) -> list[str]:
        """Headless seam: the ordered text labels the cards render from."""
        return [AppSwitcher._card_title(a) for a in apps]

    @staticmethod
    def _wire_change(manager: ToplevelManager, callback) -> None:
        """Headless seam: register the switcher's refresh on manager changes."""
        manager.on_change(callback)

    @staticmethod
    def _focus_app_with_manager(manager: ToplevelManager, app: AppInfo) -> None:
        """Headless seam: activating a card dispatches to the manager."""
        if app.handle is not None:
            manager.activate(app.handle)

    @staticmethod
    def _kill_app_with_manager(manager: ToplevelManager, app: AppInfo) -> None:
        """Headless seam: killing a card dispatches to the manager."""
        if app.handle is not None:
            manager.close(app.handle)

    def _rebuild_cards(self) -> None:
        child = self.card_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.card_box.remove(child)
            child = nxt

        if not self._apps:
            empty = Gtk.Label(label=_EMPTY_STATE_TEXT, xalign=0)
            empty.add_css_class('switcher-header')
            empty.set_margin_start(4)
            self.card_box.append(empty)
            return

        for app in self._apps:
            self.card_box.append(self._make_card(app))

    def _make_card(self, app: AppInfo) -> Gtk.Widget:
        name_label = Gtk.Label(label=self._card_title(app), wrap=True, max_width_chars=12)
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
        self._focus_app_with_manager(self._manager, app)
        self.hide_switcher()

    def _kill_app(self, app: AppInfo) -> None:
        self._kill_app_with_manager(self._manager, app)
        self._apps = [a for a in self._apps if a.handle != app.handle]
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
