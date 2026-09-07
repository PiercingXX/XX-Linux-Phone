from __future__ import annotations

import logging

from config import DANGER_RED, DESTRUCTIVE_TINT_BG, ShellConfig, ThemePreset
from toplevel_manager import ToplevelManager

log = logging.getLogger(__name__)

_LAYER_SHELL = False
_GTK_AVAILABLE = True
try:
    import gi

    gi.require_version('Gtk', '4.0')
    from gi.repository import Gdk, Gtk

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
    # Bind the GTK module names to None so that any code path that touches them
    # in headless mode fails fast with a clear AttributeError instead of a
    # NameError. The static headless seams below never reference these names;
    # the window itself is never constructed while _GTK_AVAILABLE is False.
    Gdk = None  # type: ignore[assignment,misc]
    Gtk = None  # type: ignore[assignment,misc]
    LayerShell = None  # type: ignore[assignment,misc]


class _WindowBase:
    """Base class used only when GTK is unavailable (headless seam tests).

    The switcher's logic is exercised headlessly through the static seams
    (_apps_from_manager, _card_labels, _focus_app_with_manager, ...); the
    Gtk.Window itself is never constructed in this mode. If construction is
    nonetheless attempted, fail loudly with an actionable message rather than
    letting super().__init__ raise a confusing AttributeError deep inside
    AppSwitcher.__init__.
    """

    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError(
            'AppSwitcher cannot be constructed: GTK/PyGObject is unavailable '
            'in this environment. Use the static headless seams '
            '(AppSwitcher._apps_from_manager, _card_labels, '
            '_focus_app_with_manager, _kill_app_with_manager) to exercise the '
            'switcher logic without a display.'
        )


_AppSwitcherBase = Gtk.Window if _GTK_AVAILABLE else _WindowBase

