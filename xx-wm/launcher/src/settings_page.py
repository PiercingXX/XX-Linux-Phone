from __future__ import annotations

from gesture_config import IPC_VERBS, VALID_ACTIONS


# Settings sections a quick-tile long-press can deep-link to (quick_actions
# tile_settings_section emits these); anything else opens the top of the page.
_SETTINGS_SECTION_KEYS = ('wifi', 'bluetooth', 'mobile', 'appearance',
                          'standby', 'permissions', 'smart_lock', 'charging',
                          'batteryuse', 'screentime', 'medical', 'recording',
                          'captions', 'nearby', 'findmy', 'about')

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

_UPDATE_NOTIF_ID = 999901


def _link_button(label_text: str, css: str):
    from gi.repository import Gtk

    btn = Gtk.Button(label=label_text)
    btn.add_css_class('flat')
    btn.add_css_class(css)
    return btn


def _text_action(label_text: str, callback=None):
    from xx_ui import widgets

    return widgets.text_button(label_text, callback)


def _outline_action(label_text: str, callback=None):
    from xx_ui import widgets

    return widgets.outline_button(label_text, callback)


def _replace_box_children(box, children: list) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt
    for widget in children:
        box.append(widget)


def _section_placeholder(text: str):
    from xx_ui import widgets

    return widgets.wrap_label(text)


def _shrink_button_label(btn) -> None:
    """Let a button with dynamic text (SSID, device, sink) shrink below
    its full label instead of forcing the settings page wider."""
    from gi.repository import Gtk, Pango

    child = btn.get_child()
    if isinstance(child, Gtk.Label):
        child.set_xalign(0)
        child.set_ellipsize(Pango.EllipsizeMode.END)
        child.set_max_width_chars(24)


def _shrink_settings_entry(entry) -> None:
    """Let a settings entry shrink below its placeholder width so the
    page fits a 320 px phone. Entries fill the card width regardless
    (vertical box), so only the minimum request changes."""
    entry.set_width_chars(12)


def _settings_row(title: str, control):
    from gi.repository import Gtk
    from xx_ui import widgets

    # Caption over the control, like a Field: the side-by-side layout
    # forced the page past 320 px.
    row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    row.append(widgets.caption(title))
    row.append(control)
    return row


def _section_title(text: str):
    from xx_ui import widgets

    return widgets.section_label(text)


def _section_card(spacing: int = 8):
    from xx_ui import widgets

    card = widgets.card()
    return card


def _run_bg(work, on_done) -> None:
    import threading
    from gi.repository import GLib

    def _worker() -> None:
        result = None
        try:
            result = work()
        except Exception:
            pass
        GLib.idle_add(lambda: on_done(result) and False or False)

    threading.Thread(target=_worker, daemon=True).start()


class _Section:
    """One settings page section: layout lives here, behavior on SettingsPage.

    Later lanes add sections in new files (settings_<feature>.py) and
    register them in SETTINGS_SECTIONS below. build() returns the widgets
    to append in order (title first, then card); refresh() re-queries live
    state for sections that have any.
    """

    key = ''
    title = ''

    def build(self, win) -> list:
        raise NotImplementedError

    def refresh(self, win) -> None:
        return None


class SystemSection(_Section):
    key = 'system'
    title = 'System'

    def build(self, win) -> list:
        from gi.repository import Gtk
        from xx_ui import widgets

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.append(widgets.setting_row(
            'Software updates', 'Check for updates',
            widgets.caps_button(
                'Check', lambda: win._on_check_updates_clicked(None)),
            last=True))
        body.append(widgets.outline_button(
            'Update system', lambda: win._on_update_clicked(None)))
        return [_section_title(self.title), body]


class WifiSection(_Section):
    key = 'wifi'
    title = 'WiFi'

    def build(self, win) -> list:
        from gi.repository import Gtk

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        win._wifi_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.append(win._wifi_list_box)
        body.append(_outline_action(
            'Scan for networks', lambda: win._refresh_wifi()))
        return [_section_title(self.title), body]

    def refresh(self, win) -> None:
        win._refresh_wifi()


class BluetoothSection(_Section):
    key = 'bluetooth'
    title = 'Bluetooth'

    def build(self, win) -> list:
        from gi.repository import Gtk

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        win._bt_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.append(win._bt_list_box)
        body.append(_outline_action(
            'Scan for devices', lambda: win._refresh_bluetooth(scan=True)))
        return [_section_title(self.title), body]

    def refresh(self, win) -> None:
        win._refresh_bluetooth()


