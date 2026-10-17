"""Launcher settings section: the Linux counterpart of the Android
launcher's preferences screen (home, widgets, weather, clock, drawer,
gestures, appearance), all backed by config.json with hot reload.
"""
from __future__ import annotations

import settings_page

WIDGET_ORDER = ('time', 'date', 'weather', 'battery')

_WIDGET_TITLES = {
    'time': 'Time widget',
    'date': 'Date widget',
    'weather': 'Weather widget',
    'battery': 'Battery widget',
}

_TAP_LABELS = {
    'default': 'Default',
    'none': 'None',
    'refresh': 'Refresh',
}

_TEMP_CHOICES = (('fahrenheit', '°F'), ('celsius', '°C'))
_CLOCK_CHOICES = ((None, 'System'), (False, '12 h'), (True, '24 h'))
_SORT_CHOICES = (('default', 'Pinned first'), ('az', 'A–Z'),
                 ('install', 'Install date'))
_ALIGN_CHOICES = (('left', 'Left'), ('center', 'Center'), ('right', 'Right'))
_MODE_CHOICES = (('theme', 'Theme'), ('light', 'Light'), ('dark', 'Dark'),
                 ('system', 'System'))
_FONT_ORDER = ('system-light', 'monospace', 'space-mono', 'jetbrains-mono',
               'jetbrains-mono-nerd', 'custom')
_FONT_LABELS = {
    'system-light': 'System light',
    'monospace': 'Monospace',
    'space-mono': 'Space Mono',
    'jetbrains-mono': 'JetBrains Mono',
    'jetbrains-mono-nerd': 'JetBrains Mono Nerd',
    'custom': 'Custom',
}

_DOUBLE_TAP_TITLE = 'Double-tap home'
_LONG_PRESS_TITLE = 'Long-press home'


def _config_get(win, key: str, default):
    try:
        get = getattr(win.config, 'get', None)
        if callable(get):
            return get(key, default)
        return win.config.data.get(key, default)
    except Exception:
        return default


def _config_set(win, key: str, value) -> None:
    try:
        win.config.set(key, value)
    except Exception:
        pass


def _tap_text(win, key: str) -> str:
    try:
        tap = win.config.widgets.get(key, {}).get('tap', 'default')
    except Exception:
        return 'Default'
    if isinstance(tap, dict) and tap.get('app'):
        app_id = str(tap['app'])
        try:
            for entry in win.app_index.entries:
                if entry.app_id == app_id:
                    return win.config.label_for(app_id, entry.name)
        except Exception:
            pass
        return app_id
    return _TAP_LABELS.get(tap, 'Default')


def _binding_text(win, key: str) -> str:
    try:
        action = win.gesture_config.get(key) or 'none'
    except Exception:
        return 'Not set'
    if action == 'none':
        return 'Not set'
    if action == 'edit':
        return 'Edit mode'
    if action.startswith('launch:'):
        app_id = action[len('launch:'):]
        try:
            for entry in win.app_index.entries:
                if entry.app_id == app_id:
                    return win.config.label_for(app_id, entry.name)
        except Exception:
            pass
        return app_id
    return action.replace('_', ' ').title()


def _font_key(win) -> str:
    try:
        key = str(win.config.data.get('font', 'jetbrains-mono-nerd'))
    except Exception:
        return 'jetbrains-mono-nerd'
    return key if key in _FONT_LABELS else 'jetbrains-mono-nerd'


def _font_label(win) -> str:
    key = _font_key(win)
    if key == 'custom':
        try:
            family = win.config.data.get('custom_font_family')
            if family:
                return str(family)
        except Exception:
            pass
    return _FONT_LABELS[key]


def tail_log_lines(path: str, max_lines: int = 400) -> str:
    """Tail of a log file (default 400 lines). Reads from the end so a
    multi-megabyte shell.log never loads fully; missing files read empty."""
    try:
        with open(path, 'rb') as handle:
            handle.seek(0, 2)
            pos = handle.tell()
            data = b''
            while pos > 0 and data.count(b'\n') <= max_lines:
                step = min(8192, pos)
                pos -= step
                handle.seek(pos)
                data = handle.read(step) + data
    except OSError:
        return ''
    lines = data.decode('utf-8', errors='replace').splitlines(keepends=True)
    return ''.join(lines[-max_lines:]) if len(lines) > max_lines else ''.join(lines)