def theme_css(preset: ThemePreset) -> str:
    return f"""
window.switcher-window {{
    background: transparent;
}}
.switcher-root {{
    background: alpha({preset.background}, 0.96);
    color: {preset.foreground};
}}
.switcher-dismiss {{
    background: alpha({preset.background}, 0.45);
    border: none;
    box-shadow: none;
    min-height: 0;
}}
.switcher-header {{
    font-size: 11pt;
    font-weight: 700;
    letter-spacing: 0.18em;
    color: {preset.muted};
}}
.switcher-empty {{
    font-size: 13pt;
    color: {preset.foreground};
}}
.app-card {{
    background: {preset.surface};
    border-radius: 20px;
    padding: 20px 16px;
    min-width: 140px;
    min-height: 180px;
}}
.app-card:hover, .app-card:focus {{ background: {preset.surface_alt}; }}
.card-name {{
    font-size: 13pt;
    font-weight: 400;
    color: {preset.foreground};
}}
.card-kill {{
    font-size: 12pt;
    color: {preset.muted};
    min-width: 32px;
    min-height: 32px;
    border-radius: 16px;
    padding: 0;
    background: transparent;
    border: none;
}}
.card-kill:hover {{
    background: {DESTRUCTIVE_TINT_BG};
    color: {DANGER_RED};
}}
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

    def __init__(self, manager: ToplevelManager | None = None,
                 config: ShellConfig | None = None) -> None:
        super().__init__(title='PiercingXX Switcher')
        self.add_css_class('switcher-window')

        # The shell window threads its LIVE config so theme == 'custom'
        # renders the derived palette; the fallback keeps direct no-arg
        # construction working (pre-existing snapshot behavior).
        self._config = config if config is not None else ShellConfig()

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            # OVERLAY sits above xdg apps (and above the launcher's TOP hop
            # for Settings). Bottom-anchored opaque panel, not a 4-edge
            # transparent surface — those never composited on this phoc.
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, False)
            LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, True)
            LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_exclusive_zone(self, 0)
            # NONE while hidden; show_switcher flips EXCLUSIVE so Escape
            # and taps reach this overlay (phoc v3 has no ON_DEMAND).
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        else:
            self.set_default_size(420, 320)

        self._manager = manager if manager is not None else ToplevelManager()
        self._apps: list[AppInfo] = []
        self._card_swiping = False

        self._theme_provider = Gtk.CssProvider()
        self._theme_provider.load_from_data(
            theme_css(self._display_preset()).encode('utf-8'))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )

        self.card_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12, homogeneous=False,
        )

        self.set_child(self._build_content())

        self._wire_change(self._manager, self.refresh)
        self._manager_wired = True
        self.refresh()

        swipe = Gtk.GestureSwipe.new()
        swipe.connect('swipe', self._on_swipe)
        self.add_controller(swipe)

        key = Gtk.EventControllerKey.new()
        key.connect('key-pressed', self._on_key)
        self.add_controller(key)

    def attach_manager(self, manager: ToplevelManager) -> None:
        """Swap in the live toplevel client after the window is already shown."""
        if manager is self._manager:
            self.refresh()
            return
        self._manager = manager
        self._wire_change(manager, self.refresh)
        self._manager_wired = True
        self.refresh()

    def _display_preset(self) -> ThemePreset:
        """Preset this surface renders with: window.resolve_theme over the
        injected config, so theme == 'custom' derives its palette instead of
        falling back to the default preset (W1-B). Lazy import: window.py
        sits above this module in the shell stack, and a module-level import
        would drag its GTK requirements into headless contexts that import
        app_switcher for its static seams.
        """
        from window import resolve_theme
        return resolve_theme(self._config)

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        data = theme_css(preset if preset is not None else self._display_preset())
        self._theme_provider.load_from_data(data.encode('utf-8'))

    def _build_content(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class('switcher-root')
        root.set_margin_top(12)
        root.set_margin_start(16)
        root.set_margin_end(16)
        root.set_margin_bottom(20)
        root.set_size_request(-1, 280)

        header = Gtk.Label(label='OPEN APPS', xalign=0)
        header.add_css_class('switcher-header')
        header.set_margin_bottom(12)
        header.set_margin_start(4)

        scroller = Gtk.ScrolledWindow(hexpand=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_child(self.card_box)
        scroller.set_min_content_height(220)

        close_btn = Gtk.Button(label='▲ Close')
        close_btn.add_css_class('flat')
        close_btn.add_css_class('switcher-header')
        close_btn.set_halign(Gtk.Align.CENTER)
        close_btn.set_margin_top(8)
        close_btn.connect('clicked', lambda _b: self.hide_switcher())

        root.append(header)
        root.append(scroller)
        root.append(close_btn)
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
        if not hasattr(self, 'card_box'):
            return
        child = self.card_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.card_box.remove(child)
            child = nxt

        if not self._apps:
            empty = Gtk.Label(label=_EMPTY_STATE_TEXT, xalign=0)
            empty.add_css_class('switcher-empty')
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
        kill_btn.set_halign(Gtk.Align.END)
        kill_claim = Gtk.GestureClick.new()
        kill_claim.connect(
            'pressed',
            lambda g, *_: g.set_state(Gtk.EventSequenceState.CLAIMED),
        )
        kill_btn.add_controller(kill_claim)
        kill_btn.connect('clicked', lambda _b, a=app: self._kill_app(a))

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.add_css_class('app-card')
        inner.append(kill_btn)
        inner.append(name_label)

        # A wrapping Gtk.Button never saw clicked: the inner GestureDrag
        # stole the sequence. Tap is a click on the card; swipe-up kills.
        click = Gtk.GestureClick.new()
        click.connect(
            'released',
            lambda _g, _n, _x, _y, a=app: self._on_card_tap(a),
        )
        inner.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect(
            'drag-update',
            lambda g, _dx, dy, card=inner: self._on_card_drag(g, card, dy),
        )
        drag.connect(
            'drag-end',
            lambda _g, _dx, dy, a=app: self._on_card_drag_end(dy, a),
        )
        inner.add_controller(drag)

        return inner

    def _on_card_drag(self, gesture: Gtk.GestureDrag, card: Gtk.Box, dy: float) -> None:
        if dy < -20:
            self._card_swiping = True
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if dy < 0:
            card.set_margin_bottom(max(0, int(abs(dy))))

    def _on_card_drag_end(self, dy: float, app: AppInfo) -> None:
        swiping = self._card_swiping
        self._card_swiping = False
        if dy < -_SWIPE_DISMISS_THRESHOLD:
            self._kill_app(app)
        elif swiping:
            self._rebuild_cards()

    def _on_card_tap(self, app: AppInfo) -> None:
        if self._card_swiping:
            return
        self._focus_app(app)

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
        self.refresh()
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
        self.set_visible(True)
        self.present()

    def hide_switcher(self) -> None:
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        self.set_visible(False)
        self.hide()
