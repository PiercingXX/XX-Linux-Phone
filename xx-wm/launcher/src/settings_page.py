from __future__ import annotations

from gesture_config import IPC_VERBS, VALID_ACTIONS


# Settings sections a quick-tile long-press can deep-link to (quick_actions
# tile_settings_section emits these); anything else opens the top of the page.
_SETTINGS_SECTION_KEYS = ('wifi', 'bluetooth', 'mobile', 'standby',
                          'permissions', 'smart_lock', 'charging',
                          'batteryuse', 'screentime', 'medical')

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


def _replace_box_children(box, children: list) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt
    for widget in children:
        box.append(widget)


def _section_placeholder(text: str):
    from gi.repository import Gtk, Pango

    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class('dim-label')
    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_max_width_chars(26)
    return label


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
    from gi.repository import Gtk, Pango

    # Title above the control: label + entry side by side forced width.
    row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    label = Gtk.Label(label=title, xalign=0)
    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    row.append(label)
    row.append(control)
    return row


def _section_title(text: str):
    from gi.repository import Gtk

    title = Gtk.Label(label=text, xalign=0)
    title.add_css_class('section-title')
    return title


def _section_card(spacing: int = 12):
    from gi.repository import Gtk

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    card.add_css_class('settings-card')
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

        system_card = _section_card()
        update_button = Gtk.Button(label='Update system')
        update_button.add_css_class('flat')
        update_button.add_css_class('action-link')
        update_button.connect('clicked', win._on_update_clicked)

        check_updates_button = Gtk.Button(label='Check for updates now')
        check_updates_button.add_css_class('flat')
        check_updates_button.add_css_class('action-link')
        check_updates_button.connect('clicked', win._on_check_updates_clicked)

        system_card.append(update_button)
        system_card.append(check_updates_button)
        return [_section_title(self.title), system_card]


class WifiSection(_Section):
    key = 'wifi'
    title = 'WiFi'

    def build(self, win) -> list:
        from gi.repository import Gtk

        wifi_card = _section_card(spacing=8)
        win._wifi_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        wifi_scan_btn = Gtk.Button(label='Scan for networks')
        wifi_scan_btn.add_css_class('flat')
        wifi_scan_btn.add_css_class('action-link')
        wifi_scan_btn.connect('clicked', lambda _b: win._refresh_wifi())
        wifi_card.append(win._wifi_list_box)
        wifi_card.append(wifi_scan_btn)
        return [_section_title(self.title), wifi_card]

    def refresh(self, win) -> None:
        win._refresh_wifi()


class BluetoothSection(_Section):
    key = 'bluetooth'
    title = 'Bluetooth'

    def build(self, win) -> list:
        from gi.repository import Gtk

        bt_card = _section_card(spacing=8)
        win._bt_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bt_scan_btn = Gtk.Button(label='Scan for devices')
        bt_scan_btn.add_css_class('flat')
        bt_scan_btn.add_css_class('action-link')
        bt_scan_btn.connect('clicked', lambda _b: win._refresh_bluetooth(scan=True))
        bt_card.append(win._bt_list_box)
        bt_card.append(bt_scan_btn)
        return [_section_title(self.title), bt_card]

    def refresh(self, win) -> None:
        win._refresh_bluetooth()


class SoundSection(_Section):
    key = 'sound'
    title = 'Sound output'

    def build(self, win) -> list:
        from gi.repository import Gtk

        sound_card = _section_card(spacing=8)
        win._sink_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sound_card.append(win._sink_list_box)
        return [_section_title(self.title), sound_card]


class BatterySection(_Section):
    key = 'battery'
    title = 'Battery'

    def build(self, win) -> list:
        from gi.repository import Gtk, Pango

        battery_card = _section_card(spacing=8)
        win._battery_label = Gtk.Label(label='Checking…', xalign=0)
        win._battery_label.add_css_class('dim-label')
        win._battery_label.set_wrap(True)
        win._battery_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        win._battery_label.set_max_width_chars(26)
        battery_card.append(win._battery_label)
        return [_section_title(self.title), battery_card]


class BackupSection(_Section):
    key = 'backup'
    title = 'Backup & restore'

    def build(self, win) -> list:
        from gi.repository import Gtk

        backup_card = _section_card()
        export_btn = Gtk.Button(label='Export backup')
        export_btn.add_css_class('flat')
        export_btn.add_css_class('action-link')
        export_btn.connect('clicked', lambda _btn: win._on_export_backup())

        restore_btn = Gtk.Button(label='Restore from backup')
        restore_btn.add_css_class('flat')
        restore_btn.add_css_class('action-link')
        restore_btn.connect('clicked', lambda btn: win._on_restore_backup(btn))
        win._restore_btn = restore_btn

        backup_card.append(export_btn)
        backup_card.append(restore_btn)
        return [_section_title(self.title), backup_card]