def open_logs(win) -> None:
    """Settings → About → Logs: a page with the shell.log tail and Copy."""
    from pathlib import Path

    from gi.repository import Gdk, Gtk
    from xx_ui import widgets

    log_path = Path.home() / '.local' / 'share' / 'xx-wm' / 'shell.log'
    text = tail_log_lines(str(log_path))

    dialog = Gtk.Window(title='Logs')
    try:
        dialog.set_transient_for(win)
        dialog.set_modal(True)
    except Exception:
        pass
    dialog.set_decorated(False)
    dialog.set_default_size(360, 720)

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    root.set_margin_top(16)
    root.set_margin_bottom(24)
    root.set_margin_start(20)
    root.set_margin_end(20)

    def _copy() -> None:
        try:
            buf = view.get_buffer()
            copied = buf.get_text(buf.get_start_iter(), buf.get_end_iter(),
                                  False)
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set(copied)
        except Exception:
            pass

    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    title = widgets.label('Logs', 'xx-title')
    title.set_hexpand(True)
    header.append(title)
    header.append(widgets.text_button('Copy', _copy))
    root.append(header)
    root.append(widgets.wrap_label(
        'Last 400 lines of shell.log. Nothing here leaves the phone.'))

    view = Gtk.TextView()
    view.set_editable(False)
    view.set_cursor_visible(False)
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    view.set_monospace(True)
    view.get_buffer().set_text(text or 'No log yet.')
    scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_child(view)
    root.append(scroll)

    dialog.set_child(root)
    try:
        dialog.present()
    except Exception:
        pass


def reset_home_layout(config, gesture_config) -> None:
    """Clear slots, renames and swipe bindings, then reseed the default
    layout. Pure UI helper: re-render stays with the caller."""
    config.set_home_slots([])
    config.clear_app_labels()
    for key in ('swipe_left_home', 'swipe_right_home'):
        try:
            gesture_config.reset(key)
        except Exception:
            pass
    config.set_default_layout_applied(False)
    from default_layout import apply_default_layout
    apply_default_layout(config, gesture_config)


def _install_font(win, path: str):
    from config import install_custom_font
    try:
        return install_custom_font(win.config, path)
    except Exception as error:
        return (False, str(error))