class SoundSection(_Section):
    key = 'sound'
    title = 'Sound output'

    def build(self, win) -> list:
        from gi.repository import Gtk

        win._sink_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        return [_section_title(self.title), win._sink_list_box]


class BatterySection(_Section):
    key = 'battery'
    title = 'Battery'

    def build(self, win) -> list:
        win._battery_label = _section_placeholder('Checking…')
        return [_section_title(self.title), win._battery_label]


class BackupSection(_Section):
    key = 'backup'
    title = 'Backup & restore'

    def build(self, win) -> list:
        from gi.repository import Gtk
        from xx_ui import widgets

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.append(widgets.outline_button(
            'Export backup', lambda: win._on_export_backup()))
        win._restore_btn = widgets.outline_button(
            'Restore from backup',
            lambda: win._on_restore_backup(win._restore_btn))
        body.append(win._restore_btn)
        return [_section_title(self.title), body]


def _settings_entry(win, placeholder: str, text: str = '',
                    password: bool = False):
    from xx_ui import widgets

    entry = widgets.deferred_entry(placeholder)
    try:
        entry.set_text(text)
    except Exception:
        pass
    if password:
        entry.set_visibility(False)
    _shrink_settings_entry(entry)
    getattr(win, '_attach_osk', lambda _w: None)(entry)
    return entry


class MobileSection(_Section):
    key = 'mobile'
    title = 'Mobile data'

    def build(self, win) -> list:
        from gi.repository import Gtk
        from xx_ui import widgets

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        win.apn_entry = _settings_entry(
            win, 'APN (e.g. internet)', win.config.data.get('apn', ''))
        win.apn_user_entry = _settings_entry(
            win, 'Username (leave blank if none)',
            win.config.data.get('apn_user', ''))
        win.apn_pass_entry = _settings_entry(
            win, 'Password (leave blank if none)',
            win.config.data.get('apn_pass', ''), password=True)

        body.append(_settings_row('APN', win.apn_entry))
        body.append(_settings_row('Username', win.apn_user_entry))
        body.append(_settings_row('Password', win.apn_pass_entry))
        body.append(widgets.primary_button(
            'Save APN', lambda: win._on_apn_save(None)))
        return [_section_title(self.title), body]


class AppearanceSection(_Section):
    key = 'appearance'
    title = 'Appearance'

    def build(self, win) -> list:
        # The swatch picker lives on the window (it owns the live preview
        # provider); the section only gives it the caps label to sit under.
        return [_section_title(self.title), win._build_appearance_card()]


class AboutSection(_Section):
    key = 'about'
    title = 'About'

    def build(self, win) -> list:
        from xx_ui import widgets
        from importlib.metadata import version as get_version

        try:
            shell_version = get_version('xx-wm')
        except Exception:
            shell_version = '0.1.1'

        about_card = _section_card()
        about_card.append(widgets.setting_row('Version', shell_version))
        about_card.append(widgets.setting_row(
            'Device', win._get_device_name(), last=True))
        return [_section_title(self.title), about_card]


class ShellSection(_Section):
    key = 'shell'
    title = 'Shell'

    def build(self, _win) -> list:
        # Shell preferences live in ~/.config/xx-wm/ (design.md "Settings
        # scope"), not on this system-only page — the gesture bindings stay
        # reachable here at the bottom, under a Shell heading that says so.
        shell_note = _section_title(self.title)
        note = _section_placeholder(
            'Other shell preferences live in ~/.config/xx-wm/')
        return [shell_note, note]


class RecordingSection(_Section):
    """Screen recording status (read-only): the Record tile owns capture."""

    key = 'recording'
    title = 'Screen recording'

    def build(self, win) -> list:
        card = _section_card()
        self._status = _section_placeholder('Not recording.')
        card.append(self._status)
        card.append(_section_placeholder(
            'Recordings save to ~/Videos/Screenrecordings.'))
        return [_section_title(self.title), card]

    def refresh(self, win) -> None:
        def _state():
            try:
                import capture
                recorder = capture.get_recorder()
                if recorder.is_recording():
                    return 'Recording…'
            except Exception:
                pass
            return 'Not recording.'

        _run_bg(_state, self._render)

    def _render(self, status) -> None:
        try:
            if self._status is not None:
                self._status.set_text(status or 'Not recording.')
        except Exception:
            pass


