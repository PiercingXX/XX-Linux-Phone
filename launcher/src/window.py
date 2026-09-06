from __future__ import annotations

import gi
import os
import re
from datetime import datetime
from pathlib import Path

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

from app_index import AppEntry, AppIndex
from app_item_actions import AppItemActions
# _derive_shades is imported despite the underscore: the custom-theme
# resolution below must follow the exact same shade rule as the canonical
# presets, and duplicating it here would let the two drift apart.
from config import (
    DEFAULT_CONFIG,
    ShellConfig,
    ThemePreset,
    THEME_PRESETS,
    _derive_shades,
)
from gesture_config import GestureConfig, IPC_VERBS, VALID_ACTIONS
from system_status import status_line

_PAGE_ORDER = ['home', 'apps', 'settings']

# Home side-swipe gestures the user can bind to an app in Settings
_GESTURE_TITLES = {
    'swipe_left_home': 'Swipe left',
    'swipe_right_home': 'Swipe right',
}

# System-level lisgd slots (design.md "Gestures"): they fire over any
# focused app, so besides actions they accept the IPC verbs that
# gesture_bindings.py materializes at session start.
_SYSTEM_GESTURE_TITLES = {
    'swipe_up_short': 'Swipe up (short)',
    'swipe_up_long': 'Swipe up (long)',
    'swipe_down_top': 'Swipe down (top)',
    'swipe_left_edge': 'Edge swipe',
}

_ACTION_LABELS = {
    'home': 'Home', 'app_switcher': 'App switcher',
    'notification_shade': 'Notification shade', 'back': 'Back',
    'search': 'Search', 'lock_screen': 'Lock screen', 'settings': 'Settings',
    'camera': 'Camera', 'dialer': 'Dialer', 'assistant': 'Assistant',
    'none': 'None',
}

_VERB_LABELS = {
    'gesture.back': 'Back (system)', 'gesture.home': 'Home (system)',
    'gesture.shade': 'Shade (system)', 'gesture.keyboard': 'Keyboard (system)',
    'gesture.switcher': 'Switcher (system)',
}

# Swipe-up velocity split: a gentle/short flick raises the keyboard, a
# stronger/longer swipe opens the app drawer. Velocity-based since the
# in-window GestureSwipe reports velocity, not distance (lisgd does distance).
_LONG_SWIPE_UP_VEL = 900

_UPDATE_NOTIF_ID = 999901

_WEB_SEARCH_URL = 'https://duckduckgo.com/?q='

_CUSTOM_HEX_RE = re.compile(r'#[0-9a-fA-F]{6}')


def resolve_theme(config: ShellConfig) -> ThemePreset:
    """Active ThemePreset, with theme == 'custom' resolved from the stored
    custom_background color (docs/config.md "Appearance"): the color becomes
    the background, surface shades derive via config's shade rule, and text/
    accent come from the canonical dark or light preset by background
    luminance — everything still derives from one preset (WS22).

    A missing or garbage color silently falls back to the default preset
    (config-compat invariant: old configs and junk never crash the shell).
    """
    if str(config.data.get('theme') or '') != 'custom':
        return config.theme
    color = config.custom_background or ''
    # fullmatch, not ^...$: '$' also matches before a trailing newline, and
    # a newline inside the color would leak into every themed CSS sheet.
    if not _CUSTOM_HEX_RE.fullmatch(color):
        return config.theme
    surface, surface_alt, border = _derive_shades(color)
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    text = THEME_PRESETS['amoled'] if luminance < 0.5 else THEME_PRESETS['paper']
    return ThemePreset(
        key='custom', name='Custom', background=color,
        surface=surface, surface_alt=surface_alt, border=border,
        foreground=text.foreground, muted=text.muted, accent=text.accent,
    )


def _back_env() -> dict[str, str]:
    """Env for the back-key injection children (wtype/waydroid): target the
    Wayland session the shell actually runs on, not a hardcoded wayland-0 —
    same rule as display_manager._WL_ENV / home_launcher._WAYDROID_ENV."""
    return {
        'WAYLAND_DISPLAY': os.environ.get('WAYLAND_DISPLAY') or 'wayland-0',
        'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}',
    }


def _set_osk_visible(visible: bool) -> None:
    """Toggle squeekboard's visibility over D-Bus (headless seam for 20.1).

    Both _show_keyboard() and _hide_keyboard() route through here. It is the
    headless seam the tap-outside test drives: monkeypatching Gio's
    bus_get_sync lets a test assert the SetVisible payload without a display.
    """
    from gi.repository import Gio
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(
            'sm.puri.OSK0', '/sm/puri/OSK0', 'sm.puri.OSK0', 'SetVisible',
            GLib.Variant('(b)', (visible,)), None,
            Gio.DBusCallFlags.NONE, 500, None,
        )
    except Exception as error:
        # A D-Bus failure (squeekboard absent, bus down) must not crash the
        # shell, but it is a real fault on a data path — log it rather than
        # silently dropping the show/hide request.
        from shell_log import get_logger
        get_logger('window').warning(
            'set-OSK-visible(%s) over D-Bus failed: %s', visible, error)


class ShellWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title='PiercingXX')
        self.add_css_class('xx-wm')

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.BOTTOM)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            # Zone 0 (not -1): the shell shrinks above other surfaces'
            # exclusive zones — critically the OSK, so the drawer search
            # field rides up above the keyboard instead of hiding under it
            LayerShell.set_exclusive_zone(self, 0)
            # Default interactivity is NONE: the compositor never sends
            # text-input, so tapping Search never raises squeekboard.
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.ON_DEMAND)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        self.config = ShellConfig()
        self.gesture_config = GestureConfig()
        from dnd import DndState
        from focus_mode import FocusState
        self.dnd_state = DndState(self.config)
        self.focus_state = FocusState(self.config)
        self.app_index = AppIndex()
        self.app_index.refresh()

        try:
            from default_layout import apply_default_layout
            apply_default_layout(self.config, self.gesture_config)
        except Exception:
            pass  # first-boot seeding must never block the shell

        self._idle_timer_id: int | None = None
        self._pick_slot_mode = False
        # When set to a gesture key ('swipe_left_home'/'swipe_right_home'),
        # the next drawer tap binds that app to the gesture instead of launching
        self._pick_gesture_key: str | None = None
        self._gesture_binding_labels: dict[str, Gtk.Label] = {}
        self._system_gesture_labels: dict[str, Gtk.Label] = {}
        self._drawer_open_folder: int | None = None
        self._drawer_folder_header: Gtk.ListBoxRow | None = None
        self._last_search_results: list[AppEntry] = []
        self._call_ui: object | None = None
        self._call_bar: object | None = None
        self._shade: object | None = None
        self._switcher: object | None = None
        self._dialer: object | None = None
        self._power_menu: object | None = None
        self._back_layer: object | None = None

        from modem_monitor import ModemMonitor
        self._modem_monitor = ModemMonitor(
            on_incoming=self._on_call_incoming,
            on_answered=self._on_call_answered,
            on_ended=self._on_call_ended,
        )

        self.base_provider = Gtk.CssProvider()
        self.theme_provider = Gtk.CssProvider()
        self._load_css()

        self.stack = Gtk.Stack(
            hexpand=True,
            vexpand=True,
            transition_duration=200,
            transition_type=Gtk.StackTransitionType.NONE,
        )
        # Leaving the drawer disarms the pick modes so a later tap launches
        self.stack.connect(
            'notify::visible-child-name', self._on_stack_page_changed)

        self.item_actions = AppItemActions(
            self.config,
            on_changed=self._refresh_after_item_action,
            on_status=self._show_status,
            focus_state=self.focus_state,
        )

        # Right-aligned flat search, as in the PiercingXX Android launcher
        self.apps_search = Gtk.SearchEntry(placeholder_text='Search')
        self.apps_search.set_alignment(1.0)
        self.apps_search.add_css_class('drawer-search')
        self.apps_search.connect('search-changed', self._on_apps_search_changed)
        self.apps_search.connect('activate', self._on_apps_search_activate)
        self.apps_search.connect('notify::has-focus', self._on_search_focus)

        self.status_label = Gtk.Label(xalign=0)
        self.status_label.add_css_class('dim-label')

        self.app_count_label = Gtk.Label(xalign=0)
        self.app_count_label.add_css_class('dim-label')

        self.clock_label = Gtk.Label(xalign=0)
        self.clock_label.add_css_class('display-clock')

        self.date_label = Gtk.Label(xalign=0)
        self.date_label.add_css_class('display-date')

        self.weather_label = Gtk.Label(xalign=0)
        self.weather_label.add_css_class('display-date')

        self.status_strip = Gtk.Label(xalign=0)
        self.status_strip.add_css_class('dim-label')

        self.apps_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.apps_list.add_css_class('text-list')

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self._build_root())
        self.set_content(self.toast_overlay)

        # Idle monitoring — any motion or key event resets the auto-lock timer
        motion = Gtk.EventControllerMotion.new()
        motion.connect('motion', lambda *_: self._reset_idle_timer())
        self.add_controller(motion)
        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_ctrl)

        self._apply_theme()
        self._refresh_clock()
        self._populate_apps()
        self._refresh_status()
        self._setup_idle_timer()
        self._setup_config_monitor()
        GLib.timeout_add_seconds(1, self._tick_clock)
        GLib.timeout_add_seconds(60, self._tick_status)

        from update_checker import UpdateChecker
        self._update_checker = UpdateChecker(
            self.config, on_updates_available=self._on_updates_available,
        )

    def _load_css(self) -> None:
        style_path = Path(__file__).with_name('style.css')
        self.base_provider.load_from_path(str(style_path))
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, self.base_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.StyleContext.add_provider_for_display(display, self.theme_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

    def _build_root(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class('shell-root')

        self.stack.add_titled(self._build_home_page(), 'home', 'Home')
        self.stack.add_titled(self._build_apps_page(), 'apps', 'Apps')
        self.stack.add_titled(self._build_settings_page(), 'settings', 'Settings')

        swipe = Gtk.GestureSwipe.new()
        swipe.set_touch_only(False)
        swipe.connect('swipe', self._on_stack_swipe)
        self.stack.add_controller(swipe)

        # Tap-outside → hide the OSK (20.1): a tap that lands on a non-editable
        # widget means the user is done typing, so drop squeekboard.
        tap = Gtk.GestureClick.new()
        tap.set_touch_only(False)
        tap.connect('pressed', self._on_tap_outside)
        self.stack.add_controller(tap)

        root.append(self.stack)
        return root

    def _on_stack_swipe(self, _gesture: Gtk.GestureSwipe, vel_x: float, vel_y: float) -> None:
        # Vertical swipes match the PiercingXX Android launcher: up opens the
        # app drawer, down runs the configured action (default: shade)
        if abs(vel_y) > abs(vel_x) * 1.5:
            if vel_y > 300:
                self._dispatch_gesture_action(
                    self.gesture_config.get('swipe_down_top') or 'notification_shade')
            elif vel_y < -_LONG_SWIPE_UP_VEL:
                # Long/fast swipe up → app drawer
                self.stack.set_visible_child_name('apps')
            elif vel_y < -300:
                # Short swipe up → on-screen keyboard
                self._show_keyboard()
            return
        if abs(vel_y) > abs(vel_x):
            return
        current = self.stack.get_visible_child_name()
        # Swipe right in the drawer collapses an open folder drop-down first
        if current == 'apps' and vel_x > 200 and self._drawer_open_folder is not None:
            self._drawer_open_folder = None
            self._populate_apps(self.apps_search.get_text())
            return
        # Home swipes dispatch their configured apps (design.md "Gestures");
        # an unbound ('none') swipe falls through to page navigation so the
        # drawer stays reachable without lisgd.
        if current == 'home' and not self._home_launcher.edit_mode:
            # Sideways on home is app-launch only (launcher parity) — an
            # unbound direction does nothing; the drawer is a swipe up away
            if abs(vel_x) > 200:
                action = self.gesture_config.get(
                    'swipe_left_home' if vel_x < -200 else 'swipe_right_home')
                if action != 'none':
                    self._dispatch_gesture_action(action)
            return
        # Settings is a leaf page: a horizontal swipe from either edge is an
        # unconditional "back to home", so there is always a way out by gesture
        if current == 'settings' and abs(vel_x) > 200:
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.stack.set_visible_child_name('home')
            return
        idx = _PAGE_ORDER.index(current) if current in _PAGE_ORDER else 0
        if vel_x < -200 and idx < len(_PAGE_ORDER) - 1:
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
            self.stack.set_visible_child_name(_PAGE_ORDER[idx + 1])
        elif vel_x > 200 and idx > 0:
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.stack.set_visible_child_name(_PAGE_ORDER[idx - 1])

    def _dispatch_gesture_action(self, action: str) -> None:
        """Run a gesture_config action, including launch:<app_id> bindings."""
        if action.startswith('launch:'):
            self._launch_app_id(action[len('launch:'):])
            return
        if action == 'notification_shade':
            self._show_shade()
        elif action == 'search':
            # Opens the drawer with keyboard focus in the search field
            self.stack.set_visible_child_name('apps')
            self.apps_search.grab_focus()
        elif action == 'home':
            self.stack.set_visible_child_name('home')
        elif action == 'app_switcher':
            self._show_switcher()
        elif action == 'lock_screen':
            self._show_lock_screen()
        elif action == 'settings':
            self.stack.set_visible_child_name('settings')
        elif action == 'dialer':
            self._open_dialer()
        elif action == 'back':
            self._handle_back()
        elif action == 'camera':
            camera = next(
                (e for e in self.app_index.entries if 'camera' in e.name.casefold()), None)
            if camera is not None:
                self._launch_entry(camera)

    def _show_focus_notice(self, label: str) -> None:
        actions = [(f'Take a break ({m} min)',
                    lambda mins=m: self._start_focus_break(mins))
                   for m in (5, 10, 15)]
        actions.append(('Turn off Focus', self._turn_off_focus))
        self.item_actions._show_action_menu(self.stack, f'{label} is paused', actions)

    def _start_focus_break(self, minutes: int) -> None:
        self.focus_state.start_break(minutes)
        self._refresh_after_item_action()
        self._show_status(f'Focus paused for {minutes} minutes.')

    def _turn_off_focus(self) -> None:
        self.focus_state.set_enabled(False)
        self._refresh_after_item_action()
        self._show_status('Focus turned off.')

    def preload_gesture_apps(self) -> None:
        """Warm the swipe-bound apps at session start so the gesture opens a
        resident process instead of cold-starting it (the camera through the
        android HAL can take a minute-plus cold). Launched apps briefly map
        above home; the delayed present_over_apps() re-raises the shell, and
        the next real launch drops it back so the warm window comes forward.
        """
        from gi.repository import Gio
        ids: list[str] = []
        for key in ('swipe_left_home', 'swipe_right_home'):
            action = self.gesture_config.get(key) or 'none'
            if action == 'camera':
                entry = next((e for e in self.app_index.entries
                              if 'camera' in e.name.casefold()), None)
                if entry:
                    ids.append(entry.app_id)
            elif action.startswith('launch:'):
                ids.append(action[len('launch:'):])
        for app_id in ids:
            try:
                info = Gio.DesktopAppInfo.new(app_id)
                if info is not None:
                    info.launch([], None)
            except Exception:
                continue
        if ids:
            GLib.timeout_add_seconds(5, self._reraise_after_preload)

    def _reraise_after_preload(self) -> bool:
        self.stack.set_visible_child_name('home')
        self.present_over_apps()
        return GLib.SOURCE_REMOVE

    def present_over_apps(self) -> None:
        """Raise the shell above regular app windows (the go-home gesture).

        Home lives on the BOTTOM layer, so a focused app covers it; until
        phoc grows foreign-toplevel management this hop to the TOP layer is
        what makes 'swipe up → home' work while an app is open."""
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.set_layer(self, LayerShell.Layer.TOP)

    def drop_to_background(self) -> None:
        """Return the shell to the BOTTOM layer so launched apps show above."""
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.set_layer(self, LayerShell.Layer.BOTTOM)

    def _launch_app_id(self, app_id: str) -> None:
        from gi.repository import Gio
        try:
            info = Gio.DesktopAppInfo.new(app_id)
        except Exception:
            info = None
        if info is None:
            # Uninstalled target — behave like 'none' per Workstream 8.1
            return
        try:
            info.launch([], None)
            self.config.record_launch(app_id)
            self.drop_to_background()
        except GLib.Error as error:
            self._show_status(f'Failed to launch: {error.message}')

    def _build_home_page(self) -> Gtk.Widget:
        from home_launcher import HomeLauncher, theme_css
        from gi.repository import Gdk

        css = Gtk.CssProvider()
        css.load_from_data(theme_css(self.config.theme).encode('utf-8'))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 3,
        )

        # --- Top 1/3: widget block — config-driven, vertically centered ---
        self.clock_label.set_xalign(0.5)
        self.date_label.set_xalign(0.5)
        self.weather_label.set_xalign(0.5)
        self.status_strip.set_xalign(0.5)

        self._clock_tap_timer: int | None = None

        clock_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        clock_inner.set_halign(Gtk.Align.FILL)
        clock_inner.set_hexpand(True)
        self._widget_block = clock_inner
        self._widget_taps_wired: set[str] = set()
        self._build_widget_block()

        self._init_weather()

        # Equal spacers above and below clock_inner → vertically centered in top pane
        top_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top_pane.set_hexpand(True)
        top_pane.set_vexpand(True)
        sp_top = Gtk.Box()
        sp_top.set_vexpand(True)
        sp_bot = Gtk.Box()
        sp_bot.set_vexpand(True)
        top_pane.append(sp_top)
        top_pane.append(clock_inner)
        top_pane.append(sp_bot)

        # --- Bottom 2/3: launcher — vertically centered ---
        def _get_home_slots() -> list[dict]:
            return self.config.home_slots

        def _launch_slot(slot: dict) -> None:
            self._launch_slot(slot)

        self._home_launcher = HomeLauncher(
            open_dialer_fn=self._open_dialer,
            get_slots_fn=_get_home_slots,
            on_launch_slot=_launch_slot,
            on_member_long_press=lambda w, s, m: self.item_actions.show_member_menu(w, s, m),
            on_slot_move=self._move_slot,
            on_slot_remove=self._remove_slot,
            on_slot_rename=self._rename_slot,
            on_edit_action=self._on_edit_action,
            is_paused_fn=self.focus_state.is_paused_app,
            get_alignment_fn=lambda: self.config.home_alignment,
        )

        launcher_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        launcher_inner.set_valign(Gtk.Align.CENTER)
        launcher_inner.set_vexpand(True)
        launcher_inner.set_hexpand(True)
        launcher_inner.append(self._home_launcher)

        launcher_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        launcher_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        launcher_scroll.set_has_frame(False)
        launcher_scroll.add_css_class('home-scroll')
        launcher_scroll.set_child(launcher_inner)

        bot_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bot_pane.set_hexpand(True)
        bot_pane.set_vexpand(True)
        bot_pane.set_margin_bottom(24)
        bot_pane.append(launcher_scroll)

        # Long-press anywhere on home → slot edit mode (design.md "Home screen")
        edit_press = Gtk.GestureLongPress.new()
        edit_press.set_touch_only(False)
        edit_press.connect('pressed', self._on_home_long_press)
        bot_pane.add_controller(edit_press)

        # --- 1/3 widgets / 2/3 launcher — a homogeneous grid, so there is
        # no Paned divider bar and no realize-time ratio hack. The widget
        # block centers in the top third; the slot list centers in the rest.
        grid = Gtk.Grid()
        grid.set_row_homogeneous(True)
        grid.set_hexpand(True)
        grid.set_vexpand(True)
        grid.attach(top_pane, 0, 0, 1, 1)
        grid.attach(bot_pane, 0, 1, 1, 2)
        return grid

    def _open_dialer(self) -> None:
        from dialer import Dialer
        if self._dialer and self._dialer.get_visible():
            return
        d = Dialer(dnd_state=self.dnd_state, config=self.config)
        d.set_application(self.get_application())
        # Lazy mid-session construction must render the CURRENT preset, not
        # whatever the injected config resolved to at construction inside
        # the surface (apply_theme() there is only a standalone fallback).
        d.apply_theme(resolve_theme(self.config))
        d.present()
        self._dialer = d

    def _launch_slot(self, slot: dict) -> None:
        """Launch an app or command from a home slot."""
        app_id = slot.get('app_id')
        if app_id and self.focus_state.is_paused_app(str(app_id)):
            self._show_focus_notice(slot.get('label', 'App'))
            return
        # Record usage
        if app_id:
            self.config.record_launch(app_id)

        # Launch based on type
        slot_type = slot.get('type')
        if slot_type == 'app':
            android_pkg = slot.get('app_id')
            cmd = slot.get('cmd')
            label = slot.get('label', '')

            if label == 'Phone' and not android_pkg and not cmd:
                # Built-in dialer
                self._open_dialer()
            elif android_pkg:
                # Desktop-file ids (incl. waydroid.*.desktop wrappers) launch
                # via Gio; only bare Android package names go through waydroid
                if str(android_pkg).endswith('.desktop'):
                    self._launch_app_id(str(android_pkg))
                else:
                    from home_launcher import _launch_android
                    _launch_android(android_pkg)
                    self.drop_to_background()
            elif cmd:
                # Command
                from home_launcher import _launch_cmd
                _launch_cmd(cmd)
                self.drop_to_background()

    def _handle_back(self) -> None:
        """Called by BackGestureLayer on edge swipe from either side."""
        import subprocess
        # 1. Dismiss notification shade if open
        if self._shade and self._shade.get_visible():
            self._shade.hide_shade()
            return
        # 2. Dismiss app switcher if open
        if self._switcher and self._switcher.get_visible():
            self._switcher.hide_switcher()
            return
        # 3. Close dialer if open
        if self._dialer and self._dialer.get_visible():
            self._dialer.close()
            return
        # 4. Navigate back within the shell stack
        current = self.stack.get_visible_child_name()
        if current in ('apps', 'settings'):
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.stack.set_visible_child_name('home')
            return
        # 5. Nothing shell-owned is open — send back to whatever is focused
        env = {**os.environ, **_back_env()}
        try:
            subprocess.Popen(['wtype', '-k', 'Escape'], env=env,
                             close_fds=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass
        # Always try Android KEYCODE_BACK — exits silently if Waydroid isn't running.
        # Deliberately no status check: check_output blocks the main thread for up to 1s.
        try:
            subprocess.Popen(
                ['waydroid', 'shell', 'input', 'keyevent', '4'],
                env=env, close_fds=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass

    def _build_widget_block(self) -> None:
        block = self._widget_block
        child = block.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            block.remove(child)
            child = nxt
        widget_map = {
            'time': self.clock_label,
            'date': self.date_label,
            'weather': self.weather_label,
            'battery': self.status_strip,
        }
        for key, _conf in self.config.ordered_widgets():
            widget = widget_map.get(key)
            if widget is None:
                continue
            if key not in self._widget_taps_wired:
                self._wire_widget_tap(widget, key)
                self._widget_taps_wired.add(key)
            block.append(widget)

    def _current_tap(self, key: str) -> object:
        # Read at dispatch time so a hot-reloaded config takes effect
        return self.config.widgets.get(key, {}).get('tap', 'default')

    def _wire_widget_tap(self, widget: Gtk.Widget, key: str) -> None:
        if key == 'time':
            # The clock keeps double-tap-to-lock; a single tap (after the
            # double-tap window passes) runs the configured action
            tap_gesture = Gtk.GestureClick.new()
            tap_gesture.connect(
                'pressed', lambda _g, n, _x, _y: self._on_clock_tapped(n))
            widget.add_controller(tap_gesture)
            return
        gesture = Gtk.GestureClick.new()
        gesture.connect(
            'pressed',
            lambda _g, _n, _x, _y, k=key: self._dispatch_widget_tap(k, self._current_tap(k)))
        widget.add_controller(gesture)

    def _on_clock_tapped(self, n_press: int) -> None:
        if n_press == 2:
            if self._clock_tap_timer is not None:
                GLib.source_remove(self._clock_tap_timer)
                self._clock_tap_timer = None
            self._show_lock_screen()
            return
        if n_press == 1 and self._clock_tap_timer is None:
            def _fire() -> bool:
                self._clock_tap_timer = None
                tap = self._current_tap('time')
                if tap != 'none':
                    self._dispatch_widget_tap('time', tap)
                return GLib.SOURCE_REMOVE
            self._clock_tap_timer = GLib.timeout_add(280, _fire)

    def _dispatch_widget_tap(self, key: str, tap: object) -> None:
        if isinstance(tap, dict) and tap.get('app'):
            self._launch_app_id(str(tap['app']))
            return
        if tap == 'refresh' or (key == 'weather' and tap == 'default'):
            self._refresh_weather(force=True)
            return
        if tap != 'default':
            return
        if key == 'time':
            self._launch_first_matching('clock')
        elif key == 'date':
            self._launch_first_matching('calendar')
        elif key == 'battery':
            self.stack.set_visible_child_name('settings')

    def _launch_first_matching(self, needle: str) -> None:
        entry = next(
            (e for e in self.app_index.entries if needle in e.name.casefold()), None)
        if entry is not None:
            self._launch_entry(entry)

    def _init_weather(self) -> None:
        from weather import WeatherProvider, PLACEHOLDER
        self._weather = WeatherProvider(self.config)
        self.weather_label.set_text(PLACEHOLDER)
        self._refresh_weather()
        GLib.timeout_add_seconds(900, self._tick_weather)

    def _tick_weather(self) -> bool:
        self._refresh_weather()
        return True

    def _refresh_weather(self, force: bool = False) -> None:
        self._weather.refresh(self.weather_label.set_text, force=force)

    def _show_lock_screen(self) -> None:
        lock = getattr(self, '_lock_screen', None)
        if lock is not None:
            if not lock.get_visible():
                lock.prepare()
            lock.present()
            lock.set_visible(True)
            return
        from lock_screen import LockScreen
        self._lock_screen = LockScreen(
            on_unlock=self._dismiss_lock_screen,
            config=self.config,
            get_notifications=lambda: self._ensure_shade().notifications_snapshot(),
            dnd_active_fn=lambda: self.dnd_state.is_active(),
            on_open_shade=self._show_shade,
        )
        self._lock_screen.set_application(self.get_application())
        self._lock_screen.present()

    def _dismiss_lock_screen(self) -> None:
        lock = getattr(self, '_lock_screen', None)
        if lock is not None:
            lock.set_visible(False)

    def try_fingerprint_unlock(self) -> None:
        lock = getattr(self, '_lock_screen', None)
        if lock is not None and lock.get_visible():
            lock.try_fingerprint_unlock()

    def _ensure_shade(self) -> object:
        if self._shade is None:
            from notification_shade import NotificationShade
            # The app (XXWMApplication) owns the Hud; the shade is created lazily
            # after _show_shell sets _hud, so it is always present here. getattr
            # keeps this safe even if the shade is ever built before the HUD.
            hud = getattr(self.get_application(), '_hud', None)
            self._shade = NotificationShade(
                dnd_state=self.dnd_state,
                focus_state=self.focus_state,
                on_open_settings=lambda: (
                    self.stack.set_visible_child_name('settings'), self.present()),
                on_power=self._show_power_menu,
                hud=hud,
                config=self.config,
            )
            self._shade.set_application(self.get_application())
            # Same current-preset rule as _open_dialer: the shade can be
            # built long after startup, under a hot-reloaded theme.
            self._shade.apply_theme(resolve_theme(self.config))
        return self._shade

    def _show_shade(self) -> None:
        self._ensure_shade().show_shade()

    def _show_power_menu(self) -> None:
        if self._power_menu is None:
            from power_menu import PowerMenu
            self._power_menu = PowerMenu(config=self.config)
            self._power_menu.set_application(self.get_application())
            # Same current-preset rule as _open_dialer.
            self._power_menu.apply_theme(resolve_theme(self.config))
        self._power_menu.show_menu()

    def _show_keyboard(self) -> None:
        """Force the on-screen keyboard up via squeekboard's D-Bus interface."""
        _set_osk_visible(True)

    def _hide_keyboard(self) -> None:
        """Hide squeekboard when focus leaves an editable widget (20.1)."""
        _set_osk_visible(False)

    def _on_tap_outside(self, _gesture: Gtk.GestureClick, _n_press: int,
                        x: float, y: float) -> None:
        """Hide squeekboard when a tap lands outside any editable widget.

        The shell's only editable widget is the drawer search entry; a tap
        anywhere else (home, the app list, settings) means the user is done
        typing, so drop the OSK instead of leaving it floating (20.1). This
        lives in the shell's focus handling, not per-surface hacks.
        """
        try:
            widget = self.stack.pick(x, y, Gtk.PickFlags.DEFAULT)
        except Exception as error:
            # pick() failed — we cannot tell what the tap landed on, so do not
            # guess by dismissing the OSK. Log it instead of masking the fault.
            from shell_log import get_logger
            get_logger('window').warning(
                'tap-outside pick(%s, %s) failed: %s', x, y, error)
            return
        while widget is not None:
            if isinstance(widget, Gtk.Editable):
                return
            widget = widget.get_parent() if hasattr(widget, 'get_parent') else None
        self._hide_keyboard()

    def _show_switcher(self) -> None:
        if self._switcher is None:
            from app_switcher import AppSwitcher
            self._switcher = AppSwitcher(config=self.config)
            self._switcher.set_application(self.get_application())
        self._switcher.show_switcher()

    def _build_apps_page(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_vexpand(True)
        outer.set_margin_bottom(24)
        outer.set_margin_start(24)
        outer.set_margin_end(0)

        self._sort_mode = 'az'

        # No 'All apps' header or count — the drawer is just the list, like
        # the PiercingXX Android launcher; the sort toggle rides alone, dim
        self._sort_btn = Gtk.Button(label='A-Z')
        self._sort_btn.add_css_class('flat')
        self._sort_btn.add_css_class('dim-label')
        self._sort_btn.set_halign(Gtk.Align.END)
        self._sort_btn.connect('clicked', self._toggle_sort_mode)

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_row.set_margin_end(24)
        header_row.append(Gtk.Box(hexpand=True))
        header_row.append(self._sort_btn)

        self.apps_search.set_margin_end(24)

        self.apps_scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.apps_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.apps_scroller.set_child(self.apps_list)

        # A-Z jump strip on the right edge
        self._alpha_letter_rows: dict[str, int] = {}
        alpha_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        alpha_box.set_valign(Gtk.Align.CENTER)
        alpha_box.set_margin_end(4)

        for ch in '#ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            btn = Gtk.Button(label=ch)
            btn.add_css_class('flat')
            btn.add_css_class('alpha-jump')
            btn.connect('clicked', self._on_alpha_jump, ch)
            alpha_box.append(btn)

        list_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        list_row.append(self.apps_scroller)
        list_row.append(alpha_box)

        # Search lives at the bottom where the thumb is; keyboard only on tap
        outer.append(header_row)
        outer.append(list_row)
        outer.append(self.apps_search)

        # The drawer leaves the top ~15% of the screen free so it reads as a
        # sheet rather than a full-screen takeover.
        spacer = Gtk.Box()
        sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sheet.set_vexpand(True)
        sheet.append(spacer)
        sheet.append(outer)

        def _set_sheet_ratio(w: Gtk.Box) -> None:
            def _apply() -> bool:
                h = w.get_height()
                if h > 0:
                    spacer.set_size_request(-1, int(h * 0.15))
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE
            GLib.idle_add(_apply)
        sheet.connect('realize', _set_sheet_ratio)
        return sheet

    def _toggle_sort_mode(self, _btn: Gtk.Button) -> None:
        self._sort_mode = 'install' if self._sort_mode == 'az' else 'az'
        self._sort_btn.set_label('Install date' if self._sort_mode == 'install' else 'A-Z')
        self._populate_apps(self.apps_search.get_text())

    def _home_slot_app_ids(self) -> set[str]:
        ids: set[str] = set()
        for slot in self.config.home_slots:
            if slot.get('app_id'):
                ids.add(str(slot['app_id']))
            for member in slot.get('folder') or []:
                if member.get('app_id'):
                    ids.add(str(member['app_id']))
        return ids

    def _drawer_folder_slots(self) -> list[tuple[int, dict, list[tuple[int, dict]]]]:
        """Folder slots the drawer shows, in home-slot order (folders first).

        Members keep their original folder-list indices so the shared member
        menu edits the right entry; members whose app is not installed are
        skipped at render, and empty folders never appear.
        """
        installed = {entry.app_id for entry in self.app_index.entries}
        out: list[tuple[int, dict, list[tuple[int, dict]]]] = []
        for idx, slot in enumerate(self.config.home_slots):
            if slot.get('type') != 'folder':
                continue
            members = [
                (m_idx, m) for m_idx, m in enumerate(slot.get('folder') or [])
                if m.get('app_id') and str(m['app_id']) in installed
            ]
            if members:
                out.append((idx, slot, members))
        return out

    @staticmethod
    def _install_time(entry: AppEntry) -> float:
        try:
            filename = entry.desktop_app.get_filename()
            return Path(filename).stat().st_mtime if filename else 0.0
        except OSError:
            return 0.0

    def _on_alpha_jump(self, _btn: Gtk.Button, letter: str) -> None:
        idx = self._alpha_letter_rows.get(letter)
        if idx is None and letter == '#':
            idx = 0
        if idx is None:
            return
        row = self.apps_list.get_row_at_index(idx)
        if row is None:
            return
        alloc = row.get_allocation()
        adj = self.apps_scroller.get_vadjustment()
        adj.set_value(max(0.0, alloc.y))

    def _on_search_focus(self, entry: Gtk.SearchEntry, _pspec=None) -> None:
        if entry.has_focus():
            self._show_keyboard()

    def _on_stack_page_changed(self, stack: Gtk.Stack, _param: object) -> None:
        # Leaving the drawer disarms both pick modes so a later tap launches
        if stack.get_visible_child_name() != 'apps':
            self._pick_slot_mode = False
            self._pick_gesture_key = None
            self._hide_keyboard()

    def _gesture_binding_text(self, key: str) -> str:
        action = self.gesture_config.get(key) or 'none'
        if action == 'none':
            return 'Not set'
        if action in _VERB_LABELS:
            return _VERB_LABELS[action]
        if action == 'camera':
            return 'Camera'
        if action.startswith('launch:'):
            app_id = action[len('launch:'):]
            for entry in self.app_index.entries:
                if entry.app_id == app_id:
                    return self.config.label_for(app_id, entry.name)
            return app_id
        return action.replace('_', ' ').title()

    def _refresh_gesture_labels(self) -> None:
        for key, label in self._gesture_binding_labels.items():
            label.set_text(self._gesture_binding_text(key))
        for key, label in self._system_gesture_labels.items():
            label.set_text(self._gesture_binding_text(key))

    def _build_gestures_card(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class('settings-card')
        for key, title in _GESTURE_TITLES.items():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            name = Gtk.Label(label=title, xalign=0)
            name.set_hexpand(True)

            binding = Gtk.Label(label=self._gesture_binding_text(key), xalign=1)
            binding.add_css_class('dim-label')
            self._gesture_binding_labels[key] = binding

            change = Gtk.Button(label='Change')
            change.add_css_class('flat')
            change.add_css_class('action-link')
            change.connect('clicked', lambda _b, k=key, t=title: self._pick_gesture_app(k, t))

            clear = Gtk.Button(label='Clear')
            clear.add_css_class('flat')
            clear.add_css_class('dim-label')
            clear.connect('clicked', lambda _b, k=key: self._clear_gesture_app(k))

            row.append(name)
            row.append(binding)
            row.append(change)
            row.append(clear)
            card.append(row)

        for key, title in _SYSTEM_GESTURE_TITLES.items():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            name = Gtk.Label(label=title, xalign=0)
            name.set_hexpand(True)

            binding = Gtk.Label(label=self._gesture_binding_text(key), xalign=1)
            binding.add_css_class('dim-label')
            self._system_gesture_labels[key] = binding

            change = Gtk.Button(label='Change')
            change.add_css_class('flat')
            change.add_css_class('action-link')
            change.connect(
                'clicked', lambda b, k=key, t=title: self._pick_system_gesture(b, k, t))

            clear = Gtk.Button(label='Default')
            clear.add_css_class('flat')
            clear.add_css_class('dim-label')
            clear.connect('clicked', lambda _b, k=key: self._reset_system_gesture(k))

            row.append(name)
            row.append(binding)
            row.append(change)
            row.append(clear)
            card.append(row)
        return card

    def _pick_system_gesture(self, anchor: Gtk.Widget, key: str, title: str) -> None:
        choices = [(_ACTION_LABELS[a], lambda a=a: self._set_system_gesture(key, a))
                   for a in sorted(VALID_ACTIONS)]
        choices += [(_VERB_LABELS[v], lambda v=v: self._set_system_gesture(key, v))
                    for v in sorted(IPC_VERBS)]
        self.item_actions._show_action_menu(anchor, title, choices)

    def _set_system_gesture(self, key: str, value: str) -> None:
        self.gesture_config.set(key, value)
        self._refresh_gesture_labels()

    def _reset_system_gesture(self, key: str) -> None:
        self.gesture_config.reset(key)
        self._refresh_gesture_labels()

    def _pick_gesture_app(self, key: str, title: str) -> None:
        self._pick_gesture_key = key
        self.stack.set_visible_child_name('apps')
        self._show_status(f'Tap an app to bind to {title}.')

    def _clear_gesture_app(self, key: str) -> None:
        self.gesture_config.set(key, 'none')
        self._refresh_gesture_labels()

    def _build_settings_page(self) -> Gtk.Widget:
        from importlib.metadata import version as get_version
        try:
            shell_version = get_version('xx-wm')
        except Exception:
            shell_version = '0.1.1'

        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add_css_class('settings-page')

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        box.set_margin_top(36)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        title = Gtk.Label(label='Shell settings', xalign=0)
        title.add_css_class('section-title')

        # System updates
        system_title = Gtk.Label(label='System', xalign=0)
        system_title.add_css_class('section-title')

        system_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        system_card.add_css_class('settings-card')

        update_button = Gtk.Button(label='Update system')
        update_button.add_css_class('flat')
        update_button.add_css_class('action-link')
        update_button.connect('clicked', self._on_update_clicked)

        check_updates_button = Gtk.Button(label='Check for updates now')
        check_updates_button.add_css_class('flat')
        check_updates_button.add_css_class('action-link')
        check_updates_button.connect('clicked', self._on_check_updates_clicked)

        system_card.append(update_button)
        system_card.append(check_updates_button)

        # Backup & restore
        backup_title = Gtk.Label(label='Backup & restore', xalign=0)
        backup_title.add_css_class('section-title')

        backup_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        backup_card.add_css_class('settings-card')

        export_btn = Gtk.Button(label='Export backup')
        export_btn.add_css_class('flat')
        export_btn.add_css_class('action-link')
        export_btn.connect('clicked', lambda _btn: self._on_export_backup())

        restore_btn = Gtk.Button(label='Restore from backup')
        restore_btn.add_css_class('flat')
        restore_btn.add_css_class('action-link')
        restore_btn.connect('clicked', lambda _btn: self._on_restore_backup())

        backup_card.append(export_btn)
        backup_card.append(restore_btn)

        # WiFi
        wifi_title = Gtk.Label(label='WiFi', xalign=0)
        wifi_title.add_css_class('section-title')

        wifi_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        wifi_card.add_css_class('settings-card')
        self._wifi_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        wifi_scan_btn = Gtk.Button(label='Scan for networks')
        wifi_scan_btn.add_css_class('flat')
        wifi_scan_btn.add_css_class('action-link')
        wifi_scan_btn.connect('clicked', lambda _b: self._refresh_wifi())
        wifi_card.append(self._wifi_list_box)
        wifi_card.append(wifi_scan_btn)

        # Bluetooth
        bt_title = Gtk.Label(label='Bluetooth', xalign=0)
        bt_title.add_css_class('section-title')

        bt_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        bt_card.add_css_class('settings-card')
        self._bt_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bt_scan_btn = Gtk.Button(label='Scan for devices')
        bt_scan_btn.add_css_class('flat')
        bt_scan_btn.add_css_class('action-link')
        bt_scan_btn.connect('clicked', lambda _b: self._refresh_bluetooth(scan=True))
        bt_card.append(self._bt_list_box)
        bt_card.append(bt_scan_btn)

        # Sound output
        sound_title = Gtk.Label(label='Sound output', xalign=0)
        sound_title.add_css_class('section-title')

        sound_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sound_card.add_css_class('settings-card')
        self._sink_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sound_card.append(self._sink_list_box)

        # Battery
        battery_title = Gtk.Label(label='Battery', xalign=0)
        battery_title.add_css_class('section-title')

        battery_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        battery_card.add_css_class('settings-card')
        self._battery_label = Gtk.Label(label='Checking…', xalign=0)
        self._battery_label.add_css_class('dim-label')
        battery_card.append(self._battery_label)

        # Refresh the quick sections whenever the settings page is opened
        self.stack.connect(
            'notify::visible-child-name',
            lambda stack, _p: self._refresh_system_sections()
            if stack.get_visible_child_name() == 'settings' else None,
        )

        # Mobile data / APN
        network_title = Gtk.Label(label='Mobile data', xalign=0)
        network_title.add_css_class('section-title')

        apn_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        apn_card.add_css_class('settings-card')

        self.apn_entry = Gtk.Entry()
        self.apn_entry.set_placeholder_text('APN (e.g. internet)')
        self.apn_entry.set_text(self.config.data.get('apn', ''))

        self.apn_user_entry = Gtk.Entry()
        self.apn_user_entry.set_placeholder_text('Username (leave blank if none)')
        self.apn_user_entry.set_text(self.config.data.get('apn_user', ''))

        self.apn_pass_entry = Gtk.Entry()
        self.apn_pass_entry.set_visibility(False)
        self.apn_pass_entry.set_placeholder_text('Password (leave blank if none)')
        self.apn_pass_entry.set_text(self.config.data.get('apn_pass', ''))

        apn_save_btn = Gtk.Button(label='Save APN')
        apn_save_btn.add_css_class('flat')
        apn_save_btn.add_css_class('action-link')
        apn_save_btn.connect('clicked', self._on_apn_save)

        apn_card.append(self._settings_row('APN', self.apn_entry))
        apn_card.append(self._settings_row('Username', self.apn_user_entry))
        apn_card.append(self._settings_row('Password', self.apn_pass_entry))
        apn_card.append(apn_save_btn)

        # About
        about_title = Gtk.Label(label='About', xalign=0)
        about_title.add_css_class('section-title')

        about_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        about_card.add_css_class('settings-card')

        version_label = Gtk.Label(label=f'Version: {shell_version}', xalign=0)
        version_label.add_css_class('dim-label')

        device_label = Gtk.Label(label=f'Device: {self._get_device_name()}', xalign=0)
        device_label.add_css_class('dim-label')

        about_card.append(version_label)
        about_card.append(device_label)

        gestures_title = Gtk.Label(label='Gestures', xalign=0)
        gestures_title.add_css_class('section-title')

        box.append(system_title)
        box.append(system_card)
        box.append(gestures_title)
        box.append(self._build_gestures_card())
        box.append(wifi_title)
        box.append(wifi_card)
        box.append(bt_title)
        box.append(bt_card)
        box.append(sound_title)
        box.append(sound_card)
        box.append(battery_title)
        box.append(battery_card)
        box.append(backup_title)
        box.append(backup_card)
        box.append(network_title)
        box.append(apn_card)
        box.append(about_title)
        box.append(about_card)

        scroll.set_child(box)
        return scroll


    # -- system settings sections (design.md "Settings scope") -------------

    @staticmethod
    def _run_bg(work, on_done) -> None:
        import threading

        def _worker() -> None:
            result = None
            try:
                result = work()
            except Exception:
                pass
            GLib.idle_add(lambda: on_done(result) and False or False)

        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def _replace_box_children(box: Gtk.Box, children: list[Gtk.Widget]) -> None:
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt
        for widget in children:
            box.append(widget)

    def _section_placeholder(self, text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class('dim-label')
        return label

    def _refresh_system_sections(self) -> None:
        import system_settings
        self._run_bg(system_settings.battery_status, self._show_battery)
        self._run_bg(system_settings.list_audio_sinks, self._show_sinks)
        self._run_bg(system_settings.list_bt_devices, self._show_bt_devices)

    def _show_battery(self, status: str | None) -> None:
        self._battery_label.set_text(status or 'No battery detected.')

    def _show_sinks(self, sinks) -> None:
        if sinks is None:
            self._replace_box_children(
                self._sink_list_box,
                [self._section_placeholder('Audio system unavailable.')])
            return
        rows = []
        for sink in sinks:
            marker = '● ' if sink.is_default else ''
            btn = Gtk.Button(label=marker + sink.description)
            btn.add_css_class('flat')
            btn.add_css_class('action-link' if sink.is_default else 'dim-label')
            btn.get_child().set_xalign(0)
            btn.connect('clicked', lambda _b, s=sink: self._on_sink_selected(s))
            rows.append(btn)
        self._replace_box_children(
            self._sink_list_box,
            rows or [self._section_placeholder('No output devices.')])

    def _on_sink_selected(self, sink) -> None:
        import system_settings
        self._run_bg(
            lambda: system_settings.set_default_sink(sink.name),
            lambda ok: (self._show_status(
                f'Output set to {sink.description}.' if ok else 'Could not switch output.'),
                self._run_bg(system_settings.list_audio_sinks, self._show_sinks)),
        )

    def _refresh_wifi(self) -> None:
        import system_settings
        self._replace_box_children(
            self._wifi_list_box, [self._section_placeholder('Scanning…')])
        self._run_bg(system_settings.list_wifi, self._show_wifi)

    def _show_wifi(self, networks) -> None:
        if networks is None:
            self._replace_box_children(
                self._wifi_list_box,
                [self._section_placeholder('WiFi unavailable on this device.')])
            return
        rows = []
        for net in networks[:12]:
            marker = '● ' if net.in_use else ''
            lock = '' if net.security == 'open' else '  ⚿'
            btn = Gtk.Button(label=f'{marker}{net.ssid}{lock}  ({net.signal}%)')
            btn.add_css_class('flat')
            btn.add_css_class('action-link' if net.in_use else 'dim-label')
            btn.get_child().set_xalign(0)
            btn.connect('clicked', lambda _b, n=net, w=btn: self._on_wifi_selected(n, w))
            rows.append(btn)
        self._replace_box_children(
            self._wifi_list_box,
            rows or [self._section_placeholder('No networks found.')])

    def _on_wifi_selected(self, net, widget: Gtk.Widget) -> None:
        if net.in_use:
            self._show_status(f'Already connected to {net.ssid}.')
            return
        if net.security == 'open':
            self._connect_wifi(net.ssid, None)
            return
        self.item_actions._show_entry_dialog(
            widget, f'Password for {net.ssid}', '', 'Connect',
            lambda password: self._connect_wifi(net.ssid, password))

    def _connect_wifi(self, ssid: str, password: str | None) -> None:
        import system_settings
        self._show_status(f'Connecting to {ssid}…')
        self._run_bg(
            lambda: system_settings.connect_wifi(ssid, password),
            lambda result: (self._show_status(result[1] if result else 'Connection failed.'),
                            self._refresh_wifi()),
        )

    def _refresh_bluetooth(self, scan: bool = False) -> None:
        import system_settings
        if scan:
            self._replace_box_children(
                self._bt_list_box, [self._section_placeholder('Scanning…')])
            system_settings.start_bt_discovery()
            GLib.timeout_add_seconds(
                6, lambda: self._run_bg(system_settings.list_bt_devices,
                                        self._show_bt_devices) or False)
            return
        self._run_bg(system_settings.list_bt_devices, self._show_bt_devices)

    def _show_bt_devices(self, devices) -> None:
        if devices is None:
            self._replace_box_children(
                self._bt_list_box,
                [self._section_placeholder('Bluetooth unavailable on this device.')])
            return
        rows = []
        for dev in devices[:12]:
            state = ' — connected' if dev.connected else (' — paired' if dev.paired else '')
            btn = Gtk.Button(label=dev.name + state)
            btn.add_css_class('flat')
            btn.add_css_class('action-link' if dev.connected else 'dim-label')
            btn.get_child().set_xalign(0)
            btn.connect('clicked', lambda _b, d=dev: self._on_bt_selected(d))
            rows.append(btn)
        self._replace_box_children(
            self._bt_list_box,
            rows or [self._section_placeholder('No devices — scan to discover.')])

    def _on_bt_selected(self, dev) -> None:
        import system_settings
        action = (system_settings.connect_bt_device if dev.paired
                  else system_settings.pair_bt_device)
        self._show_status(f'{"Connecting" if dev.paired else "Pairing"} {dev.name}…')
        self._run_bg(
            lambda: action(dev.address),
            lambda result: (self._show_status(result[1] if result else 'Bluetooth error.'),
                            self._refresh_bluetooth()),
        )

    def _settings_row(self, title: str, control: Gtk.Widget) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        label = Gtk.Label(label=title, xalign=0)
        label.set_hexpand(True)
        row.append(label)
        row.append(control)
        return row

    def _apply_theme(self, preset: ThemePreset | None = None) -> None:
        from font_theme import apply_global_font
        apply_global_font(self.config.font_family)
        theme = preset if preset is not None else resolve_theme(self.config)
        style_manager = Adw.StyleManager.get_default()
        if self.config.prefer_dark:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

        scale = self.config.text_size_scale
        css = f"""
        .shell-root {{
            background: {theme.background};
            color: {theme.foreground};
            font-family: '{self.config.font_family}';
            font-size: {scale}rem;
        }}

        .shell-root entry,
        .shell-root text,
        .shell-root list,
        .shell-root row,
        .shell-root button,
        .shell-root box,
        .shell-root scrolledwindow,
        .shell-root label {{
            color: {theme.foreground};
        }}

        .shell-root entry,
        .shell-root list,
        .shell-root row,
        .shell-root scrolledwindow,
        .settings-card {{
            background: {theme.surface};
            border-color: {theme.border};
        }}

        /* The home slot list sits directly on the wallpaper-black root —
           without this it picks up the generic scrolledwindow surface tint */
        .shell-root scrolledwindow.home-scroll,
        .shell-root scrolledwindow.home-scroll > viewport {{
            background: transparent;
        }}

        /* Drawer search stays flat text — no entry box chrome */
        .shell-root entry.drawer-search,
        .shell-root searchentry.drawer-search {{
            background: transparent;
            border: none;
            box-shadow: none;
        }}

        .shell-root button:hover,
        .shell-root button:focus {{
            background: {theme.surface_alt};
        }}

        .dim-label,
        .eyebrow {{
            color: {theme.muted};
        }}

        .action-link {{
            color: {theme.accent};
        }}

        /* Long-press menus render outside .shell-root; theme them directly
           so every dialog follows the launcher font and colors. */
        popover.action-menu > contents {{
            background: {theme.surface};
            color: {theme.foreground};
            font-family: '{self.config.font_family}';
            font-size: {scale}rem;
            border: 1px solid {theme.border};
        }}

        popover.action-menu button,
        popover.action-menu label {{
            color: {theme.foreground};
        }}

        popover.action-menu button:hover,
        popover.action-menu button:focus {{
            background: {theme.surface_alt};
        }}

        popover.action-menu entry {{
            background: {theme.surface_alt};
            color: {theme.foreground};
        }}
        """
        self.theme_provider.load_from_data(css.encode('utf-8'))

    def _refresh_clock(self) -> None:
        now = datetime.now()
        self.clock_label.set_text(now.strftime('%H:%M'))
        self.date_label.set_text(now.strftime('%a, %b %d').replace(' 0', ' '))

    def _tick_clock(self) -> bool:
        self._refresh_clock()
        return True

    def _refresh_status(self) -> None:
        self.status_strip.set_text(status_line())

    def _tick_status(self) -> bool:
        self._refresh_status()
        return True

    def _refresh_index(self, _button: Gtk.Button | None = None) -> None:
        self.app_index.refresh()
        self._home_launcher.refresh()
        self._populate_apps(self.apps_search.get_text())
        self._show_status('Application index rebuilt.')

    def _populate_apps(self, query: str = '') -> None:
        results = self.app_index.search(query)
        labels = self.config.app_labels
        trimmed = query.strip().casefold()

        if trimmed:
            # Search surfaces everything: hidden apps, home-slot apps, folder
            # members — plus matches against renamed labels.
            matched = {e.app_id for e in results}
            renamed_hits = [
                e for e in self.app_index.entries
                if e.app_id not in matched and trimmed in labels.get(e.app_id, '').casefold()
            ]
            results = results + renamed_hits
        else:
            # The browse list hides hidden apps and everything already on home
            browse_hidden = set(self.config.hidden_apps) | self._home_slot_app_ids()
            results = [e for e in results if e.app_id not in browse_hidden]
            if getattr(self, '_sort_mode', 'az') == 'install':
                results = sorted(results, key=self._install_time, reverse=True)
            # Pinned apps surface first, in their pinned order
            pinned_order = {app_id: i for i, app_id in enumerate(self.config.pinned)}
            pinned = sorted((e for e in results if e.app_id in pinned_order),
                            key=lambda e: pinned_order[e.app_id])
            results = pinned + [e for e in results if e.app_id not in pinned_order]

        self._last_search_results = results if trimmed else []

        # Home folders stay on home — the drawer only lists apps that are
        # not already a slot or folder member (they remain searchable)
        folder_slots = self._drawer_folder_slots()
        if (self._drawer_open_folder is not None
                and self._drawer_open_folder not in {i for i, _s, _m in folder_slots}):
            self._drawer_open_folder = None

        expanded_members = 0
        for i, _slot, members in folder_slots:
            if i == self._drawer_open_folder:
                expanded_members = len(members)
        row_offset = len(folder_slots) + expanded_members

        # Build letter→first-row-index map for A-Z jump strip
        self._alpha_letter_rows = {}
        for idx, entry in enumerate(results):
            display = labels.get(entry.app_id, entry.name)
            first = display[0].upper() if display else '#'
            letter = first if first.isalpha() else '#'
            if letter not in self._alpha_letter_rows:
                self._alpha_letter_rows[letter] = idx + row_offset

        self._replace_rows(self.apps_list, results, 'No apps matched this search.', self._make_app_row)

        # Folder drop-downs sit above the app rows (design.md "App drawer")
        self._drawer_folder_header = None
        insert_at = 0
        for idx, slot, members in folder_slots:
            header = self._make_drawer_folder_row(slot, idx)
            if idx == self._drawer_open_folder:
                self._drawer_folder_header = header
            self.apps_list.insert(header, insert_at)
            insert_at += 1
            if idx == self._drawer_open_folder:
                for m_idx, member in members:
                    self.apps_list.insert(
                        self._make_drawer_member_row(idx, m_idx, member), insert_at)
                    insert_at += 1

        # Synthetic "Launcher Settings" entry always sits at the very end
        if not trimmed or trimmed in 'launcher settings':
            self.apps_list.append(self._make_settings_row())

        # Search results anchor at the bottom, above the search field, within
        # thumb reach; a set that overflows the view still reads from the top.
        self.apps_list.set_valign(Gtk.Align.END if trimmed else Gtk.Align.FILL)
        if trimmed:
            GLib.idle_add(self._scroll_apps_to_top)

        if trimmed:
            self.app_count_label.set_text(f'{len(results)} matches')
        else:
            self.app_count_label.set_text(f'{len(results)} apps indexed')

    def _scroll_apps_to_top(self) -> bool:
        self.apps_scroller.get_vadjustment().set_value(0.0)
        return GLib.SOURCE_REMOVE

    def _refresh_after_item_action(self) -> None:
        # Keep any open folder drop-down open, mirroring the Android launcher
        self._home_launcher.refresh(preserve_folder=True)
        self._populate_apps(self.apps_search.get_text())

    # -- home edit mode ----------------------------------------------------

    def _on_home_long_press(self, gesture: Gtk.GestureLongPress, _x: float, _y: float) -> None:
        if self._home_launcher.edit_mode:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._home_launcher.set_edit_mode(True)

    def _on_edit_action(self, action: str, widget: Gtk.Widget) -> None:
        if action == 'done':
            self._home_launcher.set_edit_mode(False)
        elif action == 'settings':
            self._home_launcher.set_edit_mode(False)
            self.stack.set_visible_child_name('settings')
        elif action == 'add_app':
            self._pick_slot_mode = True
            self.stack.set_visible_child_name('apps')
            self._show_status('Tap an app to add it to home.')
        elif action == 'new_folder':
            self.item_actions._show_entry_dialog(
                widget, 'New folder', '', 'Create', self._create_folder_slot)

    def _add_app_slot(self, entry: AppEntry) -> None:
        slots = self.config.home_slots
        if len(slots) >= 8:
            self._show_status('Home is full — remove a slot first.')
            return
        display = self.config.label_for(entry.app_id, entry.name)
        slots.append({'type': 'app', 'label': display, 'app_id': entry.app_id,
                      'cmd': None, 'folder': None})
        self.config.set_home_slots(slots)
        self.stack.set_visible_child_name('home')
        self._home_launcher.refresh()
        self._populate_apps(self.apps_search.get_text())
        self._show_status(f'{display} added to home.')

    def _create_folder_slot(self, name: str) -> None:
        slots = self.config.home_slots
        if len(slots) >= 8:
            self._show_status('Home is full — remove a slot first.')
            return
        slots.append({'type': 'folder', 'label': name, 'app_id': None, 'cmd': None, 'folder': []})
        self.config.set_home_slots(slots)
        self._home_launcher.refresh()
        self._home_launcher.set_edit_mode(True)

    def _move_slot(self, idx: int, delta: int) -> None:
        slots = self.config.home_slots
        new_idx = idx + delta
        if not (0 <= idx < len(slots) and 0 <= new_idx < len(slots)):
            return
        slots[idx], slots[new_idx] = slots[new_idx], slots[idx]
        self.config.set_home_slots(slots)
        self._home_launcher.refresh(preserve_folder=True)

    def _remove_slot(self, idx: int) -> None:
        slots = self.config.home_slots
        if not (0 <= idx < len(slots)):
            return
        removed = slots.pop(idx)
        self.config.set_home_slots(slots)
        self._home_launcher.refresh(preserve_folder=True)
        self._populate_apps(self.apps_search.get_text())
        self._show_status(f'{removed.get("label", "Slot")} removed from home.')

    def _rename_slot(self, widget: Gtk.Widget, idx: int) -> None:
        slots = self.config.home_slots
        if not (0 <= idx < len(slots)):
            return

        def _commit(text: str) -> None:
            slots[idx]['label'] = text
            app_id = slots[idx].get('app_id')
            if slots[idx].get('type') == 'app' and app_id:
                self.config.set_app_label(str(app_id), text)
            self.config.set_home_slots(slots)
            self._refresh_after_item_action()

        self.item_actions._show_entry_dialog(
            widget, 'Rename', slots[idx].get('label', ''), 'Rename', _commit)

    def _make_drawer_folder_row(self, slot: dict, slot_index: int) -> Gtk.ListBoxRow:
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label=slot.get('label', ''), xalign=0)
        title.add_css_class('app-name')
        indicator = Gtk.Label(label='⌄' if slot_index == self._drawer_open_folder else '›')
        indicator.add_css_class('dim-label')
        inner.append(title)
        inner.append(indicator)
        inner.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.set_child(inner)
        btn.connect('clicked', lambda _b, i=slot_index: self._toggle_drawer_folder(i))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _make_drawer_member_row(self, slot_index: int, member_index: int, member: dict) -> Gtk.ListBoxRow:
        label = member.get('label', '')
        paused = self.focus_state.is_paused_app(str(member.get('app_id') or ''))
        title = Gtk.Label(label=label + (' · paused' if paused else ''), xalign=0)
        title.add_css_class('app-name')
        title.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.add_css_class('folder-member')
        if paused:
            btn.add_css_class('paused')
        btn.set_child(title)

        def _launch(_b: Gtk.Button) -> None:
            self._drawer_open_folder = None
            self._populate_apps(self.apps_search.get_text())
            self._launch_slot({'type': 'app', **member})

        btn.connect('clicked', _launch)
        self._add_long_press(
            btn, lambda w: self.item_actions.show_member_menu(w, slot_index, member_index))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _toggle_drawer_folder(self, slot_index: int) -> None:
        opening = self._drawer_open_folder != slot_index
        self._drawer_open_folder = slot_index if opening else None
        self._populate_apps(self.apps_search.get_text())
        if opening:
            GLib.idle_add(self._center_drawer_folder)

    def _center_drawer_folder(self) -> bool:
        # Scroll so the folder row plus its drop-down sit vertically centered
        header = self._drawer_folder_header
        if header is None or self._drawer_open_folder is None:
            return GLib.SOURCE_REMOVE
        row_h = header.get_height()
        if row_h <= 0:
            return GLib.SOURCE_CONTINUE
        # Size from rendered members only: uninstalled members are skipped
        # at render, so counting the raw list would overshoot the block.
        members = next((m for i, _s, m in self._drawer_folder_slots()
                        if i == self._drawer_open_folder), [])
        block_h = row_h * (1 + len(members))
        view_h = self.apps_scroller.get_height()
        alloc = header.get_allocation()
        block_h = min(block_h, view_h)
        target = alloc.y - max(0, (view_h - block_h) // 2)
        adj = self.apps_scroller.get_vadjustment()
        adj.set_value(max(0.0, float(target)))
        return GLib.SOURCE_REMOVE

    def _add_long_press(self, widget: Gtk.Widget, callback) -> None:
        def _on_long_press(gesture: Gtk.GestureLongPress, _x: float, _y: float) -> None:
            # Claim the sequence so releasing doesn't also fire the row's click
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            callback(widget)

        long_press = Gtk.GestureLongPress.new()
        long_press.set_touch_only(False)
        long_press.connect('pressed', _on_long_press)
        widget.add_controller(long_press)
        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)
        right_click.connect('pressed', lambda _g, _n, _x, _y, w=widget: callback(w))
        widget.add_controller(right_click)

    def _make_settings_row(self) -> Gtk.ListBoxRow:
        title = Gtk.Label(label='Launcher Settings', xalign=0)
        title.add_css_class('app-name')
        title.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.set_child(title)
        btn.connect('clicked', lambda _b: self.stack.set_visible_child_name('settings'))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _replace_rows(
        self,
        list_box: Gtk.ListBox,
        entries: list[AppEntry],
        empty_message: str,
        row_maker: object = None,
    ) -> None:
        child = list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            list_box.remove(child)
            child = next_child

        if not entries:
            label = Gtk.Label(label=empty_message, xalign=0)
            label.add_css_class('dim-label')
            row = Gtk.ListBoxRow(selectable=False, activatable=False)
            row.set_child(label)
            list_box.append(row)
            return

        maker = row_maker if callable(row_maker) else self._make_app_row
        for entry in entries:
            list_box.append(maker(entry))

    def _on_key_pressed(self, _ctrl: Gtk.EventControllerKey, keyval: int, *_) -> bool:
        self._reset_idle_timer()
        # Power button → force redraw to wake display if HWComposer idled
        if keyval in (Gdk.KEY_PowerOff, 0x1008ff2a, 0x1008ff18):
            self.queue_draw()
            return True
        return False

    def _setup_config_monitor(self) -> None:
        """Hot reload: editing ~/.config/xx-wm/* in a terminal is a
        first-class workflow (design.md "Settings scope"). Debounced; invalid
        files keep the last good config."""
        from gi.repository import Gio
        self._config_reload_timer: int | None = None
        self._config_monitors = []
        gestures_path = self.config.config_dir / 'gestures.json'
        for path in (self.config.config_path, gestures_path):
            try:
                monitor = Gio.File.new_for_path(str(path)).monitor_file(
                    Gio.FileMonitorFlags.NONE, None)
                monitor.connect('changed', self._on_config_file_changed)
                self._config_monitors.append(monitor)
            except GLib.Error:
                pass

    def _on_config_file_changed(self, _monitor, _file, _other, event) -> None:
        from gi.repository import Gio
        if event not in (Gio.FileMonitorEvent.CHANGED,
                         Gio.FileMonitorEvent.CHANGES_DONE_HINT,
                         Gio.FileMonitorEvent.CREATED,
                         Gio.FileMonitorEvent.RENAMED):
            return
        if self._config_reload_timer is not None:
            GLib.source_remove(self._config_reload_timer)
        self._config_reload_timer = GLib.timeout_add(300, self._reload_config)

    def _reload_config(self) -> bool:
        self._config_reload_timer = None
        import json
        loaded: dict | None = None
        try:
            if self.config.config_path.exists():
                loaded = json.loads(self.config.config_path.read_text(encoding='utf-8'))
                if not isinstance(loaded, dict):
                    raise ValueError('config root must be an object')
        except (OSError, json.JSONDecodeError, ValueError) as error:
            from shell_log import get_logger
            get_logger('window').warning('config reload skipped: %s', error)
            return GLib.SOURCE_REMOVE
        # Gestures re-read is cheap and side-effect free
        self.gesture_config = GestureConfig()
        # Skip the UI rebuild when the change was our own save
        if loaded is not None and loaded == self.config.data:
            return GLib.SOURCE_REMOVE
        self.config.data = dict(DEFAULT_CONFIG)
        self.config.load()
        self._apply_config_change()
        return GLib.SOURCE_REMOVE

    def _apply_config_change(self) -> None:
        """Re-apply everything a config edit can influence."""
        self._apply_theme()
        self._build_widget_block()
        self._home_launcher.refresh()
        self._populate_apps(self.apps_search.get_text())
        self._refresh_weather()
        self._setup_idle_timer()
        self._retheme_surfaces()

    def _retheme_surfaces(self) -> None:
        """Fan the freshly resolved preset out to every constructed overlay
        surface. They draw outside .shell-root with their own display-level
        providers, so a hot-reloaded theme edit must reach each one; fonts
        already propagate display-wide via font_theme's global provider."""
        preset = resolve_theme(self.config)
        for attr in ('_shade', '_dialer', '_call_ui', '_call_bar',
                     '_power_menu', '_switcher'):
            surface = getattr(self, attr, None)
            if surface is not None:
                surface.apply_theme(preset)
        lock = getattr(self, '_lock_screen', None)
        if lock is not None:
            lock.apply_theme(preset)
        back = getattr(self, '_back_layer', None)
        if back is not None:
            back.apply_theme(preset)
        app = self.get_application() if callable(
            getattr(self, 'get_application', None)) else None
        if app is not None:
            hud = getattr(app, '_hud', None)
            if hud is not None:
                hud.apply_theme(preset)
            app_menu = getattr(app, '_power_menu', None)
            if app_menu is not None and app_menu is not getattr(
                    self, '_power_menu', None):
                app_menu.apply_theme(preset)

    def _setup_idle_timer(self) -> None:
        if self._idle_timer_id is not None:
            GLib.source_remove(self._idle_timer_id)
            self._idle_timer_id = None
        timeout = self.config.auto_lock_timeout
        if timeout > 0:
            self._idle_timer_id = GLib.timeout_add_seconds(timeout, self._on_idle_timeout)

    def _reset_idle_timer(self) -> None:
        self._setup_idle_timer()

    def _on_idle_timeout(self) -> bool:
        self._idle_timer_id = None
        self._show_lock_screen()
        return False

    def _on_apn_save(self, _btn: Gtk.Button) -> None:
        apn = self.apn_entry.get_text().strip()
        user = self.apn_user_entry.get_text().strip()
        pwd = self.apn_pass_entry.get_text()
        self.config.data['apn'] = apn
        self.config.data['apn_user'] = user
        self.config.data['apn_pass'] = pwd
        self.config.save()
        self._apply_apn(apn, user, pwd)
        self._show_status('APN saved.')

    @staticmethod
    def _gsm_connection_name() -> str:
        import subprocess
        try:
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show'],
                capture_output=True, text=True, timeout=5, check=True,
            )
            for line in result.stdout.splitlines():
                # Terse mode escapes ':' inside names as '\:'; TYPE is last
                name, _, ctype = line.rpartition(':')
                if ctype.strip() == 'gsm' and name:
                    return name.replace('\\:', ':')
        except (OSError, subprocess.SubprocessError) as error:
            from shell_log import get_logger
            get_logger('window').warning(
                'GSM connection lookup failed (%s); using default name', error)
        return 'mobile'

    def _apply_apn(self, apn: str, user: str, pwd: str) -> None:
        if not apn:
            return
        try:
            import subprocess
            conn = self._gsm_connection_name()
            subprocess.Popen(
                ['nmcli', 'connection', 'modify', conn,
                 'gsm.apn', apn, 'gsm.username', user, 'gsm.password', pwd],
                close_fds=True,
            )
        except Exception as error:
            from shell_log import get_logger
            get_logger('window').warning('APN update failed: %s', error)

    def _on_call_incoming(self, caller: str, number: str) -> None:
        from call_ui import CallUI, CallBar
        import sound
        if self._call_ui is None:
            self._call_ui = CallUI(on_accept=sound.stop, on_decline=sound.stop,
                                   config=self.config)
            self._call_ui.set_application(self.get_application())
            # Same current-preset rule as _open_dialer.
            self._call_ui.apply_theme(resolve_theme(self.config))
        if self._call_bar is None:
            self._call_bar = CallBar(on_expand=self._expand_call_ui,
                                     config=self.config)
            self._call_bar.set_application(self.get_application())
            self._call_bar.apply_theme(resolve_theme(self.config))
        # DnD: only exceptions (starred, repeat caller) ring; everyone else
        # shows silently in the call UI. Exception check runs before this
        # call is recorded so the 15-minute repeat window looks at prior calls.
        rings = not self.dnd_state.is_active() or self.dnd_state.is_exception(number)
        self.dnd_state.note_call(number)
        if rings and self.config.sound_ringtone:
            sound.play('ringtone.mp3', loop=True)
        self._call_ui.show_incoming(caller or 'Incoming call', number)

    def _on_call_answered(self, caller: str, number: str) -> None:
        import sound
        sound.stop()
        if self._call_ui is None:
            return
        self._call_ui.show_active(caller or 'Active call', number)
        if self._call_bar is not None:
            self._call_bar.show_bar(caller or number)

    def _on_call_ended(self) -> None:
        import sound
        sound.stop()
        if self._call_ui is not None:
            self._call_ui.end_call()
        if self._call_bar is not None:
            self._call_bar.hide_bar()

    def _expand_call_ui(self) -> None:
        # CallBar's expand button: re-present the full call surface
        if self._call_ui is not None:
            self._call_ui.present()

    def _make_app_row(self, entry: AppEntry) -> Gtk.ListBoxRow:
        display_name = self.config.label_for(entry.app_id, entry.name)
        paused = self.focus_state.is_paused_app(entry.app_id)
        if paused:
            display_name += ' · paused'
        # Single-line centered rows, name only — matches the PiercingXX
        # Android launcher's drawer; descriptions were noise at phone size
        title = Gtk.Label(label=display_name, xalign=0.5)
        title.add_css_class('app-name')
        title.set_ellipsize(Pango.EllipsizeMode.END)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(title)
        content.set_hexpand(True)

        launch_btn = Gtk.Button()
        launch_btn.add_css_class('flat')
        launch_btn.add_css_class('app-entry')
        if paused:
            launch_btn.add_css_class('paused')
        launch_btn.set_child(content)
        launch_btn.connect('clicked', lambda _b, e=entry: self._launch_entry(e))
        self._add_long_press(
            launch_btn, lambda w, e=entry: self.item_actions.show_app_menu(w, e))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(launch_btn)
        return row

    def _launch_entry(self, entry: AppEntry) -> None:
        if self._pick_slot_mode:
            self._pick_slot_mode = False
            self._add_app_slot(entry)
            return
        if self._pick_gesture_key:
            key = self._pick_gesture_key
            self._pick_gesture_key = None
            self.gesture_config.set(key, f'launch:{entry.app_id}')
            self._refresh_gesture_labels()
            self.stack.set_visible_child_name('settings')
            name = self.config.label_for(entry.app_id, entry.name)
            self._show_status(f'{name} bound to {_GESTURE_TITLES[key]}.')
            return
        if self.focus_state.is_paused_app(entry.app_id):
            self._show_focus_notice(self.config.label_for(entry.app_id, entry.name))
            return
        ok, error = self.app_index.launch(entry)
        if ok:
            self.config.record_launch(entry.app_id)
            self._show_status(f'Launching {entry.name}...')
            self.drop_to_background()
        else:
            self._show_status(f'Failed to launch {entry.name}: {error}')

    def _on_apps_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text()
        self._populate_apps(text)
        # Auto-launch the single result while typing, when enabled
        query = text.strip()
        if (self.config.search_auto_launch and query and not query.startswith('!')
                and len(self._last_search_results) == 1):
            entry.set_text('')
            self._launch_entry(self._last_search_results[0])

    def _on_apps_search_activate(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text().strip()
        if not query:
            return
        # `!query` → web search; a query with zero app matches falls back too
        if query.startswith('!'):
            self._open_web_search(query[1:].strip())
            entry.set_text('')
            return
        results = self._last_search_results
        if results:
            entry.set_text('')
            self._launch_entry(results[0])
        else:
            self._open_web_search(query)
            entry.set_text('')

    def _open_web_search(self, query: str) -> None:
        if not query:
            return
        from gi.repository import Gio
        from urllib.parse import quote_plus
        try:
            Gio.AppInfo.launch_default_for_uri(_WEB_SEARCH_URL + quote_plus(query), None)
            self._show_status(f'Searching the web for {query}...')
        except GLib.Error as error:
            self._show_status(f'No browser available: {error.message}')

    def _on_update_clicked(self, _btn: Gtk.Button | None = None) -> None:
        from update_checker import run_update_script
        ok, detail = run_update_script(self.config.update_script)
        if ok:
            self._show_status('Update running in terminal.')
        else:
            self._show_status(detail)

    def _on_check_updates_clicked(self, _btn: Gtk.Button) -> None:
        self._show_status('Checking for updates...')
        self._update_checker.check_now(self._on_manual_check_result)

    def _on_manual_check_result(self, count: int) -> None:
        if count > 0:
            self._on_updates_available(count)
        else:
            self._show_status('System is up to date.')

    def _on_updates_available(self, count: int) -> None:
        plural = 's' if count != 1 else ''
        self._ensure_shade().add_notification(
            _UPDATE_NOTIF_ID,
            'System',
            'Updates available',
            f'{count} package{plural} can be updated.',
            actions=[
                ('Update now', self._on_update_clicked),
                ('Snooze 1 week', self._snooze_updates),
            ],
        )
        self._show_status(f'{count} update{plural} available — see notifications.')

    def _snooze_updates(self) -> None:
        self._update_checker.snooze()
        self._show_status('Update checks snoozed for 1 week.')

    def _show_status(self, message: str) -> None:
        self.status_label.set_text(message)
        self.toast_overlay.add_toast(Adw.Toast.new(message))
    def _on_export_backup(self) -> None:
        from backup import export_backup
        import json
        from datetime import datetime
        from pathlib import Path

        backup = export_backup(self.config)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'xx-wm-backup-{timestamp}.json'
        filepath = Path.home() / filename
        
        filepath.write_text(json.dumps(backup, indent=2), encoding='utf-8')
        self._show_status(f'Backup exported to {filepath}')
    
    def _build_restore_dialog(self) -> Gtk.FileChooserDialog:
        from gi.repository import Gtk as gtk

        # Create a file chooser dialog
        dialog = gtk.FileChooserDialog(
            title='Restore from backup',
            parent=self,
            action=gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            '_Cancel',
            gtk.ResponseType.CANCEL,
            '_Restore',
            gtk.ResponseType.OK,
        )

        filter_json = gtk.FileFilter()
        filter_json.set_name('JSON files')
        filter_json.add_pattern('*.json')
        dialog.add_filter(filter_json)
        return dialog

    def _on_restore_backup(self) -> None:
        from gi.repository import Gtk as gtk

        # dialog.run() is gone in GTK4; the response signal carries on
        dialog = self._build_restore_dialog()

        def _on_response(dlg: Gtk.FileChooserDialog, response: int) -> None:
            filepath = dlg.get_filename()
            dlg.destroy()
            if response != gtk.ResponseType.OK:
                return
            self._restore_from_path(filepath)

        dialog.connect('response', _on_response)
        dialog.show()

    def _restore_from_path(self, filepath: str | None) -> None:
        from backup import restore_backup, validate_backup
        import json

        if not filepath:
            self._show_status('No backup file selected.')
            return

        try:
            payload = json.loads(Path(filepath).read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as e:
            self._show_status(f'Failed to read backup: {e}')
            return

        is_valid, error = validate_backup(payload)
        if not is_valid:
            self._show_status(f'Invalid backup: {error}')
            return

        success = restore_backup(self.config, payload)
        if success:
            self._show_status('Backup restored successfully')
            self._apply_config_change()
        else:
            self._show_status('Failed to restore backup')
    
    def _get_device_name(self) -> str:
        import platform
        try:
            # Try to read device model from sysfs (common on Linux phones)
            with open('/sys/devices/virtual/dmi/id/product_name', 'r') as f:
                return f.read().strip()
        except Exception:
            pass
        
        try:
            with open('/sys/devices/virtual/dmi/id/sys_vendor', 'r') as f:
                vendor = f.read().strip()
            with open('/sys/devices/virtual/dmi/id/product_version', 'r') as f:
                version = f.read().strip()
            return f'{vendor} {version}'
        except Exception:
            pass
        
        # Fallback to platform info
        return f'{platform.system()} {platform.release()}'