class MobileSection(_Section):
    key = 'mobile'
    title = 'Mobile data'

    def build(self, win) -> list:
        from gi.repository import Gtk

        apn_card = _section_card()
        win.apn_entry = Gtk.Entry()
        win.apn_entry.set_placeholder_text('APN (e.g. internet)')
        win.apn_entry.set_text(win.config.data.get('apn', ''))
        _shrink_settings_entry(win.apn_entry)

        win.apn_user_entry = Gtk.Entry()
        win.apn_user_entry.set_placeholder_text('Username (leave blank if none)')
        win.apn_user_entry.set_text(win.config.data.get('apn_user', ''))
        _shrink_settings_entry(win.apn_user_entry)

        win.apn_pass_entry = Gtk.Entry()
        win.apn_pass_entry.set_visibility(False)
        win.apn_pass_entry.set_placeholder_text('Password (leave blank if none)')
        win.apn_pass_entry.set_text(win.config.data.get('apn_pass', ''))
        _shrink_settings_entry(win.apn_pass_entry)

        apn_save_btn = Gtk.Button(label='Save APN')
        apn_save_btn.add_css_class('flat')
        apn_save_btn.add_css_class('action-link')
        apn_save_btn.connect('clicked', win._on_apn_save)

        apn_card.append(_settings_row('APN', win.apn_entry))
        apn_card.append(_settings_row('Username', win.apn_user_entry))
        apn_card.append(_settings_row('Password', win.apn_pass_entry))
        apn_card.append(apn_save_btn)
        return [_section_title(self.title), apn_card]


class AboutSection(_Section):
    key = 'about'
    title = 'About'

    def build(self, win) -> list:
        from gi.repository import Gtk, Pango
        from importlib.metadata import version as get_version

        try:
            shell_version = get_version('xx-wm')
        except Exception:
            shell_version = '0.1.1'

        about_card = _section_card(spacing=8)
        version_label = Gtk.Label(label=f'Version: {shell_version}', xalign=0)
        version_label.add_css_class('dim-label')
        version_label.set_wrap(True)
        version_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        version_label.set_max_width_chars(26)

        device_label = Gtk.Label(label=f'Device: {win._get_device_name()}', xalign=0)
        device_label.add_css_class('dim-label')
        device_label.set_wrap(True)
        device_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        device_label.set_max_width_chars(26)

        about_card.append(version_label)
        about_card.append(device_label)
        return [_section_title(self.title), about_card]


