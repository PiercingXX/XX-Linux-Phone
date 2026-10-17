"""Launcher settings section: the Linux counterpart of the Android
launcher's preferences screen (home, widgets, weather, clock, drawer,
gestures, appearance), all backed by config.json with hot reload.
"""
from __future__ import annotations

import settings_page
import settings_ui as ui

WIDGET_ORDER = ('time', 'date', 'weather', 'battery')

_WIDGET_TITLES = {
    'time': 'Time',
    'date': 'Date',
    'weather': 'Weather',
    'battery': 'Battery',
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


def location_text(win) -> str:
    """Manual weather location row value: 'Not set' or 'lat, lon'."""
    try:
        lat = win.config.data.get('weather_lat')
        lon = win.config.data.get('weather_lon')
    except Exception:
        return 'Not set'
    if lat is None or lon is None:
        return 'Not set'
    return '%s, %s' % (lat, lon)


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


def _visible_text(view) -> str:
    try:
        buf = view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    except Exception:
        return ''


def share_log_text(text: str) -> tuple[bool, str]:
    """Write `text` to a temp file and open it with `xx-share --send`."""
    import os
    import shutil
    import subprocess
    import tempfile
    if shutil.which('xx-share') is None:
        return False, 'Share is not installed on this device.'
    try:
        fd, path = tempfile.mkstemp(prefix='xxwm-log-', suffix='.txt')
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text or '')
    except OSError as err:
        return False, 'Could not stage the log: %s' % err
    try:
        subprocess.Popen(['xx-share', '--send', path], close_fds=True)
        return True, 'Opening Share…'
    except OSError as err:
        return False, 'Could not open Share: %s' % err


def open_logs(win) -> None:
    """Settings → About → Logs: shell tail plus journal/kernel/system
    tabs, with Copy and Share (via xx-share)."""
    from pathlib import Path

    from gi.repository import Gdk, Gtk
    from xx_ui import widgets

    import settings_logs
    from settings_page import _run_bg

    log_path = Path.home() / '.local' / 'share' / 'xx-wm' / 'shell.log'

    dialog = Gtk.Window(title='Logs')
    try:
        dialog.set_transient_for(win)
        dialog.set_modal(True)
    except Exception:
        pass
    dialog.set_decorated(False)
    dialog.set_default_size(360, 720)

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    root.add_css_class('logs-root')
    root.set_margin_top(16)
    root.set_margin_bottom(24)
    root.set_margin_start(20)
    root.set_margin_end(20)

    state = {'tab': 'shell'}

    view = Gtk.TextView()
    view.set_editable(False)
    view.set_cursor_visible(False)
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    view.set_monospace(True)

    status = widgets.wrap_label(
        'Capped output. PINs and passwords are redacted.')

    def _show(text: str) -> None:
        try:
            view.get_buffer().set_text(text or 'No log yet.')
        except Exception:
            pass

    def _fetch(tab: str) -> None:
        state['tab'] = tab
        _show('Loading…')
        if tab == 'shell':
            _run_bg(
                lambda: settings_logs.scrub_secrets(settings_logs.cap_text(
                    tail_log_lines(str(log_path)))),
                _show)
            return
        helper_name = settings_logs.helper_log_name(tab)
        if helper_name is not None:
            _fetch_helper_log(helper_name)
            return

        def _work():
            return settings_logs.fetch_journal(tab)

        def _done(text) -> None:
            if (tab == 'kernel' and isinstance(text, str)
                    and text.startswith('dmesg restricted')):
                _show(text + '\nRetrying as a service…')
                _fetch_helper_log('dmesg')
                return
            _show(text if isinstance(text, str) else 'Log unavailable.')

        _run_bg(_work, _done)

    def _fetch_helper_log(name: str) -> None:
        try:
            import helper_client
        except ImportError:
            _show('Helper client unavailable.')
            return

        def _done(ok: bool, result) -> None:
            if not ok:
                _show('System log unavailable (%s).' % result)
                return
            _show(settings_logs.scrub_secrets(
                settings_logs.cap_text(str(result or 'No entries.'))))

        helper_client.read_log(name, _done)

    def _copy() -> None:
        try:
            copied = _visible_text(view)
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set(copied)
                status.set_text('Copied.')
        except Exception:
            pass

    def _share() -> None:
        ok, message = share_log_text(_visible_text(view))
        try:
            status.set_text(message)
        except Exception:
            pass
        _ = ok

    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    title = widgets.label('Logs', 'xx-title')
    title.set_hexpand(True)
    header.append(title)
    header.append(widgets.text_button('Copy', _copy))
    header.append(widgets.text_button('Share', _share))
    root.append(header)
    tabs = widgets.segmented([
        (label, key == state['tab'],
         lambda key=key: _fetch(key))
        for key, label in settings_logs.TABS])
    tab_scroll = Gtk.ScrolledWindow(hexpand=True)
    tab_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                          Gtk.PolicyType.NEVER)
    tab_scroll.set_child(tabs)
    root.append(tab_scroll)
    root.append(status)

    scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_child(view)
    root.append(scroll)

    dialog.set_child(root)
    _fetch('shell')
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