class CaptionsSection(_Section):
    """Live captions status (read-only): the Captions tile switches it."""

    key = 'captions'
    title = 'Captions'

    def build(self, win) -> list:
        try:
            import captions
            installed = captions.captions_installed()
        except Exception:
            installed = False
        if installed:
            note = ('Live captions are available — '
                    'use the Captions tile to switch them on.')
        else:
            note = 'Live captions are not installed on this device.'
        return [_section_title(self.title), _section_placeholder(note)]


SHARE_APP_ID = 'io.piercingxx.XXShare.desktop'


class NearbySection(_Section):
    key = 'nearby'
    title = 'Nearby share'

    def build(self, win) -> list:
        from gi.repository import Gio
        from xx_ui import widgets
        try:
            installed = Gio.DesktopAppInfo.new(SHARE_APP_ID) is not None
        except Exception:
            installed = False
        if not installed:
            return [_section_title(self.title), _section_placeholder(
                'Nearby share is not installed on this device.')]
        return [_section_title(self.title),
                _section_placeholder(
                    'Send and receive files with nearby phones and computers'
                    ' (LocalSend compatible).'),
                widgets.outline_button(
                    'Open Share',
                    lambda *_a: win._launch_app_id(SHARE_APP_ID))]


class FindMySection(_Section):
    """Find My Device stays OFF with no enabling control."""

    key = 'findmy'
    title = 'Find My Device'

    def build(self, win) -> list:
        from xx_ui import widgets

        card = _section_card()
        card.append(widgets.setting_row('Find My Device', 'Off', last=True))
        card.append(_section_placeholder(
            'This device cannot be located remotely.'))
        return [_section_title(self.title), card]


class DeveloperSection(_Section):
    key = 'developer'
    title = 'Developer'

    def build(self, win) -> list:
        return [_section_title(self.title), win._build_developer_card()]

    def refresh(self, win) -> None:
        win._refresh_xxdb_section()


class GesturesSection(_Section):
    key = 'gestures'
    title = ''

    def build(self, win) -> list:
        # The gestures card stays the last block by contract.
        return [win._build_gestures_card()]

    def refresh(self, win) -> None:
        win._refresh_gesture_labels()


# Page order: later lanes append new (key, section) pairs here; the gestures
# card must stay last (a test pins it as the final block).
#
# The feature sections below live in their own settings_<feature>.py
# modules (plain module imports, not from-imports: those modules read
# settings_page attributes at call time, so either import order works
# and the no-circular-imports test stays green).
import settings_battery_use
import settings_charging
import settings_medical
import settings_permissions
import settings_screentime
import settings_smart_lock
import settings_standby

SETTINGS_SECTIONS: list[tuple[str, type[_Section]]] = [
    ('system', SystemSection),
    ('wifi', WifiSection),
    ('bluetooth', BluetoothSection),
    ('sound', SoundSection),
    ('battery', BatterySection),
    ('charging', settings_charging.ChargingSection),
    ('batteryuse', settings_battery_use.BatteryUseSection),
    ('screentime', settings_screentime.ScreenTimeSection),
    ('backup', BackupSection),
    ('mobile', MobileSection),
    ('appearance', AppearanceSection),
    ('standby', settings_standby.StandbySection),
    ('permissions', settings_permissions.PermissionsSection),
    ('smart_lock', settings_smart_lock.SmartLockSection),
    ('medical', settings_medical.MedicalSection),
    ('recording', RecordingSection),
    ('captions', CaptionsSection),
    ('nearby', NearbySection),
    ('findmy', FindMySection),
    ('about', AboutSection),
    ('shell', ShellSection),
    ('developer', DeveloperSection),
    ('gestures', GesturesSection),
]