class ShellSection(_Section):
    key = 'shell'
    title = 'Shell'

    def build(self, _win) -> list:
        from gi.repository import Gtk, Pango

        # Shell preferences live in ~/.config/xx-wm/ (design.md "Settings
        # scope"), not on this system-only page — the gesture bindings stay
        # reachable here at the bottom, under a Shell heading that says so.
        shell_note = _section_title(self.title)
        note = Gtk.Label(
            label='Other shell preferences live in ~/.config/xx-wm/', xalign=0)
        note.add_css_class('dim-label')
        note.set_wrap(True)
        note.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        note.set_max_width_chars(26)
        return [shell_note, note]


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
    ('standby', settings_standby.StandbySection),
    ('permissions', settings_permissions.PermissionsSection),
    ('smart_lock', settings_smart_lock.SmartLockSection),
    ('medical', settings_medical.MedicalSection),
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

        win = self._win
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

        built: dict[str, list] = {}
        for key, _cls in SETTINGS_SECTIONS:
            widgets = self._sections[key].build(win)
            built[key] = widgets
            for widget in widgets:
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
            GLib.idle_add(win._scroll_settings_to, key)
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
        from gi.repository import Gtk, Pango

        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(label=title_text, xalign=0)
        name.set_wrap(True)
        name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        name.set_max_width_chars(24)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.append(change)
        buttons.append(reset)
        row.append(name)
        row.append(binding)
        row.append(buttons)
        return row

    def _binding_label(self, key: str, store: dict):
        from gi.repository import Gtk, Pango

        win = self._win
        binding = Gtk.Label(label=win._gesture_binding_text(key), xalign=0)
        binding.add_css_class('dim-label')
        binding.set_hexpand(True)
        binding.set_ellipsize(Pango.EllipsizeMode.END)
        binding.set_max_width_chars(16)
        store[key] = binding
        return binding

    @staticmethod
    def _link_button(label_text: str, css: str):
        return _link_button(label_text, css)

    def _build_gestures_card(self):
        win = self._win
        card = _section_card()
        for key, title in _GESTURE_TITLES.items():
            change = _link_button('Change', 'action-link')
            change.connect('clicked', lambda _b, k=key, t=title: win._pick_gesture_app(k, t))

            clear = _link_button('Clear', 'dim-label')
            clear.connect('clicked', lambda _b, k=key: win._clear_gesture_app(k))

            card.append(win._build_gesture_row(
                title, win._binding_label(key, win._gesture_binding_labels),
                change, clear))

        for key, title in _SYSTEM_GESTURE_TITLES.items():
            change = _link_button('Change', 'action-link')
            change.connect(
                'clicked', lambda b, k=key, t=title: win._pick_system_gesture(b, k, t))

            clear = _link_button('Default', 'dim-label')
            clear.connect('clicked', lambda _b, k=key: win._reset_system_gesture(k))

            card.append(win._build_gesture_row(
                title, win._binding_label(key, win._system_gesture_labels),
                change, clear))
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
        from gi.repository import Gtk, Pango

        win = self._win
        card = _section_card()

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label='Wireless debugging', xalign=0)
        title.set_hexpand(True)
        title.set_wrap(True)
        title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title.set_max_width_chars(18)
        switch = Gtk.Switch()
        switch.set_active(False)
        switch.connect('notify::active', win._on_xxdb_switch_flipped)
        row.append(title)
        row.append(switch)
        win._xxdb_switch = switch

        win._xxdb_status = Gtk.Label(label='Off', xalign=0)
        win._xxdb_status.add_css_class('dim-label')
        win._xxdb_status.set_wrap(True)
        win._xxdb_status.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        win._xxdb_status.set_max_width_chars(26)

        pair_btn = _link_button('Pair device with pairing code',
                                'action-link')
        pair_btn.connect('clicked', lambda _b: win._on_xxdb_pair_clicked())

        win._xxdb_hosts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                      spacing=4)
        win._xxdb_pending_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                        spacing=4)

        card.append(row)
        card.append(win._xxdb_status)
        card.append(pair_btn)
        card.append(win._xxdb_hosts_box)
        card.append(win._xxdb_pending_box)
        win._refresh_xxdb_section()
        return card

    def _refresh_xxdb_section(self) -> None:
        from gi.repository import Gtk, Pango

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
                name = Gtk.Label(
                    label=f"{entry['name']} — {entry['fingerprint'][:16]}…",
                    xalign=0)
                name.add_css_class('dim-label')
                name.set_wrap(True)
                name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                name.set_max_width_chars(20)
                forget = _link_button('Forget', 'dim-label')
                forget.connect(
                    'clicked',
                    lambda _b, fp=entry['fingerprint']: win._on_xxdb_forget(fp))
                line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=8)
                name.set_hexpand(True)
                line.append(name)
                line.append(forget)
                hosts.append(line)
        _replace_box_children(win._xxdb_hosts_box, hosts or
                              [_section_placeholder('No paired hosts.')])
        pending: list = []
        if ctrl is not None:
            for token, label in ctrl.pending_system_requests():
                text = Gtk.Label(label=f'Allow file access: {label}', xalign=0)
                text.set_wrap(True)
                text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                text.set_max_width_chars(22)
                allow = _link_button('Allow', 'action-link')
                allow.connect(
                    'clicked',
                    lambda _b, t=token: win._on_xxdb_confirm(t, True))
                deny = _link_button('Deny', 'dim-label')
                deny.connect(
                    'clicked',
                    lambda _b, t=token: win._on_xxdb_confirm(t, False))
                line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=8)
                text.set_hexpand(True)
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
        for key in ('standby', 'permissions', 'smart_lock'):
            section = self._sections.get(key)
            if section is not None:
                try:
                    section.refresh(win)
                except Exception:
                    pass

    def _show_battery(self, status: str | None) -> None:
        self._win._battery_label.set_text(status or 'No battery detected.')

    def _show_sinks(self, sinks) -> None:
        from gi.repository import Gtk

        win = self._win
        if sinks is None:
            _replace_box_children(
                win._sink_list_box,
                [_section_placeholder('Audio system unavailable.')])
            return
        rows = []
        for sink in sinks:
            marker = '● ' if sink.is_default else ''
            btn = Gtk.Button(label=marker + sink.description)
            btn.add_css_class('flat')
            btn.add_css_class('action-link' if sink.is_default else 'dim-label')
            _shrink_button_label(btn)
            btn.connect('clicked', lambda _b, s=sink: win._on_sink_selected(s))
            rows.append(btn)
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
        from gi.repository import Gtk

        win = self._win
        if networks is None:
            _replace_box_children(
                win._wifi_list_box,
                [_section_placeholder('WiFi unavailable on this device.')])
            return
        rows = []
        for net in networks[:12]:
            marker = '● ' if net.in_use else ''
            lock = '' if net.security == 'open' else '  ⚿'
            btn = Gtk.Button(label=f'{marker}{net.ssid}{lock}  ({net.signal}%)')
            btn.add_css_class('flat')
            btn.add_css_class('action-link' if net.in_use else 'dim-label')
            _shrink_button_label(btn)
            btn.connect('clicked', lambda _b, n=net, w=btn: win._on_wifi_selected(n, w))
            rows.append(btn)
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
        from gi.repository import Gtk

        win = self._win
        if devices is None:
            _replace_box_children(
                win._bt_list_box,
                [_section_placeholder('Bluetooth unavailable on this device.')])
            return
        rows = []
        for dev in devices[:12]:
            state = ' — connected' if dev.connected else (' — paired' if dev.paired else '')
            btn = Gtk.Button(label=dev.name + state)
            btn.add_css_class('flat')
            btn.add_css_class('action-link' if dev.connected else 'dim-label')
            _shrink_button_label(btn)
            btn.connect('clicked', lambda _b, d=dev: win._on_bt_selected(d))
            rows.append(btn)
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
