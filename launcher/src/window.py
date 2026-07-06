from __future__ import annotations

import gi
from datetime import datetime
from pathlib import Path

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

from gi.repository import Adw, Gdk, GLib, Gtk

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

from app_index import AppEntry, AppIndex
from config import FONT_FAMILIES, THEME_PRESETS, ShellConfig
from gesture_config import ACTION_LABELS, GESTURE_LABELS, GestureConfig, VALID_ACTIONS
from system_status import status_line

_PAGE_ORDER = ['home', 'apps', 'settings']


class ShellWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title='PiercingOS')
        self.add_css_class('piercing-shell')

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.BOTTOM)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            LayerShell.set_exclusive_zone(self, -1)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        self.config = ShellConfig()
        self.gesture_config = GestureConfig()
        self.app_index = AppIndex()
        self.app_index.refresh()

        self._edit_mode = False
        self._idle_timer_id: int | None = None
        self._call_ui: object | None = None
        self._call_bar: object | None = None
        self._shade: object | None = None
        self._switcher: object | None = None
        self._dialer: object | None = None
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

        self.home_rows: list[Gtk.Widget] = []
        self.drawer_rows: list[Gtk.Widget] = []

        self.stack = Gtk.Stack(
            hexpand=True,
            vexpand=True,
            transition_duration=200,
            transition_type=Gtk.StackTransitionType.NONE,
        )

        self.home_search = Gtk.SearchEntry(placeholder_text='Search or launch')
        self.home_search.connect('activate', self._on_home_search_activate)
        self.home_search.connect('search-changed', self._on_home_search_changed)

        self.apps_search = Gtk.SearchEntry(placeholder_text='Filter apps')
        self.apps_search.connect('search-changed', self._on_apps_search_changed)
        self.apps_search.connect('activate', self._on_apps_search_activate)

        self.theme_dropdown = Gtk.DropDown.new_from_strings([preset.name for preset in THEME_PRESETS.values()])
        self.theme_dropdown.connect('notify::selected', self._on_theme_changed)

        self.font_dropdown = Gtk.DropDown.new_from_strings(['Sans Light', 'Space Mono', 'JetBrains Mono'])
        self.font_dropdown.connect('notify::selected', self._on_font_changed)

        self.dark_mode_switch = Gtk.Switch(active=self.config.prefer_dark)
        self.dark_mode_switch.connect('notify::active', self._on_dark_mode_toggled)

        _size_labels = ['XS (70%)', 'S (85%)', 'Normal', 'L (115%)', 'XL (130%)']
        _size_values = [0.7, 0.85, 1.0, 1.15, 1.3]
        self._size_values = _size_values
        self.size_dropdown = Gtk.DropDown.new_from_strings(_size_labels)
        cur_scale = self.config.text_size_scale
        closest = min(range(len(_size_values)), key=lambda i: abs(_size_values[i] - cur_scale))
        self.size_dropdown.set_selected(closest)
        self.size_dropdown.connect('notify::selected', self._on_size_changed)

        _align_labels = ['Left', 'Center', 'Right']
        _align_values = ['left', 'center', 'right']
        self._align_values = _align_values
        self.align_dropdown = Gtk.DropDown.new_from_strings(_align_labels)
        cur_align = self.config.home_alignment
        self.align_dropdown.set_selected(_align_values.index(cur_align) if cur_align in _align_values else 0)
        self.align_dropdown.connect('notify::selected', self._on_align_changed)

        _lock_labels = ['Never', '30 sec', '1 min', '2 min', '5 min', '10 min']
        _lock_seconds = [0, 30, 60, 120, 300, 600]
        self._lock_seconds = _lock_seconds
        self.auto_lock_dropdown = Gtk.DropDown.new_from_strings(_lock_labels)
        cur_timeout = self.config.auto_lock_timeout
        idx = _lock_seconds.index(cur_timeout) if cur_timeout in _lock_seconds else 3
        self.auto_lock_dropdown.set_selected(idx)
        self.auto_lock_dropdown.connect('notify::selected', self._on_auto_lock_changed)

        self.status_label = Gtk.Label(xalign=0)
        self.status_label.add_css_class('dim-label')

        self.app_count_label = Gtk.Label(xalign=0)
        self.app_count_label.add_css_class('dim-label')

        self.clock_label = Gtk.Label(xalign=0)
        self.clock_label.add_css_class('display-clock')

        self.date_label = Gtk.Label(xalign=0)
        self.date_label.add_css_class('display-date')

        self.status_strip = Gtk.Label(xalign=0)
        self.status_strip.add_css_class('dim-label')

        self.home_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.home_list.add_css_class('text-list')

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

        self._sync_controls_from_config()
        self._apply_theme()
        self._refresh_clock()
        self._populate_home()
        self._populate_apps()
        self._refresh_status()
        self._setup_idle_timer()
        GLib.timeout_add_seconds(1, self._tick_clock)
        GLib.timeout_add_seconds(60, self._tick_status)

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
        self._swipe_navigated = False

        root.append(self.stack)
        return root

    def _on_stack_swipe(self, _gesture: Gtk.GestureSwipe, vel_x: float, vel_y: float) -> None:
        # Vertical swipes: shade (down) or switcher (up)
        if abs(vel_y) > abs(vel_x) * 1.5:
            self._swipe_navigated = True
            if vel_y > 300:
                self._show_shade()
            elif vel_y < -400:
                self._show_switcher()
            return
        if abs(vel_y) > abs(vel_x):
            return
        current = self.stack.get_visible_child_name()
        idx = _PAGE_ORDER.index(current) if current in _PAGE_ORDER else 0
        if vel_x < -200 and idx < len(_PAGE_ORDER) - 1:
            self._swipe_navigated = True
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
            self.stack.set_visible_child_name(_PAGE_ORDER[idx + 1])
        elif vel_x > 200 and idx > 0:
            self._swipe_navigated = True
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.stack.set_visible_child_name(_PAGE_ORDER[idx - 1])

    def _on_stack_swipe_drag_end(
        self, _gesture: Gtk.GestureSwipe, offset_x: float, offset_y: float
    ) -> None:
        # Called before `swipe` fires — defer snap-back check one idle tick
        GLib.idle_add(self._maybe_snap_back, offset_x, offset_y)

    def _maybe_snap_back(self, offset_x: float, offset_y: float) -> bool:
        navigated = self._swipe_navigated
        self._swipe_navigated = False
        if navigated:
            return False
        # Horizontal drag that didn't become navigation → pulse opacity as feedback
        if abs(offset_x) > 18 and abs(offset_x) > abs(offset_y):
            self.stack.set_opacity(0.72)
            GLib.timeout_add(160, self._restore_stack_opacity)
        return False

    def _restore_stack_opacity(self) -> bool:
        self.stack.set_opacity(1.0)
        return False

    def _build_home_page(self) -> Gtk.Widget:
        from home_launcher import HomeLauncher, _HOME_CSS
        from gi.repository import Gdk

        css = Gtk.CssProvider()
        css.load_from_data(_HOME_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 3,
        )

        # --- Top 1/3: clock / date / status — vertically centered ---
        self.clock_label.set_xalign(0.5)
        self.date_label.set_xalign(0.5)
        self.status_strip.set_xalign(0.5)

        # Double-tap clock → lock screen
        double_tap = Gtk.GestureClick.new()
        double_tap.connect('pressed', self._on_clock_tapped)
        self._clock_tap_count = 0
        self._clock_tap_timer: int | None = None
        self.clock_label.add_controller(double_tap)

        clock_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        clock_inner.set_halign(Gtk.Align.FILL)
        clock_inner.set_hexpand(True)
        clock_inner.append(self.status_strip)
        clock_inner.append(self.clock_label)
        clock_inner.append(self.date_label)

        # Equal spacers above and below clock_inner → vertically centered in top pane
        top_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top_pane.set_hexpand(True)
        top_pane.set_vexpand(True)
        sp_top = Gtk.Box(); sp_top.set_vexpand(True)
        sp_bot = Gtk.Box(); sp_bot.set_vexpand(True)
        top_pane.append(sp_top)
        top_pane.append(clock_inner)
        top_pane.append(sp_bot)

        # --- Bottom 2/3: launcher — vertically centered ---
        self._home_launcher = HomeLauncher(open_dialer_fn=self._open_dialer)

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

        # --- Paned: top=1/3, bottom=2/3, ratio maintained on resize ---
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_hexpand(True)
        paned.set_vexpand(True)
        paned.set_wide_handle(False)
        paned.set_resize_start_child(False)
        paned.set_resize_end_child(True)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)
        paned.set_start_child(top_pane)
        paned.set_end_child(bot_pane)

        # Set divider at 1/3 once the widget is realized and allocated.
        # GTK4 removed size-allocate as a connectable signal; use realize + idle_add.
        def _set_ratio_once(w: Gtk.Paned) -> None:
            def _apply() -> bool:
                h = w.get_height()
                if h > 0:
                    w.set_position(h // 3)
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE   # retry next idle
            GLib.idle_add(_apply)
        paned.connect('realize', _set_ratio_once)

        return paned

    def _open_dialer(self) -> None:
        from dialer import Dialer
        if self._dialer and self._dialer.get_visible():
            return
        d = Dialer()
        d.set_application(self.get_application())
        d.present()
        self._dialer = d

    def _handle_back(self) -> None:
        """Called by BackGestureLayer on edge swipe from either side."""
        import subprocess, os
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
        env = {**os.environ, 'WAYLAND_DISPLAY': 'wayland-0',
               'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}'}
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

    def _on_clock_tapped(self, _gesture: Gtk.GestureClick, n_press: int, _x: float, _y: float) -> None:
        if n_press == 2:
            self._show_lock_screen()

    def _show_lock_screen(self) -> None:
        lock = getattr(self, '_lock_screen', None)
        if lock is not None and lock.get_visible():
            lock.present()
            return
        from lock_screen import LockScreen
        self._lock_screen = LockScreen(on_unlock=self._dismiss_lock_screen)
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

    def _show_shade(self) -> None:
        if self._shade is None:
            from notification_shade import NotificationShade
            self._shade = NotificationShade()
            self._shade.set_application(self.get_application())
        self._shade.show_shade()

    def _show_switcher(self) -> None:
        if self._switcher is None:
            from app_switcher import AppSwitcher
            self._switcher = AppSwitcher()
            self._switcher.set_application(self.get_application())
        self._switcher.show_switcher()

    def _build_apps_page(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_margin_top(36)
        outer.set_margin_bottom(24)
        outer.set_margin_start(24)
        outer.set_margin_end(0)

        self._sort_by_usage = False

        title = Gtk.Label(label='All apps', xalign=0, hexpand=True)
        title.add_css_class('section-title')

        self._usage_sort_btn = Gtk.Button(label='A-Z')
        self._usage_sort_btn.add_css_class('flat')
        self._usage_sort_btn.add_css_class('action-link')
        self._usage_sort_btn.connect('clicked', self._toggle_usage_sort)

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_row.set_margin_end(24)
        header_row.append(title)
        header_row.append(self._usage_sort_btn)

        self.apps_search.set_margin_end(24)
        self.app_count_label.set_margin_end(24)

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

        outer.append(header_row)
        outer.append(self.apps_search)
        outer.append(self.app_count_label)
        outer.append(list_row)
        return outer

    def _toggle_usage_sort(self, _btn: Gtk.Button) -> None:
        self._sort_by_usage = not self._sort_by_usage
        self._usage_sort_btn.set_label('Usage' if self._sort_by_usage else 'A-Z')
        self._populate_apps(self.apps_search.get_text())

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

    def _build_settings_page(self) -> Gtk.Widget:
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

        # Appearance
        appearance = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        appearance.add_css_class('settings-card')
        appearance.append(self._settings_row('Theme preset', self.theme_dropdown))
        appearance.append(self._settings_row('Font family', self.font_dropdown))
        appearance.append(self._settings_row('Text size', self.size_dropdown))
        appearance.append(self._settings_row('Home alignment', self.align_dropdown))
        appearance.append(self._settings_row('Prefer dark surfaces', self.dark_mode_switch))
        appearance.append(self._settings_row('Auto-lock', self.auto_lock_dropdown))

        # App index
        index_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        index_card.add_css_class('settings-card')

        top_apps_button = Gtk.Button(label='Use top 8 apps on home')
        top_apps_button.add_css_class('flat')
        top_apps_button.add_css_class('action-link')
        top_apps_button.connect('clicked', self._use_top_apps_for_home)

        refresh_button = Gtk.Button(label='Rebuild application index')
        refresh_button.add_css_class('flat')
        refresh_button.add_css_class('action-link')
        refresh_button.connect('clicked', self._refresh_index)

        index_card.append(top_apps_button)
        index_card.append(refresh_button)
        index_card.append(self.status_label)

        # Gesture editor
        gesture_title = Gtk.Label(label='Gestures', xalign=0)
        gesture_title.add_css_class('section-title')

        gesture_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        gesture_card.add_css_class('settings-card')

        for gesture_key, current_action in self.gesture_config.all():
            gesture_card.append(self._gesture_row(gesture_key, current_action))

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

        # Hidden apps management
        hidden_title = Gtk.Label(label='Hidden apps', xalign=0)
        hidden_title.add_css_class('section-title')

        hidden_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hidden_card.add_css_class('settings-card')
        self._hidden_list_box = hidden_card
        self._refresh_hidden_list()

        box.append(title)
        box.append(appearance)
        box.append(index_card)
        box.append(hidden_title)
        box.append(hidden_card)
        box.append(network_title)
        box.append(apn_card)
        box.append(gesture_title)
        box.append(gesture_card)

        scroll.set_child(box)
        return scroll

    def _gesture_row(self, gesture_key: str, current_action: str) -> Gtk.Widget:
        label = Gtk.Label(label=GESTURE_LABELS.get(gesture_key, gesture_key), xalign=0)
        label.set_hexpand(True)
        label.add_css_class('dim-label')

        action_keys = sorted(VALID_ACTIONS)
        action_labels = [ACTION_LABELS.get(a, a) for a in action_keys]
        dropdown = Gtk.DropDown.new_from_strings(action_labels)
        if current_action in action_keys:
            dropdown.set_selected(action_keys.index(current_action))

        dropdown.connect(
            'notify::selected',
            lambda dd, _p, gk=gesture_key, keys=action_keys: self._on_gesture_changed(dd, gk, keys),
        )

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.append(label)
        row.append(dropdown)
        return row

    def _on_gesture_changed(self, dropdown: Gtk.DropDown, gesture_key: str, action_keys: list[str]) -> None:
        action = action_keys[dropdown.get_selected()]
        try:
            self.gesture_config.set(gesture_key, action)
        except ValueError:
            pass

    def _settings_row(self, title: str, control: Gtk.Widget) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        label = Gtk.Label(label=title, xalign=0)
        label.set_hexpand(True)
        row.append(label)
        row.append(control)
        return row

    def _sync_controls_from_config(self) -> None:
        theme_keys = list(THEME_PRESETS)
        font_keys = list(FONT_FAMILIES)
        self.theme_dropdown.set_selected(theme_keys.index(self.config.theme.key))
        self.font_dropdown.set_selected(font_keys.index(next(key for key, value in FONT_FAMILIES.items() if value == self.config.font_family)))
        self.dark_mode_switch.set_active(self.config.prefer_dark)

    def _apply_theme(self) -> None:
        theme = self.config.theme
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
        """
        self.theme_provider.load_from_data(css.encode('utf-8'))

    def _refresh_clock(self) -> None:
        now = datetime.now()
        self.clock_label.set_text(now.strftime('%H:%M'))
        self.date_label.set_text(now.strftime('%A, %d %b').replace(' 0', ' '))

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
        self._populate_home()
        self._populate_apps(self.apps_search.get_text())
        self._show_status('Application index rebuilt.')

    def _use_top_apps_for_home(self, _button: Gtk.Button) -> None:
        top_ids = [entry.app_id for entry in self.app_index.top(8)]
        self.config.set_pinned(top_ids)
        self._populate_home()
        self._show_status('Pinned home list reset to the first 8 visible apps.')

    def _populate_home(self) -> None:
        pinned = self.app_index.resolve(self.config.pinned)
        if not pinned:
            pinned = self.app_index.top(8)
        self._replace_rows(self.home_list, pinned, 'No launchable apps were indexed.', self._make_home_row)

    def _populate_apps(self, query: str = '') -> None:
        sort_usage = getattr(self, '_sort_by_usage', False)
        results = self.app_index.search(
            query,
            sort_by_usage=sort_usage,
            launch_counts=self.config.launch_counts if sort_usage else None,
        )
        hidden = set(self.config.hidden_apps)
        results = [e for e in results if e.app_id not in hidden]

        # Build letter→first-row-index map for A-Z jump strip
        self._alpha_letter_rows = {}
        for idx, entry in enumerate(results):
            first = entry.name[0].upper() if entry.name else '#'
            letter = first if first.isalpha() else '#'
            if letter not in self._alpha_letter_rows:
                self._alpha_letter_rows[letter] = idx

        self._replace_rows(self.apps_list, results, 'No apps matched this search.', self._make_app_row)
        if query.strip():
            self.app_count_label.set_text(f'{len(results)} matches')
        else:
            self.app_count_label.set_text(f'{len(results)} apps indexed')

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

    def _make_home_row(self, entry: AppEntry) -> Gtk.ListBoxRow:
        alignment_map = {'left': 0.0, 'center': 0.5, 'right': 1.0}
        xalign = alignment_map.get(self.config.home_alignment, 0.0)

        title = Gtk.Label(label=entry.name, xalign=xalign)
        title.add_css_class('app-name')

        subtitle = Gtk.Label(label=entry.description or entry.app_id, xalign=xalign, wrap=True)
        subtitle.add_css_class('app-subtitle')
        subtitle.add_css_class('dim-label')

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.append(title)
        content.append(subtitle)
        content.set_hexpand(True)

        launch_btn = Gtk.Button()
        launch_btn.add_css_class('flat')
        launch_btn.add_css_class('app-entry')
        launch_btn.set_child(content)
        launch_btn.connect('clicked', lambda _b, e=entry: self._launch_entry(e))

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        outer.append(launch_btn)

        if self._edit_mode:
            unpin_btn = Gtk.Button(label='×')
            unpin_btn.add_css_class('flat')
            unpin_btn.add_css_class('dim-label')
            unpin_btn.set_valign(Gtk.Align.CENTER)
            unpin_btn.connect('clicked', lambda _b, eid=entry.app_id: self._unpin_app(eid))
            outer.append(unpin_btn)

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(outer)
        return row

    def _toggle_edit_mode(self, _btn: Gtk.Button) -> None:
        self._edit_mode = not self._edit_mode
        self.edit_btn.set_label('Done' if self._edit_mode else 'Edit')
        self._populate_home()

    def _pin_app(self, app_id: str) -> None:
        pinned = list(self.config.pinned)
        if app_id not in pinned:
            pinned.append(app_id)
            self.config.set_pinned(pinned)
            self._populate_home()
            self._show_status('Pinned to home.')

    def _unpin_app(self, app_id: str) -> None:
        pinned = [p for p in self.config.pinned if p != app_id]
        self.config.set_pinned(pinned)
        self._populate_home()

    def _hide_app(self, app_id: str) -> None:
        hidden = list(self.config.hidden_apps)
        if app_id not in hidden:
            hidden.append(app_id)
            self.config.set_hidden_apps(hidden)
        self._populate_apps(self.apps_search.get_text())
        self._refresh_hidden_list()
        self._show_status('App hidden from drawer.')

    def _unhide_app(self, app_id: str) -> None:
        hidden = [h for h in self.config.hidden_apps if h != app_id]
        self.config.set_hidden_apps(hidden)
        self._populate_apps(self.apps_search.get_text())
        self._refresh_hidden_list()

    def _refresh_hidden_list(self) -> None:
        if not hasattr(self, '_hidden_list_box'):
            return
        box: Gtk.Box = self._hidden_list_box
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt
        by_id = {e.app_id: e for e in self.app_index.entries}
        for app_id in self.config.hidden_apps:
            entry = by_id.get(app_id)
            name = entry.name if entry else app_id
            lbl = Gtk.Label(label=name, xalign=0, hexpand=True)
            lbl.add_css_class('dim-label')
            show_btn = Gtk.Button(label='Show')
            show_btn.add_css_class('flat')
            show_btn.add_css_class('action-link')
            show_btn.connect('clicked', lambda _b, eid=app_id: self._unhide_app(eid))
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(lbl)
            row.append(show_btn)
            box.append(row)
        if not self.config.hidden_apps:
            placeholder = Gtk.Label(label='No apps hidden.', xalign=0)
            placeholder.add_css_class('dim-label')
            box.append(placeholder)

    def _on_key_pressed(self, _ctrl: Gtk.EventControllerKey, keyval: int, *_) -> bool:
        self._reset_idle_timer()
        # Power button → force redraw to wake display if HWComposer idled
        if keyval in (Gdk.KEY_PowerOff, 0x1008ff2a, 0x1008ff18):
            self.queue_draw()
            return True
        return False

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

    def _on_auto_lock_changed(self, dropdown: Gtk.DropDown, _p: object) -> None:
        seconds = self._lock_seconds[dropdown.get_selected()]
        self.config.set_auto_lock_timeout(seconds)
        self._setup_idle_timer()

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

    def _apply_apn(self, apn: str, user: str, pwd: str) -> None:
        if not apn:
            return
        try:
            from gi.repository import Gio, GLib
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            # Get first active GSM connection from NetworkManager and update APN
            result = bus.call_sync(
                'org.freedesktop.NetworkManager',
                '/org/freedesktop/NetworkManager',
                'org.freedesktop.NetworkManager',
                'GetAllDevices',
                None, None, Gio.DBusCallFlags.NONE, 2000, None,
            )
            # NetworkManager APN update is complex; write to /etc/ModemManager-gsm.conf fallback
            import subprocess
            subprocess.Popen(
                ['nmcli', 'connection', 'modify', 'mobile',
                 'gsm.apn', apn, 'gsm.username', user, 'gsm.password', pwd],
                close_fds=True,
            )
        except Exception:
            pass

    def _on_call_incoming(self, caller: str, number: str) -> None:
        from call_ui import CallUI, CallBar
        if self._call_ui is None:
            self._call_ui = CallUI()
            self._call_ui.set_application(self.get_application())
        if self._call_bar is None:
            self._call_bar = CallBar()
            self._call_bar.set_application(self.get_application())
        self._call_ui.show_incoming(caller or 'Incoming call', number)

    def _on_call_answered(self, caller: str, number: str) -> None:
        if self._call_ui is None:
            return
        self._call_ui.show_active(caller or 'Active call', number)
        if self._call_bar is not None:
            self._call_bar.show_bar(caller or number)

    def _on_call_ended(self) -> None:
        if self._call_ui is not None:
            self._call_ui.end_call()
        if self._call_bar is not None:
            self._call_bar.hide_bar()

    def _make_app_row(self, entry: AppEntry) -> Gtk.ListBoxRow:
        title = Gtk.Label(label=entry.name, xalign=0)
        title.add_css_class('app-name')

        subtitle = Gtk.Label(label=entry.description or entry.app_id, xalign=0, wrap=True)
        subtitle.add_css_class('app-subtitle')
        subtitle.add_css_class('dim-label')

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.append(title)
        content.append(subtitle)
        content.set_hexpand(True)

        launch_btn = Gtk.Button()
        launch_btn.add_css_class('flat')
        launch_btn.add_css_class('app-entry')
        launch_btn.set_child(content)
        launch_btn.connect('clicked', lambda _b, e=entry: self._launch_entry(e))

        is_pinned = entry.app_id in self.config.pinned
        pin_btn = Gtk.Button(label='−' if is_pinned else '+')
        pin_btn.add_css_class('flat')
        pin_btn.add_css_class('dim-label')
        pin_btn.set_valign(Gtk.Align.CENTER)
        if is_pinned:
            pin_btn.connect('clicked', lambda _b, eid=entry.app_id: self._unpin_app(eid))
        else:
            pin_btn.connect('clicked', lambda _b, eid=entry.app_id: self._pin_app(eid))

        hide_btn = Gtk.Button(label='⊘')
        hide_btn.add_css_class('flat')
        hide_btn.add_css_class('dim-label')
        hide_btn.set_valign(Gtk.Align.CENTER)
        hide_btn.set_tooltip_text('Hide from drawer')
        hide_btn.connect('clicked', lambda _b, eid=entry.app_id: self._hide_app(eid))

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        outer.append(launch_btn)
        outer.append(pin_btn)
        outer.append(hide_btn)

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(outer)
        return row

    def _launch_entry(self, entry: AppEntry) -> None:
        ok, error = self.app_index.launch(entry)
        if ok:
            self.config.record_launch(entry.app_id)
            self._show_status(f'Launching {entry.name}...')
        else:
            self._show_status(f'Failed to launch {entry.name}: {error}')

    def _on_home_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text().strip()
        if not text:
            return
        self._populate_apps(text)

    def _on_home_search_activate(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text().strip()
        if not query:
            return

        results = self.app_index.search(query)
        if len(results) == 1 or (results and results[0].name.casefold() == query.casefold()):
            self._launch_entry(results[0])
            return

        self.apps_search.set_text(query)
        self._populate_apps(query)
        self.stack.set_visible_child_name('apps')

    def _on_apps_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._populate_apps(entry.get_text())

    def _on_apps_search_activate(self, entry: Gtk.SearchEntry) -> None:
        results = self.app_index.search(entry.get_text())
        if results:
            self._launch_entry(results[0])

    def _on_theme_changed(self, dropdown: Gtk.DropDown, _paramspec: object) -> None:
        theme_key = list(THEME_PRESETS)[dropdown.get_selected()]
        self.config.set_theme(theme_key)
        self._apply_theme()
        self._show_status(f'Theme set to {THEME_PRESETS[theme_key].name}.')

    def _on_font_changed(self, dropdown: Gtk.DropDown, _paramspec: object) -> None:
        font_key = list(FONT_FAMILIES)[dropdown.get_selected()]
        self.config.set_font(font_key)
        self._apply_theme()
        self._show_status('Font family updated.')

    def _on_dark_mode_toggled(self, switch: Gtk.Switch, _paramspec: object) -> None:
        self.config.set_prefer_dark(switch.get_active())
        self._apply_theme()
        self._show_status('Surface mode updated.')

    def _on_size_changed(self, dropdown: Gtk.DropDown, _p: object) -> None:
        scale = self._size_values[dropdown.get_selected()]
        self.config.set_text_size_scale(scale)
        self._apply_theme()
        self._show_status('Text size updated.')

    def _on_align_changed(self, dropdown: Gtk.DropDown, _p: object) -> None:
        alignment = self._align_values[dropdown.get_selected()]
        self.config.set_home_alignment(alignment)
        self._populate_home()
        self._show_status('Home alignment updated.')

    def _show_status(self, message: str) -> None:
        self.status_label.set_text(message)
        self.toast_overlay.add_toast(Adw.Toast.new(message))