def _choice_label(choices, value, fallback: str) -> str:
    for key, label in choices:
        if key == value:
            return label
    return fallback


def _scale_row(title: str, value_text: str, slider):
    """Card row: title and live value over a full-width slider."""
    from gi.repository import Gtk
    from xx_ui import widgets

    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    name = widgets.label(title, 'xx-title')
    name.set_hexpand(True)
    value = Gtk.Label(label=value_text, xalign=1.0)
    value.add_css_class('xx-secondary')
    head.append(name)
    head.append(value)
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    column.append(head)
    column.append(slider)
    row = ui.wide_row(column)
    row.value_label = value
    return row


def build_appearance_group(win) -> list:
    """Mode, font and text size: launcher prefs shown on the Display &
    appearance page under the theme swatches."""
    return LauncherSection().build_appearance(win)


class LauncherSection(settings_page._Section):
    key = 'launcher'
    title = 'Home screen'

    def build(self, win) -> list:
        win._launcher_binding_labels = {}
        win._launcher_updating = False
        home = ui.group(self.title, [
            ui.nav_row('Alignment', '',
                       lambda r: self._pick_choice(
                           win, r, 'Alignment', _ALIGN_CHOICES,
                           lambda v: self._set_alignment(win, v))),
            ui.nav_row('Clock', '',
                       lambda r: self._pick_choice(
                           win, r, 'Clock', _CLOCK_CHOICES,
                           lambda v: self._set_clock(win, v))),
            ui.nav_row('Reset home layout…', 'Back to the default slots',
                       lambda _r: self._confirm_reset(win))])
        win._launcher_home_rows = home[1]
        widgets_group = ui.group(
            'Widgets', note='Tap a widget to choose what tapping it on '
                            'home does.')
        win._launcher_widgets_box = widgets_group[1]
        weather = self._build_weather_group(win)
        drawer = self._build_drawer_group(win)
        gestures = self._build_gestures_group(win)
        self.refresh(win)
        return home + widgets_group + weather + drawer + gestures

    def refresh(self, win) -> None:
        self._sync_switches(win)
        self._render_home(win)
        self._render_widgets(win)
        self._render_weather(win)
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

    def _pick_choice(self, win, anchor, title: str, choices, apply) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        actions._show_action_menu(
            anchor, title,
            [(label, lambda value=value: apply(value))
             for value, label in choices])

    def _switch(self, win, attr: str, title: str, subtitle: str, active,
                handler):
        row = ui.switch_row(title, subtitle, bool(active))
        row.switch.connect('notify::active',
                           lambda sw, _p: handler(win, sw))
        setattr(win, attr, row.switch)
        return row

    # -- Home -----------------------------------------------------------

    def _render_home(self, win) -> None:
        card = getattr(win, '_launcher_home_rows', None)
        if card is None:
            return
        rows = []
        child = card.get_first_child()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        if len(rows) < 2:
            return
        ui.set_subtitle(rows[0], _choice_label(
            _ALIGN_CHOICES, getattr(win.config, 'home_alignment', 'center'),
            'Center'))
        ui.set_subtitle(rows[1], _choice_label(
            _CLOCK_CHOICES, _config_get(win, 'clock_24h', None), 'System'))

    def _set_alignment(self, win, value: str) -> None:
        try:
            win.config.set_home_alignment(value)
        except Exception:
            return
        self._render_home(win)
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

    def _render_widgets(self, win) -> None:
        """One row per widget: name, its tap action, the on/off switch.
        A tap on the row picks the tap action; the last row reorders."""
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
            row = ui.switch_row(
                _WIDGET_TITLES.get(key, key),
                'On tap: %s' % _tap_text(win, key),
                bool(configs[key].get('enabled', True)),
                on_activate=lambda r, k=key: self._pick_tap(win, r, k))
            row.switch.connect(
                'notify::active',
                lambda sw, _p, k=key: self._on_widget_flipped(win, k, sw))
            rows.append(row)
        if len(ordered) > 1:
            rows.append(ui.nav_row(
                'Rearrange widgets…', 'Drag to set the order on home',
                lambda r: self._show_widget_reorder(win, r)))
        ui.fill(box, rows)

    def _on_widget_flipped(self, win, key: str, switch) -> None:
        if getattr(win, '_launcher_updating', False):
            return
        try:
            win.config.set_widget_enabled(key, bool(switch.get_active()))
            win._build_widget_block()
        except Exception:
            pass

    def _show_widget_reorder(self, win, anchor) -> None:
        """The shared drag-to-reorder sheet (reorder_sheet.py)."""
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        try:
            configs = win.config.widgets
        except Exception:
            return
        ordered = sorted(configs, key=lambda k: configs[k].get('order', 99))
        actions.show_reorder(
            anchor, 'Rearrange widgets',
            [(key, _WIDGET_TITLES.get(key, key)) for key in ordered],
            on_order_changed=lambda ids: self._apply_widget_order(win, ids))

    def _apply_widget_order(self, win, ids: list) -> None:
        try:
            if win.config.set_widget_order(list(ids)):
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
        actions._show_action_menu(
            anchor, 'On tap: %s' % _WIDGET_TITLES.get(key, key), choices)

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

    def _build_weather_group(self, win) -> list:
        win._launcher_unit_row = ui.nav_row(
            'Temperature unit', '',
            lambda r: self._pick_choice(
                win, r, 'Temperature unit', _TEMP_CHOICES,
                lambda v: self._set_temp_unit(win, v)))
        auto = self._switch(
            win, '_launcher_auto_loc', 'Auto location',
            'For the forecast',
            _config_get(win, 'weather_auto_location', True),
            self._on_auto_loc)
        win._launcher_location_row = ui.nav_row(
            'Manual location', '', lambda r: self._open_location(win, r))
        return ui.group('Weather', [win._launcher_unit_row, auto,
                                    win._launcher_location_row])

    def _render_weather(self, win) -> None:
        ui.set_subtitle(getattr(win, '_launcher_unit_row', None),
                        _choice_label(_TEMP_CHOICES, _config_get(
                            win, 'weather_temp_unit', 'fahrenheit'), '°F'))
        ui.set_subtitle(getattr(win, '_launcher_location_row', None),
                        location_text(win))

    def _set_temp_unit(self, win, value: str) -> None:
        _config_set(win, 'weather_temp_unit', value)
        self._render_weather(win)
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

    def _open_location(self, win, anchor):
        try:
            lat = win.config.data.get('weather_lat')
            lon = win.config.data.get('weather_lon')
        except Exception:
            lat, lon = None, None
        return ui.form_sheet(
            win, anchor, 'Manual location',
            [('lat', 'Latitude', '' if lat is None else lat, 'number'),
             ('lon', 'Longitude', '' if lon is None else lon, 'number')],
            'Save location', lambda values: self._save_location(win, values),
            note='Leave both blank to clear.')

    def _save_location(self, win, values: dict) -> str | None:
        """Commit the location sheet; an error string keeps it open."""
        def _parse(text) -> float | None:
            text = str(text or '').strip()
            if not text:
                return None
            try:
                return float(text)
            except (TypeError, ValueError):
                return None

        lat = _parse(values.get('lat'))
        lon = _parse(values.get('lon'))
        if (lat is None) != (lon is None):
            return 'Set both latitude and longitude.'
        _config_set(win, 'weather_lat', lat)
        _config_set(win, 'weather_lon', lon)
        self._render_weather(win)
        try:
            win._refresh_weather(force=True)
            win._show_status('Location saved.' if lat is not None
                             else 'Location cleared.')
        except Exception:
            pass
        return None

    # -- Clock ----------------------------------------------------------

    def _set_clock(self, win, value) -> None:
        _config_set(win, 'clock_24h', value)
        self._render_home(win)
        try:
            win._refresh_clock()
        except Exception:
            pass

    # -- Drawer ---------------------------------------------------------

    def _build_drawer_group(self, win) -> list:
        win._launcher_sort_box = ui.nav_row(
            'Default sort', '', lambda r: self._pick_sort(win, r))
        auto = self._switch(
            win, '_launcher_auto_launch', 'Search auto-launch',
            'Opens a lone match; a leading space stops it',
            getattr(win.config, 'search_auto_launch', False),
            self._on_auto_launch)
        return ui.group('App drawer', [win._launcher_sort_box, auto])

    def _sort_label(self, win) -> str:
        current = _config_get(win, 'drawer_sort', 'default')
        return _choice_label(_SORT_CHOICES, current, _SORT_CHOICES[0][1])

    def _render_sort(self, win) -> None:
        ui.set_subtitle(getattr(win, '_launcher_sort_box', None),
                        self._sort_label(win))

    def _pick_sort(self, win, anchor) -> None:
        self._pick_choice(win, anchor, 'Default sort', _SORT_CHOICES,
                          lambda value: self._set_sort(win, value))

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

    def _build_gestures_group(self, win) -> list:
        rows = []
        for key, title in (('double_tap_home', _DOUBLE_TAP_TITLE),
                           ('long_press_home', _LONG_PRESS_TITLE)):
            row = ui.nav_row(
                title, _binding_text(win, key),
                lambda r, k=key, t=title: self._pick_home_gesture(
                    win, k, t, r))
            win._launcher_binding_labels[key] = row.subtitle_label
            rows.append(row)
        rows.append(self._switch(
            win, '_launcher_recents', 'Home opens recents',
            'When already on home',
            _config_get(win, 'home_to_recents', False), self._on_recents))
        return ui.group('Gestures on home', rows)

    def _render_gesture_rows(self, win) -> None:
        for key, label in (getattr(win, '_launcher_binding_labels', None)
                           or {}).items():
            try:
                label.set_text(_binding_text(win, key))
            except Exception:
                pass

    def _pick_home_gesture(self, win, key: str, title: str,
                           anchor=None) -> None:
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
        choices.append(('Default',
                        lambda: self._reset_home_gesture(win, key)))
        actions._show_action_menu(anchor, title, choices)

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

    # -- Appearance (shown on the Display & appearance page) -------------

    def build_appearance(self, win) -> list:
        win._launcher_mode_box = ui.nav_row(
            'Mode', '', lambda r: self._pick_mode(win, r))
        win._launcher_font_box = ui.nav_row(
            'Font', '', lambda r: self._pick_font(win, r))
        rows = [win._launcher_mode_box, win._launcher_font_box,
                ui.nav_row('Import font…', 'A .ttf or .otf file',
                           lambda _r: self._import_font(win)),
                self._build_scale(win)]
        self._render_mode(win)
        self._render_font(win)
        return ui.group('Look', rows)

    def _mode_label(self, win) -> str:
        try:
            mode = win.config.appearance_mode
        except AttributeError:
            mode = 'theme'
        return _choice_label(_MODE_CHOICES, mode, _MODE_CHOICES[0][1])

    def _render_mode(self, win) -> None:
        ui.set_subtitle(getattr(win, '_launcher_mode_box', None),
                        self._mode_label(win))

    def _pick_mode(self, win, anchor) -> None:
        self._pick_choice(win, anchor, 'Mode', _MODE_CHOICES,
                          lambda value: self._set_mode(win, value))

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

        adjustment = Gtk.Adjustment(
            value=float(getattr(win.config, 'text_size_scale', 1.0) or 1.0),
            lower=0.5, upper=2.0, step_increment=0.05, page_increment=0.1)
        slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                           adjustment=adjustment)
        slider.set_digits(0)
        slider.set_draw_value(False)
        slider.set_hexpand(True)
        slider.set_size_request(0, 44)
        row = _scale_row('Text size',
                         '%d%%' % round(adjustment.get_value() * 100),
                         slider)
        percent = row.value_label
        win._launcher_scale_label = percent
        slider.connect('value-changed',
                       lambda _s: self._on_scale(win, slider, percent))
        win._launcher_scale = slider
        return row

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
        ui.set_subtitle(getattr(win, '_launcher_font_box', None),
                        _font_label(win))

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