class SettingsPage:
    """Settings page behavior for ShellWindow.

    Holds only a reference to the window; all widget refs live on the
    window (win._wifi_list_box, win.apn_entry, ...) so bare-instance
    headless tests keep working. Method names mirror the ShellWindow
    delegators in window.py. Every behavior call routes through win so
    test stubs on the instance keep working.
    """

    def __init__(self, win) -> None:
        self._win = win
        self._sections: dict[str, _Section] = {
            key: cls() for key, cls in SETTINGS_SECTIONS
        }

    def section(self, key: str) -> _Section | None:
        return self._sections.get(key)

    def _build_settings_page(self):
        from gi.repository import Gtk
        from xx_ui import widgets as _widgets

        win = self._win
        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.add_css_class('xx-page-body')
        box.set_margin_top(16)
        box.set_margin_bottom(24)

        header = _widgets.page_header('Settings', 'XX-WM SHELL')
        # The page box already carries the gutter; the header's own
        # padding would double it and misalign the title.
        header.remove_css_class('xx-page-body')

        built: dict[str, list] = {}
        box.append(header)
        for key, _cls in SETTINGS_SECTIONS:
            section_widgets = self._sections[key].build(win)
            built[key] = section_widgets
            for widget in section_widgets:
                box.append(widget)

        # Refresh the quick sections whenever the settings page is opened
        win.stack.connect(
            'notify::visible-child-name',
            lambda stack, _p: win._refresh_system_sections()
            if stack.get_visible_child_name() == 'settings' else None,
        )

        win._settings_scroll = scroll
        win._settings_section_titles = {
            key: built[key][0] for key in _SETTINGS_SECTION_KEYS
        }

        scroll.set_child(box)
        return scroll

    def open_settings(self, section: str | None = None) -> None:
        """Show the settings page, scrolled to a quick-tile section.

        `section` is one of _SETTINGS_SECTION_KEYS (or None for the top);
        unknown values open the top. The linked section also refreshes:
        a WiFi scan for 'wifi', a device list for 'bluetooth', live state
        for the rest."""
        from gi.repository import GLib

        win = self._win
        key = section if section in _SETTINGS_SECTION_KEYS else None
        win.stack.set_visible_child_name('settings')
        win.present()
        win._scroll_settings_to(key)
        if key is not None:
            # Showing the page re-lays it out over the next frames, so an
            # early offset reads stale allocations (upper 0 clamps to the
            # top). Retry on a ladder past layout; each pass is idempotent,
            # so late passes are no-ops once the offset sticks.
            GLib.idle_add(win._scroll_settings_to, key)
            for retry_ms in (150, 300, 600, 1000, 1500):
                GLib.timeout_add(retry_ms, win._scroll_settings_to, key)
            try:
                self._sections[key].refresh(win)
            except Exception:
                pass

    def _scroll_settings_to(self, section: str | None) -> bool:
        win = self._win
        titles = getattr(win, '_settings_section_titles', None) or {}
        target = titles.get(section)
        scroll = getattr(win, '_settings_scroll', None)
        if target is None or scroll is None:
            return False
        offset = target.get_allocation().y
        scroll.get_vadjustment().set_value(max(0.0, float(offset)))
        return False

    def _gesture_binding_text(self, key: str) -> str:
        win = self._win
        action = win.gesture_config.get(key) or 'none'
        if action == 'none':
            return 'Not set'
        if action in _VERB_LABELS:
            return _VERB_LABELS[action]
        if action == 'camera':
            return 'Camera'
        if action.startswith('launch:'):
            app_id = action[len('launch:'):]
            for entry in win.app_index.entries:
                if entry.app_id == app_id:
                    return win.config.label_for(app_id, entry.name)
            return app_id
        return action.replace('_', ' ').title()

    def _refresh_gesture_labels(self) -> None:
        win = self._win
        for key, label in win._gesture_binding_labels.items():
            label.set_text(win._gesture_binding_text(key))
        for key, label in win._system_gesture_labels.items():
            label.set_text(win._gesture_binding_text(key))

    def _build_gesture_row(self, title_text: str, binding, change, reset):
        """One gesture binding stacked title/binding/buttons: the binding
        beside both buttons forced the settings page past 320 px."""
        from gi.repository import Gtk
        from xx_ui import widgets

        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row.add_css_class('xx-setting')
        name = widgets.label(title_text, 'xx-title')
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.append(change)
        buttons.append(reset)
        row.append(name)
        row.append(binding)
        row.append(buttons)
        return row

    def _binding_label(self, key: str, store: dict):
        from xx_ui import widgets

        win = self._win
        binding = widgets.label(
            win._gesture_binding_text(key), 'xx-secondary')
        binding.set_hexpand(True)
        store[key] = binding
        return binding

    @staticmethod
    def _link_button(label_text: str, css: str):
        return _link_button(label_text, css)

    def _build_gestures_card(self):
        from xx_ui import widgets

        win = self._win
        card = _section_card()
        for key, title in _GESTURE_TITLES.items():
            card.append(win._build_gesture_row(
                title, win._binding_label(key, win._gesture_binding_labels),
                _text_action('Change',
                             lambda k=key, t=title: win._pick_gesture_app(k, t)),
                _text_action('Clear',
                             lambda k=key: win._clear_gesture_app(k))))

        for key, title in _SYSTEM_GESTURE_TITLES.items():
            # The Change menu anchors on its own button, so it is wired
            # after the row hands the button back.
            change = widgets.text_button('Change')
            default = _text_action(
                'Default', lambda k=key: win._reset_system_gesture(k))
            card.append(win._build_gesture_row(
                title, win._binding_label(key, win._system_gesture_labels),
                change, default))
            change.connect(
                'clicked',
                lambda _b, c=change, k=key, t=title: win._pick_system_gesture(
                    c, k, t))
        note = _section_placeholder(
            'System-level gestures apply immediately. Home swipes stay in-shell.')
        card.append(note)
        return card

    def _pick_system_gesture(self, anchor, key: str, title: str) -> None:
        win = self._win
        choices = [(_ACTION_LABELS[a], lambda a=a: win._set_system_gesture(key, a))
                   for a in sorted(VALID_ACTIONS)]
        choices += [(_VERB_LABELS[v], lambda v=v: win._set_system_gesture(key, v))
                    for v in sorted(IPC_VERBS)]
        win.item_actions._show_action_menu(anchor, title, choices)

    def _set_system_gesture(self, key: str, value: str) -> None:
        win = self._win
        win.gesture_config.set(key, value)
        win._refresh_gesture_labels()

    def _reset_system_gesture(self, key: str) -> None:
        win = self._win
        win.gesture_config.reset(key)
        win._refresh_gesture_labels()

    def _pick_gesture_app(self, key: str, title: str) -> None:
        win = self._win
        win._pick_gesture_key = key
        win.stack.set_visible_child_name('apps')
        win._show_status(f'Tap an app to bind to {title}.')

    def _clear_gesture_app(self, key: str) -> None:
        win = self._win
        win.gesture_config.set(key, 'none')
        win._refresh_gesture_labels()

    # -- Developer section: wireless debugging (xxdb) ----------------------

    def _build_developer_card(self):
        from gi.repository import Gtk
        from xx_ui import widgets

        win = self._win
        card = _section_card()

        # The flip handler reads the switch itself, so it stays on the
        # raw notify signal instead of the bool-only builder callback.
        switch = widgets.make_switch(False)
        switch.connect('notify::active',
                       lambda s, p: win._on_xxdb_switch_flipped(s, p))
        win._xxdb_switch = switch
        card.append(widgets.setting_row(
            'Wireless debugging', '', switch, last=True))

        win._xxdb_status = _section_placeholder('Off')
        pair_btn = _outline_action(
            'Pair with code',
            lambda: win._on_xxdb_pair_clicked())

        win._xxdb_hosts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                      spacing=4)
        win._xxdb_pending_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                        spacing=4)

        card.append(win._xxdb_status)
        card.append(pair_btn)
        card.append(win._xxdb_hosts_box)
        card.append(win._xxdb_pending_box)
        win._refresh_xxdb_section()
        return card

    def _refresh_xxdb_section(self) -> None:
        from gi.repository import Gtk
        from xx_ui import widgets

        win = self._win
        if getattr(win, '_xxdb_switch', None) is None:
            return
        ctrl = getattr(win, 'xxdb_controller', None)
        win._xxdb_switch_updating = True
        try:
            win._xxdb_switch.set_active(
                bool(ctrl is not None and (ctrl.enabled or ctrl.starting)))
        finally:
            win._xxdb_switch_updating = False
        if ctrl is None:
            win._xxdb_status.set_text('Off')
        else:
            win._xxdb_status.set_text(ctrl.status_text())
        hosts: list = []
        if ctrl is not None:
            for entry in ctrl.trusted_hosts():
                name = widgets.label(
                    '%s — %s…' % (entry['name'], entry['fingerprint'][:16]),
                    'xx-secondary')
                name.set_hexpand(True)
                forget = widgets.text_button(
                    'Forget',
                    lambda fp=entry['fingerprint']: win._on_xxdb_forget(fp))
                line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=8)
                line.append(name)
                line.append(forget)
                hosts.append(line)
        _replace_box_children(win._xxdb_hosts_box, hosts or
                              [_section_placeholder('No paired hosts.')])
        pending: list = []
        if ctrl is not None:
            for token, label in ctrl.pending_system_requests():
                text = widgets.wrap_label('Allow file access: %s' % label)
                text.set_hexpand(True)
                allow = widgets.text_button(
                    'Allow',
                    lambda t=token: win._on_xxdb_confirm(t, True))
                deny = widgets.text_button(
                    'Deny', lambda t=token: win._on_xxdb_confirm(t, False))
                line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=8)
                line.append(text)
                line.append(allow)
                line.append(deny)
                pending.append(line)
        _replace_box_children(win._xxdb_pending_box, pending)

    def _on_xxdb_switch_flipped(self, switch, _pspec) -> None:
        win = self._win
        if win._xxdb_switch_updating:
            return
        ctrl = getattr(win, 'xxdb_controller', None)
        if ctrl is None:
            return
        if bool(switch.get_active()):
            ctrl.request_enable()
        else:
            ctrl.request_disable()
        win._refresh_xxdb_section()

    def _on_xxdb_pair_clicked(self) -> None:
        win = self._win
        ctrl = getattr(win, 'xxdb_controller', None)
        if ctrl is None or not ctrl.enabled:
            win._xxdb_status.set_text('Turn on wireless debugging first.')
            return
        result = ctrl.issue_pairing_code()
        if result is None:
            win._xxdb_status.set_text('Turn on wireless debugging first.')
            return
        code, shown, _expires = result
        win._xxdb_status.set_text(
            f'{shown} — code {code} (valid 2 minutes, single use)')
        win._refresh_xxdb_section()
        win._xxdb_status.set_text(
            f'{shown} — code {code} (valid 2 minutes, single use)')

    def _on_xxdb_forget(self, fingerprint: str) -> None:
        win = self._win
        ctrl = getattr(win, 'xxdb_controller', None)
        if ctrl is not None:
            ctrl.forget_host(fingerprint)
        win._refresh_xxdb_section()

    def _on_xxdb_confirm(self, token: str, allow: bool) -> None:
        win = self._win
        ctrl = getattr(win, 'xxdb_controller', None)
        if ctrl is not None:
            ctrl.confirm_system_request(token, allow)
        win._refresh_xxdb_section()

    # -- system settings sections (design.md "Settings scope") -------------

    @staticmethod
    def _run_bg(work, on_done) -> None:
        return _run_bg(work, on_done)

    @staticmethod
    def _replace_box_children(box, children: list) -> None:
        return _replace_box_children(box, children)

    def _section_placeholder(self, text: str):
        return _section_placeholder(text)

    @staticmethod
    def _shrink_button_label(btn) -> None:
        return _shrink_button_label(btn)

    @staticmethod
    def _shrink_settings_entry(entry) -> None:
        return _shrink_settings_entry(entry)

    def _refresh_system_sections(self) -> None:
        win = self._win
        win._refresh_xxdb_section()
        import system_settings
        win._run_bg(system_settings.battery_status, win._show_battery)
        win._run_bg(system_settings.list_audio_sinks, win._show_sinks)
        win._run_bg(system_settings.list_bt_devices, win._show_bt_devices)
        for key in ('standby', 'permissions', 'smart_lock', 'recording'):
            section = self._sections.get(key)
            if section is not None:
                try:
                    section.refresh(win)
                except Exception:
                    pass

    def _show_battery(self, status: str | None) -> None:
        self._win._battery_label.set_text(status or 'No battery detected.')

    def _show_sinks(self, sinks) -> None:
        from xx_ui import widgets

        win = self._win
        if sinks is None:
            _replace_box_children(
                win._sink_list_box,
                [_section_placeholder('Audio system unavailable.')])
            return
        rows = []
        items = list(sinks)
        for index, sink in enumerate(items):
            row = widgets.list_row(
                sink.description,
                'Default output' if sink.is_default else '',
                last=index == len(items) - 1)
            row.connect(
                'clicked', lambda _b, s=sink: win._on_sink_selected(s))
            rows.append(row)
        _replace_box_children(
            win._sink_list_box,
            rows or [_section_placeholder('No output devices.')])

    def _on_sink_selected(self, sink) -> None:
        import system_settings

        win = self._win
        win._run_bg(
            lambda: system_settings.set_default_sink(sink.name),
            lambda ok: (win._show_status(
                f'Output set to {sink.description}.' if ok else 'Could not switch output.'),
                win._run_bg(system_settings.list_audio_sinks, win._show_sinks)),
        )

    def _refresh_wifi(self) -> None:
        import system_settings

        win = self._win
        _replace_box_children(
            win._wifi_list_box, [_section_placeholder('Scanning…')])
        win._run_bg(system_settings.list_wifi, win._show_wifi)

    def _show_wifi(self, networks) -> None:
        from xx_ui import widgets

        win = self._win
        if networks is None:
            _replace_box_children(
                win._wifi_list_box,
                [_section_placeholder('WiFi unavailable on this device.')])
            return
        items = list(networks[:12])
        rows = []
        for index, net in enumerate(items):
            if net.in_use:
                detail = 'Connected'
            elif net.security == 'open':
                detail = 'Open network'
            else:
                detail = 'Secured'
            row = widgets.list_row(
                net.ssid or '(hidden network)', detail,
                meta='%d%%' % net.signal if net.signal is not None else '',
                last=index == len(items) - 1)
            row.connect(
                'clicked',
                lambda _b, n=net, w=row: win._on_wifi_selected(n, w))
            rows.append(row)
        _replace_box_children(
            win._wifi_list_box,
            rows or [_section_placeholder('No networks found.')])

    def _on_wifi_selected(self, net, widget) -> None:
        win = self._win
        if net.in_use:
            win._show_status(f'Already connected to {net.ssid}.')
            return
        if net.security == 'open':
            win._connect_wifi(net.ssid, None, net.security)
            return
        win.item_actions._show_entry_dialog(
            widget, f'Password for {net.ssid}', '', 'Connect',
            lambda password: win._connect_wifi(net.ssid, password, net.security),
            password=True)

    def _connect_wifi(self, ssid: str, password: str | None,
                      security: str | None = None) -> None:
        import system_settings

        win = self._win
        win._show_status(f'Connecting to {ssid}…')
        win._run_bg(
            lambda: system_settings.connect_wifi(ssid, password, security=security),
            lambda result: (win._show_status(result[1] if result else 'Connection failed.'),
                            win._refresh_wifi()),
        )

    def _refresh_bluetooth(self, scan: bool = False) -> None:
        import system_settings
        from gi.repository import GLib

        win = self._win
        if scan:
            _replace_box_children(
                win._bt_list_box, [_section_placeholder('Scanning…')])
            system_settings.start_bt_discovery()
            GLib.timeout_add_seconds(
                6, lambda: win._run_bg(system_settings.list_bt_devices,
                                       win._show_bt_devices) or False)
            return
        win._run_bg(system_settings.list_bt_devices, win._show_bt_devices)

    def _show_bt_devices(self, devices) -> None:
        from xx_ui import widgets

        win = self._win
        if devices is None:
            _replace_box_children(
                win._bt_list_box,
                [_section_placeholder('Bluetooth unavailable on this device.')])
            return
        items = list(devices[:12])
        rows = []
        for index, dev in enumerate(items):
            if dev.connected:
                state = 'Connected'
            elif dev.paired:
                state = 'Paired'
            else:
                state = 'Available'
            row = widgets.list_row(
                dev.name or dev.address, state,
                last=index == len(items) - 1)
            row.connect('clicked', lambda _b, d=dev: win._on_bt_selected(d))
            rows.append(row)
        _replace_box_children(
            win._bt_list_box,
            rows or [_section_placeholder('No devices — scan to discover.')])

    def _on_bt_selected(self, dev) -> None:
        import system_settings

        win = self._win
        action = (system_settings.connect_bt_device if dev.paired
                  else system_settings.pair_bt_device)
        win._show_status(f'{"Connecting" if dev.paired else "Pairing"} {dev.name}…')
        win._run_bg(
            lambda: action(dev.address),
            lambda result: (win._show_status(result[1] if result else 'Bluetooth error.'),
                            win._refresh_bluetooth()),
        )

    def _settings_row(self, title: str, control):
        return _settings_row(title, control)

    def _on_apn_save(self, _btn) -> None:
        win = self._win
        apn = win.apn_entry.get_text().strip()
        user = win.apn_user_entry.get_text().strip()
        pwd = win.apn_pass_entry.get_text()
        win.config.data['apn'] = apn
        win.config.data['apn_user'] = user
        win.config.data['apn_pass'] = pwd
        win.config.save()
        win._apply_apn(apn, user, pwd)
        win._show_status('APN saved.')

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
        win = self._win
        if not apn:
            return
        # The gsm lookup shells nmcli (up to 5 s); the modify itself stays a
        # fire-and-forget Popen. Both run off the GTK thread.
        args = (apn, user, pwd)

        def _work() -> None:
            try:
                import subprocess
                conn = win._gsm_connection_name()
                subprocess.Popen(
                    ['nmcli', 'connection', 'modify', conn,
                     'gsm.apn', args[0], 'gsm.username', args[1],
                     'gsm.password', args[2]],
                    close_fds=True,
                )
            except Exception as error:
                from shell_log import get_logger
                get_logger('window').warning('APN update failed: %s', error)

        win._run_bg(_work, lambda _result: None)

    def _on_update_clicked(self, _btn=None) -> None:
        from update_checker import run_update_script

        win = self._win
        ok, detail = run_update_script(win.config.update_script)
        if ok:
            win._show_status('Update running in terminal.')
        else:
            win._show_status(detail)

    def _on_check_updates_clicked(self, _btn) -> None:
        win = self._win
        win._show_status('Checking for updates...')
        win._update_checker.check_now(win._on_manual_check_result)

    def _on_manual_check_result(self, count: int | None) -> None:
        win = self._win
        if count is None:
            win._show_status('Update status unknown — package index is stale.')
        elif count > 0:
            win._on_updates_available(count)
        else:
            win._show_status('System is up to date.')

    def _on_updates_available(self, count: int) -> None:
        win = self._win
        plural = 's' if count != 1 else ''
        win._ensure_shade().add_notification(
            _UPDATE_NOTIF_ID,
            'System',
            'Updates available',
            f'{count} package{plural} can be updated.',
            actions=[
                ('Update now', win._on_update_clicked),
                ('Snooze 1 week', win._snooze_updates),
            ],
        )
        win._show_status(f'{count} update{plural} available — see notifications.')

    def _snooze_updates(self) -> None:
        win = self._win
        win._update_checker.snooze()
        win._show_status('Update checks snoozed for 1 week.')

    def _on_export_backup(self) -> None:
        from backup import backup_export_dir, export_backup
        import json
        from datetime import datetime

        win = self._win
        backup = export_backup(win.config)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'xx-wm-backup-{timestamp}.json'
        filepath = backup_export_dir() / filename

        filepath.write_text(json.dumps(backup, indent=2), encoding='utf-8')
        win._show_status(f'Backup exported to {filepath}')

    def _on_restore_backup(self, anchor=None) -> None:
        # A file chooser is a normal toplevel: it would open UNDER this
        # layer-shell TOP surface. Restore picks from an in-shell menu of
        # the xx-wm-backup-*.json files in ~ and ~/Documents instead.
        from backup import backup_label, discover_backups

        win = self._win
        if anchor is None:
            anchor = getattr(win, '_restore_btn', None)
        backups = discover_backups()
        if not backups:
            win._show_status('No backups found in ~ or ~/Documents')
            return
        actions: list = []
        for path in backups:
            label = backup_label(path)
            actions.append(
                (label,
                 lambda p=path, lb=label: win._confirm_restore_backup(anchor, p, lb)))
        actions.append(('Cancel', lambda: None))
        win.item_actions._show_action_menu(anchor, 'Restore from backup', actions)

    def _confirm_restore_backup(self, anchor, path, label: str) -> None:
        win = self._win
        win.item_actions._show_action_menu(
            anchor,
            f'Restore backup from {label}? This replaces your home layout and settings.',
            [('Restore', lambda: win._restore_from_path(str(path))),
             ('Cancel', lambda: None)])

    def _restore_from_path(self, filepath: str | None) -> None:
        from backup import restore_backup, validate_backup
        import json
        from pathlib import Path

        win = self._win
        if not filepath:
            win._show_status('No backup file selected.')
            return

        try:
            payload = json.loads(Path(filepath).read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as e:
            win._show_status(f'Failed to read backup: {e}')
            return

        is_valid, error = validate_backup(payload)
        if not is_valid:
            win._show_status(f'Invalid backup: {error}')
            return

        success = restore_backup(win.config, payload)
        if success:
            win._show_status('Backup restored successfully')
            win._apply_config_change()
        else:
            win._show_status('Failed to restore backup')

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
