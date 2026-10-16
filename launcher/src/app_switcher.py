from __future__ import annotations

import logging
from collections.abc import Callable

from app_names import fallback_app_name
from config import DANGER_RED, DESTRUCTIVE_TINT_BG, ShellConfig, ThemePreset
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
    # Bind the GTK module names to None so that any code path that touches them
    # in headless mode fails fast with a clear AttributeError instead of a
    # NameError. The static headless seams below never reference these names;
    # the window itself is never constructed while _GTK_AVAILABLE is False.
    Gdk = None  # type: ignore[assignment,misc]
    GLib = None  # type: ignore[assignment,misc]
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
.switcher-root {{
    background: alpha({preset.background}, 0.92);
    color: {preset.foreground};
}}
.switcher-header {{
    font-size: 11pt;
    font-weight: 700;
    letter-spacing: 0.18em;
    color: {preset.muted};
}}
.switcher-row {{
    background: {preset.surface};
    border-radius: 14px;
    padding: 10px 12px;
}}
.switcher-row:hover {{ background: {preset.surface_alt}; }}
.card-name {{
    font-size: 13pt;
    font-weight: 400;
    color: {preset.foreground};
}}
.card-title {{
    font-size: 11pt;
    color: {preset.muted};
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

_SWIPE_DISMISS_THRESHOLD = 120  # px of horizontal drag to dismiss a row
_EMPTY_STATE_TEXT = 'No open apps'

_APP_NAME_CACHE: dict[str, str] = {}


def resolve_app_name(app_id: str) -> str:
    """Human display name for a toplevel ``app_id``.

    Results are cached per process; unresolvable ids fall back to the
    id's last component (shared with the settings surfaces) so a card
    always has a short first line.
    """
    cached = _APP_NAME_CACHE.get(app_id)
    if cached is not None:
        return cached
    name = _resolve_app_name(app_id) or fallback_app_name(app_id)
    _APP_NAME_CACHE[app_id] = name
    return name


def _resolve_app_name(app_id: str) -> str | None:
    if not app_id:
        return None
    desktop_id = app_id if app_id.endswith('.desktop') else f'{app_id}.desktop'
    info = _desktop_info_for(desktop_id)
    if info is not None:
        name = _display_name_of(info)
        if name:
            return name
    target = app_id.casefold()
    if target.endswith('.desktop'):
        target = target[: -len('.desktop')]
    for info in _iter_desktop_infos():
        try:
            wm_class = info.get_startup_wm_class() or ''
        except Exception:
            wm_class = ''
        try:
            info_id = info.get_id() or ''
        except Exception:
            info_id = ''
        base = info_id[: -len('.desktop')] if info_id.endswith('.desktop') else info_id
        if (wm_class and wm_class.casefold() == target) or (base and base.casefold() == target):
            name = _display_name_of(info)
            if name:
                return name
    return None


def _desktop_info_for(desktop_id: str):
    """DesktopAppInfo for an exact desktop id, or None (never raises)."""
    try:
        from gi.repository import Gio
    except Exception:
        return None
    try:
        return Gio.DesktopAppInfo.new(desktop_id)
    except Exception:
        return None


def _iter_desktop_infos() -> list:
    """All installed DesktopAppInfo entries, or [] (never raises)."""
    try:
        from gi.repository import Gio
    except Exception:
        return []
    try:
        all_infos = Gio.AppInfo.get_all()
    except Exception:
        return []
    try:
        desktop_cls = Gio.DesktopAppInfo
    except Exception:
        return []
    return [info for info in all_infos if isinstance(info, desktop_cls)]


def _display_name_of(info) -> str | None:
    for method in ('get_display_name', 'get_name'):
        try:
            value = getattr(info, method)()
        except Exception:
            continue
        if value and str(value).strip():
            return str(value).strip()
    return None


def _switcher_max_height() -> int:
    """Cap for the row list: 70% of the screen height (360px-wide target)."""
    try:
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() if display is not None else None
        geometry = monitor.get_geometry() if monitor is not None else None
        height = geometry.height if geometry is not None else 720
    except Exception:
        height = 720
    return max(240, int(height * 0.7))


class AppInfo:
    __slots__ = ('app_id', 'handle', 'title')

    def __init__(self, app_id: str, title: str, handle: object | None = None) -> None:
        self.app_id = app_id
        self.title = title
        self.handle = handle


class AppSwitcher(_AppSwitcherBase):
    """
    Slides up from the bottom edge on long swipe-up gesture.
    Rows are full-width app name + window title; swipe left/right on a row
    or tap its ✕ to close that app. Reveal/hide uses Gtk.Revealer (SLIDE_UP).
    """

    def __init__(self, manager: ToplevelManager | None = None,
                 config: ShellConfig | None = None,
                 on_activated: Callable[[], None] | None = None) -> None:
        super().__init__(title='PiercingXX Switcher')

        # The shell window threads its LIVE config so theme == 'custom'
        # renders the derived palette; the fallback keeps direct no-arg
        # construction working (pre-existing snapshot behavior).
        self._config = config if config is not None else ShellConfig()
        # Hands the screen to the activated app (the shell drops to the
        # background layer); stays None for standalone use.
        self._on_activated = on_activated

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
            self.set_default_size(360, 504)

        self._manager = manager if manager is not None else ToplevelManager()
        self._apps: list[AppInfo] = []

        self._theme_provider = Gtk.CssProvider()
        self.apply_theme()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )

        self.row_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8, homogeneous=False,
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

        # Rows exist only now, so wire and populate last. Wire unconditionally:
        # a backend still generating bindings reports available == False here
        # and notifies later.
        self._wire_change(self._manager, self.refresh)
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
        """(Re)load this surface's display-level sheet. The shell window
        passes the freshly resolved preset on hot-reload fan-out; standalone
        falls back to the injected config's own preset."""
        data = theme_css(preset if preset is not None else self._display_preset())
        self._theme_provider.load_from_data(data.encode('utf-8'))

    def _build_content(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class('switcher-root')
        root.set_margin_top(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        root.set_margin_bottom(20)

        header = Gtk.Label(label='OPEN APPS', xalign=0)
        header.add_css_class('switcher-header')
        header.set_margin_bottom(12)
        header.set_margin_start(4)

        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(_switcher_max_height())
        scroller.set_child(self.row_box)

        root.append(header)
        root.append(scroller)
        return root

    def refresh(self, apps: list[AppInfo] | None = None) -> None:
        if apps is None:
            apps = self._apps_from_manager(self._manager)
        self._apps = apps
        self._rebuild_rows()

    @staticmethod
    def _apps_from_manager(manager: ToplevelManager) -> list[AppInfo]:
        """Headless seam: manager.list() -> the AppInfo records rows render from."""
        return [
            AppInfo(t.app_id, t.title, handle=t.handle)
            for t in manager.list()
        ]

    @staticmethod
    def _empty_state_label() -> str:
        """Headless seam: the message shown when there are no open apps."""
        return _EMPTY_STATE_TEXT

    @staticmethod
    def _card_name(app: AppInfo) -> str:
        """Headless seam: the first-line display name a row renders."""
        return resolve_app_name(app.app_id)

    @staticmethod
    def _card_subtitle(app: AppInfo) -> str:
        """Headless seam: the second-line window title, '' when it adds
        nothing (empty, or identical to the display name)."""
        title = (app.title or '').strip()
        if not title or title == AppSwitcher._card_name(app):
            return ''
        return title

    @staticmethod
    def _card_labels(apps: list[AppInfo]) -> list[str]:
        """Headless seam: the ordered first-line names the rows render from."""
        return [AppSwitcher._card_name(a) for a in apps]

    @staticmethod
    def _wire_change(manager: ToplevelManager, callback) -> None:
        """Headless seam: register the switcher's refresh on manager changes."""
        manager.on_change(callback)

    @staticmethod
    def _focus_app_with_manager(manager: ToplevelManager, app: AppInfo,
                                on_activated: Callable[[], None] | None = None) -> None:
        """Headless seam: activating a row dispatches to the manager, then
        hands the screen to the app via on_activated."""
        if app.handle is not None:
            manager.activate(app.handle)
        if on_activated is not None:
            on_activated()

    @staticmethod
    def _kill_app_with_manager(manager: ToplevelManager, app: AppInfo) -> None:
        """Headless seam: killing a row dispatches to the manager."""
        if app.handle is not None:
            manager.close(app.handle)

    def _rebuild_rows(self) -> None:
        child = self.row_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.row_box.remove(child)
            child = nxt

        if not self._apps:
            empty = Gtk.Label(label=_EMPTY_STATE_TEXT, xalign=0)
            empty.add_css_class('switcher-header')
            empty.set_margin_start(4)
            self.row_box.append(empty)
            return

        for app in self._apps:
            self.row_box.append(self._make_row(app))

    def _make_row(self, app: AppInfo) -> Gtk.Widget:
        from gi.repository import Pango

        name_label = Gtk.Label(label=self._card_name(app), xalign=0)
        name_label.add_css_class('card-name')
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_label.set_hexpand(True)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        text_box.append(name_label)
        subtitle = self._card_subtitle(app)
        if subtitle:
            title_label = Gtk.Label(label=subtitle, xalign=0)
            title_label.add_css_class('card-title')
            title_label.set_ellipsize(Pango.EllipsizeMode.END)
            text_box.append(title_label)

        kill_btn = Gtk.Button(label='✕')
        kill_btn.add_css_class('card-kill')
        kill_btn.connect('clicked', lambda _b, a=app: self._kill_app(a))
        kill_btn.set_valign(Gtk.Align.CENTER)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class('switcher-row')
        row.append(text_box)
        row.append(kill_btn)

        # Tap the text to activate; the ✕ button keeps its own handler, so
        # the tap controller lives on the text box only.
        tap = Gtk.GestureClick(n_points=1)
        tap.connect(
            'released',
            lambda _g, _n, _x, _y, a=app: self._focus_app(a),
        )
        text_box.add_controller(tap)

        # Swipe left/right to close the app.
        drag = Gtk.GestureDrag()
        drag.connect(
            'drag-update',
            lambda _g, dx, _dy, r=row: self._on_row_drag(r, dx),
        )
        drag.connect(
            'drag-end',
            lambda _g, dx, _dy, a=app: self._on_row_drag_end(dx, a),
        )
        row.add_controller(drag)

        return row

    def _on_row_drag(self, row: Gtk.Box, dx: float) -> None:
        if dx > 0:
            row.set_margin_start(max(0, int(dx)))
        else:
            row.set_margin_end(max(0, int(abs(dx))))

    def _on_row_drag_end(self, dx: float, app: AppInfo) -> None:
        if abs(dx) >= _SWIPE_DISMISS_THRESHOLD:
            self._kill_app(app)
        else:
            self._rebuild_rows()

    def _focus_app(self, app: AppInfo) -> None:
        self._focus_app_with_manager(self._manager, app, self._on_activated)
        self.hide_switcher()

    def _kill_app(self, app: AppInfo) -> None:
        self._kill_app_with_manager(self._manager, app)
        self._apps = [a for a in self._apps if a.handle != app.handle]
        self._rebuild_rows()

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
        self.present()
        self._revealer.set_reveal_child(True)

    def hide_switcher(self) -> None:
        self._revealer.set_reveal_child(False)
        GLib.timeout_add(260, self.hide)