class LauncherSection(settings_page._Section):
    key = 'launcher'
    title = 'Launcher'

    def build(self, win) -> list:
        from gi.repository import Gtk

        win._launcher_binding_labels = {}
        win._launcher_widgets_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win._launcher_gestures_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win._launcher_font_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8)
        win._launcher_sort_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8)
        win._launcher_mode_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._render_widgets(win)
        self._render_gesture_rows(win)
        self._render_font(win)
        self._render_sort(win)
        self._render_mode(win)
        return [
            settings_page._section_title(self.title),
            self._build_home_card(win),
            self._build_widgets_card(win),
            self._build_weather_card(win),
            self._build_clock_card(win),
            self._build_drawer_card(win),
            self._build_gestures_card(win),
            self._build_appearance_card(win),
        ]

    def refresh(self, win) -> None:
        self._sync_switches(win)
        self._render_widgets(win)
        self._render_gesture_rows(win)
        self._render_font(win)
        self._render_sort(win)
        self._render_mode(win)

    # -- shared ---------------------------------------------------------

    def _sync_switches(self, win) -> None:
        win._launcher_updating = True
        try:
            for attr, value in (
                    ('_launcher_auto_loc', _config_get(win, 'weather_auto_location', True)),
                    ('_launcher_auto_launch', bool(getattr(
                        win.config, 'search_auto_launch', False))),
                    ('_launcher_recents', _config_get(win, 'home_to_recents', False))):
                switch = getattr(win, attr, None)
                if switch is not None:
                    try:
                        switch.set_active(bool(value))
                    except Exception:
                        pass
        finally:
            win._launcher_updating = False

    # -- Home -----------------------------------------------------------

    def _build_home_card(self, win):
        from xx_ui import widgets

        card = settings_page._section_card()
        card.append(widgets.caption('HOME'))
        current = getattr(win.config, 'home_alignment', 'center')
        card.append(widgets.segmented([
            (label, value == current,
             lambda value=value: self._set_alignment(win, value))
            for value, label in _ALIGN_CHOICES]))
        card.append(settings_page._outline_action(
            'Reset home layout…', lambda: self._confirm_reset(win)))
        return card

    def _set_alignment(self, win, value: str) -> None:
        try:
            win.config.set_home_alignment(value)
        except Exception:
            return
        try:
            win._apply_config_change()
        except Exception:
            pass

    def _confirm_reset(self, win) -> None:
        from gi.repository import Adw

        dialog = Adw.AlertDialog(
            heading='Reset home layout?',
            body=('Clears home slots, renames and swipe bindings, '
                  'then reseeds the default layout.'))
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('reset', 'Reset')
        dialog.set_response_appearance(
            'reset', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def _on_response(_dialog, response: str) -> None:
            win._launcher_dialog = None
            if response != 'reset':
                return
            try:
                reset_home_layout(win.config, win.gesture_config)
            except Exception:
                return
            try:
                win._refresh_after_item_action()
            except Exception:
                pass
            try:
                win._show_status('Home layout reset.')
            except Exception:
                pass

        dialog.connect('response', _on_response)
        win._launcher_dialog = dialog
        try:
            dialog.present(win)
        except Exception:
            win._launcher_dialog = None

    # -- Widgets --------------------------------------------------------

    def _build_widgets_card(self, win):
        from xx_ui import widgets

        card = settings_page._section_card()
        card.append(widgets.caption('WIDGETS'))
        card.append(win._launcher_widgets_box)
        return card

    def _render_widgets(self, win) -> None:
        from gi.repository import Gtk
        from xx_ui import widgets

        box = getattr(win, '_launcher_widgets_box', None)
        if box is None:
            return
        try:
            configs = win.config.widgets
        except Exception:
            configs = {}
        ordered = sorted(configs, key=lambda k: configs[k].get('order', 99))
        rows = []
        for key in ordered:
            conf = configs[key]
            enabled = bool(conf.get('enabled', True))
            switch = widgets.make_switch(enabled)
            switch.connect(
                'notify::active',
                lambda sw, _p, k=key: self._on_widget_flipped(win, k, sw))
            title = _WIDGET_TITLES.get(key, key)
            rows.append(widgets.setting_row(title, _tap_text(win, key), switch))
            controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=8)
            up = widgets.text_button(
                '↑', lambda k=key: self._move_widget(win, k, -1))
            down = widgets.text_button(
                '↓', lambda k=key: self._move_widget(win, k, +1))
            tap = widgets.text_button('On tap…')
            controls.append(up)
            controls.append(down)
            controls.append(tap)
            tap.connect('clicked',
                        lambda _b, k=key, b=tap: self._pick_tap(win, b, k))
            rows.append(controls)
        settings_page._replace_box_children(box, rows)

    def _on_widget_flipped(self, win, key: str, switch) -> None:
        if getattr(win, '_launcher_updating', False):
            return
        try:
            win.config.set_widget_enabled(key, bool(switch.get_active()))
            win._build_widget_block()
        except Exception:
            pass
        self._render_widgets(win)

    def _move_widget(self, win, key: str, delta: int) -> None:
        try:
            win.config.move_widget(key, delta)
            win._build_widget_block()
        except Exception:
            pass
        self._render_widgets(win)

    def _pick_tap(self, win, anchor, key: str) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        choices = [
            ('Default', lambda: self._set_tap(win, key, 'default')),
            ('None', lambda: self._set_tap(win, key, 'none')),
            ('Refresh', lambda: self._set_tap(win, key, 'refresh')),
            ('Choose app…', lambda: self._pick_tap_app(win, key)),
        ]
        actions._show_action_menu(anchor, _WIDGET_TITLES.get(key, key),
                                  choices)

    def _set_tap(self, win, key: str, tap) -> None:
        try:
            win.config.set_widget_tap(key, tap)
        except Exception:
            pass
        self._render_widgets(win)

    def _pick_tap_app(self, win, key: str) -> None:
        try:
            win._pick_widget_tap = key
            win.stack.set_visible_child_name('apps')
            win._show_status('Tap an app to open on widget tap.')
        except Exception:
            pass

    # -- Weather --------------------------------------------------------

    def _build_weather_card(self, win):
        from xx_ui import widgets

        card = settings_page._section_card()
        card.append(widgets.caption('WEATHER'))
        current = _config_get(win, 'weather_temp_unit', 'fahrenheit')
        card.append(widgets.segmented([
            (label, value == current,
             lambda value=value: self._set_temp_unit(win, value))
            for value, label in _TEMP_CHOICES]))
        auto = widgets.make_switch(
            bool(_config_get(win, 'weather_auto_location', True)))
        auto.connect('notify::active',
                     lambda sw, _p: self._on_auto_loc(win, sw))
        win._launcher_auto_loc = auto
        card.append(widgets.setting_row('Auto location', '', auto))
        try:
            lat = win.config.data.get('weather_lat')
            lon = win.config.data.get('weather_lon')
        except Exception:
            lat, lon = None, None
        win._launcher_lat = settings_page._settings_entry(
            win, 'Latitude', '' if lat is None else str(lat))
        win._launcher_lon = settings_page._settings_entry(
            win, 'Longitude', '' if lon is None else str(lon))
        card.append(settings_page._settings_row(
            'Manual location', win._launcher_lat))
        card.append(win._launcher_lon)
        card.append(widgets.primary_button(
            'Save location', lambda: self._save_location(win)))
        return card

    def _set_temp_unit(self, win, value: str) -> None:
        _config_set(win, 'weather_temp_unit', value)
        try:
            win._refresh_weather()
        except Exception:
            pass

    def _on_auto_loc(self, win, switch) -> None:
        if getattr(win, '_launcher_updating', False):
            return
        _config_set(win, 'weather_auto_location', bool(switch.get_active()))
        try:
            win._refresh_weather(force=True)
        except Exception:
            pass

    def _save_location(self, win) -> None:
        def _parse(entry) -> float | None:
            try:
                text = entry.get_text().strip()
            except Exception:
                return None
            if not text:
                return None
            try:
                return float(text)
            except (TypeError, ValueError):
                return None

        lat = _parse(getattr(win, '_launcher_lat', None))
        lon = _parse(getattr(win, '_launcher_lon', None))
        if (lat is None) != (lon is None):
            try:
                win._show_status('Set both latitude and longitude.')
            except Exception:
                pass
            return
        _config_set(win, 'weather_lat', lat)
        _config_set(win, 'weather_lon', lon)
        try:
            win._refresh_weather(force=True)
            win._show_status('Location saved.' if lat is not None
                             else 'Location cleared.')
        except Exception:
            pass

    # -- Clock ----------------------------------------------------------

    def _build_clock_card(self, win):
        from xx_ui import widgets

        card = settings_page._section_card()
        card.append(widgets.caption('CLOCK'))
        current = _config_get(win, 'clock_24h', None)
        card.append(widgets.segmented([
            (label, value == current,
             lambda value=value: self._set_clock(win, value))
            for value, label in _CLOCK_CHOICES]))
        return card

    def _set_clock(self, win, value) -> None:
        _config_set(win, 'clock_24h', value)
        try:
            win._refresh_clock()
        except Exception:
            pass

    # -- Drawer ---------------------------------------------------------

    def _build_drawer_card(self, win):
        from xx_ui import widgets

        card = settings_page._section_card()
        card.append(widgets.caption('DRAWER'))
        # Three long values cannot fit one segmented row on a 320 px
        # content, so the default sort stays a value row opening the same
        # menu (the standby timeout idiom).
        card.append(win._launcher_sort_box)
        auto = widgets.make_switch(bool(getattr(
            win.config, 'search_auto_launch', False)))
        auto.connect('notify::active',
                     lambda sw, _p: self._on_auto_launch(win, sw))
        win._launcher_auto_launch = auto
        card.append(widgets.setting_row(
            'Search auto-launch', '', auto))
        card.append(settings_page._section_placeholder(
            'Type a space first to stop it.'))
        return card

    def _sort_label(self, win) -> str:
        current = _config_get(win, 'drawer_sort', 'default')
        for value, label in _SORT_CHOICES:
            if value == current:
                return label
        return _SORT_CHOICES[0][1]

    def _render_sort(self, win) -> None:
        from xx_ui import widgets

        box = getattr(win, '_launcher_sort_box', None)
        if box is None:
            return
        row = widgets.list_row('Default sort', self._sort_label(win),
                               trailing=widgets.chevron(), last=True)
        row.connect('clicked',
                    lambda _b, r=row: self._pick_sort(win, r))
        settings_page._replace_box_children(box, [row])

    def _pick_sort(self, win, anchor) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        choices = [(label, lambda value=value: self._set_sort(win, value))
                   for value, label in _SORT_CHOICES]
        actions._show_action_menu(anchor, 'Default sort', choices)

    def _set_sort(self, win, value: str) -> None:
        _config_set(win, 'drawer_sort', value)
        self._render_sort(win)
        try:
            win._populate_apps(win.apps_search.get_text())
        except Exception:
            pass

    def _on_auto_launch(self, win, switch) -> None:
        if getattr(win, '_launcher_updating', False):
            return
        try:
            win.config.set_search_auto_launch(bool(switch.get_active()))
        except Exception:
            pass

    # -- Gestures -------------------------------------------------------

    def _build_gestures_card(self, win):
        from xx_ui import widgets

        card = settings_page._section_card()
        card.append(widgets.caption('GESTURES'))
        card.append(win._launcher_gestures_box)
        recents = widgets.make_switch(
            bool(_config_get(win, 'home_to_recents', False)))
        recents.connect('notify::active',
                        lambda sw, _p: self._on_recents(win, sw))
        win._launcher_recents = recents
        card.append(widgets.setting_row(
            'Home opens recents', 'When already home', recents))
        return card

    def _render_gesture_rows(self, win) -> None:
        from gi.repository import Gtk
        from xx_ui import widgets

        box = getattr(win, '_launcher_gestures_box', None)
        if box is None:
            return
        rows = []
        for key, title in (('double_tap_home', _DOUBLE_TAP_TITLE),
                           ('long_press_home', _LONG_PRESS_TITLE)):
            label = widgets.label(_binding_text(win, key), 'xx-secondary')
            label.set_hexpand(True)
            win._launcher_binding_labels[key] = label
            change = widgets.text_button(
                'Change',
                lambda k=key, t=title: self._pick_home_gesture(win, k, t))
            default = widgets.text_button(
                'Default', lambda k=key: self._reset_home_gesture(win, k))
            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                              spacing=8)
            buttons.append(change)
            buttons.append(default)
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            row.add_css_class('xx-setting')
            row.append(widgets.label(title, 'xx-title'))
            row.append(label)
            row.append(buttons)
            rows.append(row)
        settings_page._replace_box_children(box, rows)

    def _pick_home_gesture(self, win, key: str, title: str) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        from gesture_config import VALID_ACTIONS
        if key == 'long_press_home':
            choices = [
                ('Edit mode', lambda: self._set_home_gesture(win, key, 'edit')),
                ('Settings', lambda: self._set_home_gesture(win, key, 'settings')),
                ('None', lambda: self._set_home_gesture(win, key, 'none')),
            ]
        else:
            choices = [
                (action.replace('_', ' ').title(),
                 lambda action=action: self._set_home_gesture(win, key, action))
                for action in sorted(VALID_ACTIONS)]
        choices.append(('Choose app…',
                        lambda: self._pick_home_gesture_app(win, key, title)))
        actions._show_action_menu(win._launcher_gestures_box, title, choices)

    def _set_home_gesture(self, win, key: str, action: str) -> None:
        try:
            win.gesture_config.set(key, action)
        except (ValueError, AttributeError):
            return
        self._render_gesture_rows(win)

    def _reset_home_gesture(self, win, key: str) -> None:
        try:
            win.gesture_config.reset(key)
        except AttributeError:
            return
        self._render_gesture_rows(win)

    def _pick_home_gesture_app(self, win, key: str, title: str) -> None:
        try:
            win._pick_gesture_key = key
            win.stack.set_visible_child_name('apps')
            win._show_status(f'Tap an app to bind to {title}.')
        except Exception:
            pass

    def _on_recents(self, win, switch) -> None:
        if getattr(win, '_launcher_updating', False):
            return
        _config_set(win, 'home_to_recents', bool(switch.get_active()))

    # -- Appearance -----------------------------------------------------

    def _build_appearance_card(self, win):
        from xx_ui import widgets

        card = settings_page._section_card()
        card.append(widgets.caption('APPEARANCE'))
        # Four values cannot fit one segmented row on a 320 px content,
        # so the mode stays a value row opening the same menu.
        card.append(win._launcher_mode_box)
        card.append(win._launcher_font_box)
        card.append(settings_page._outline_action(
            'Import font…', lambda: self._import_font(win)))
        scale_row = widgets.setting_row('Text size', '', None)
        card.append(scale_row)
        scale = self._build_scale(win)
        card.append(scale)
        return card

    def _mode_label(self, win) -> str:
        try:
            mode = win.config.appearance_mode
        except AttributeError:
            mode = 'theme'
        for value, label in _MODE_CHOICES:
            if value == mode:
                return label
        return _MODE_CHOICES[0][1]

    def _render_mode(self, win) -> None:
        from xx_ui import widgets

        box = getattr(win, '_launcher_mode_box', None)
        if box is None:
            return
        row = widgets.list_row('Mode', self._mode_label(win),
                               trailing=widgets.chevron(), last=True)
        row.connect('clicked',
                    lambda _b, r=row: self._pick_mode(win, r))
        settings_page._replace_box_children(box, [row])

    def _pick_mode(self, win, anchor) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        choices = [(label, lambda value=value: self._set_mode(win, value))
                   for value, label in _MODE_CHOICES]
        actions._show_action_menu(anchor, 'Mode', choices)

    def _set_mode(self, win, value: str) -> None:
        try:
            win.config.set_appearance_mode(value)
        except Exception:
            return
        self._render_mode(win)
        try:
            win._apply_config_change()
        except Exception:
            pass

    def _build_scale(self, win):
        from gi.repository import Gtk
        from xx_ui import widgets

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        adjustment = Gtk.Adjustment(
            value=float(getattr(win.config, 'text_size_scale', 1.0) or 1.0),
            lower=0.5, upper=2.0, step_increment=0.05, page_increment=0.1)
        slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                           adjustment=adjustment)
        slider.set_digits(0)
        slider.set_draw_value(False)
        slider.set_hexpand(True)
        slider.set_size_request(0, 44)
        percent = widgets.label(
            '%d%%' % round(adjustment.get_value() * 100), 'xx-title')
        win._launcher_scale_label = percent
        slider.connect('value-changed',
                       lambda _s: self._on_scale(win, slider, percent))
        box.append(slider)
        box.append(percent)
        win._launcher_scale = slider
        return box

    def _on_scale(self, win, slider, percent) -> None:
        try:
            value = round(float(slider.get_value()), 2)
        except Exception:
            return
        try:
            current = win.config.text_size_scale
        except Exception:
            current = 1.0
        if abs(value - current) < 0.005:
            return
        try:
            win.config.set_text_size_scale(value)
        except Exception:
            return
        try:
            percent.set_text('%d%%' % round(value * 100))
        except Exception:
            pass
        try:
            win._apply_theme()
        except Exception:
            pass

    def _render_font(self, win) -> None:
        from xx_ui import widgets

        box = getattr(win, '_launcher_font_box', None)
        if box is None:
            return
        row = widgets.list_row('Font', _font_label(win),
                               trailing=widgets.chevron(), last=True)
        row.connect('clicked',
                    lambda _b, r=row: self._pick_font(win, r))
        settings_page._replace_box_children(box, [row])

    def _pick_font(self, win, anchor) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        current = _font_key(win)
        choices = [
            (_FONT_LABELS[key] + (' ✓' if key == current else ''),
             lambda key=key: self._set_font(win, key))
            for key in _FONT_ORDER]
        actions._show_action_menu(anchor, 'Font', choices)

    def _set_font(self, win, key: str) -> None:
        try:
            win.config.set_font(key)
        except Exception:
            return
        try:
            win._apply_theme()
        except Exception:
            pass
        self._render_font(win)

    def _import_font(self, win) -> None:
        from gi.repository import Gio, Gtk

        dialog = Gtk.FileDialog(title='Import font')
        fonts = Gtk.FileFilter()
        fonts.set_name('Font files')
        fonts.add_pattern('*.ttf')
        fonts.add_pattern('*.otf')
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(fonts)
        dialog.set_filters(store)
        dialog.set_default_filter(fonts)

        def _done(_dialog, result) -> None:
            try:
                chosen = dialog.open_finish(result)
            except Exception:
                return
            if chosen is None:
                return
            try:
                path = chosen.get_path()
            except Exception:
                return
            if not path:
                return
            settings_page._run_bg(
                lambda: _install_font(win, path),
                lambda done: self._on_font_installed(win, done))

        try:
            dialog.open(win, None, _done)
        except Exception:
            pass

    def _on_font_installed(self, win, done) -> None:
        ok, message = done if isinstance(done, tuple) else (False, 'Import failed.')
        try:
            win._show_status(
                f'Font installed: {message}' if ok
                else f'Font import failed: {message}')
        except Exception:
            pass
        if not ok:
            return
        try:
            win._apply_theme()
        except Exception:
            pass
        self._render_font(win)
