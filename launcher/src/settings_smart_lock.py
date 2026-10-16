"""Smart Lock settings (UI over smart_lock).

Trusted Bluetooth devices (paired, via the system_settings BlueZ
helpers), trusted Wi-Fi networks (saved, via NetworkManager), the 4-hour
re-auth rule, and a clear status line. On-body unlock stays hidden: the
backend does not support it.
"""
from __future__ import annotations

import settings_page


def trust_status(win) -> tuple[str, str]:
    """(status, detail) for the header line: 'Unlocked by: X' while a
    trusted device holds trust, else 'Off' with the lock-screen reason
    as a dim detail. Never raises (bare test windows read as Off)."""
    try:
        import smart_lock
        state = smart_lock.current_state(getattr(win, 'config', None))
        line = smart_lock.lock_line_for(state)
    except Exception:
        return ('Off', '')
    prefix = 'Kept unlocked by '
    if (line.startswith(prefix)
            and (state.trusted_bt or state.trusted_ssid)):
        return ('Unlocked by: ' + line[len(prefix):], '')
    if not line or line.startswith(prefix):
        return ('Off', '')
    return ('Off', line)


def reauth_text() -> str:
    try:
        import smart_lock
        hours = int(smart_lock.TRUST_MAX_PIN_AGE_S // 3600)
    except Exception:
        hours = 4
    return 'PIN re-check every %d h.' % hours


def trusted_bt(win) -> list:
    try:
        return list(getattr(win.config, 'smart_lock_bt_devices', None) or ())
    except Exception:
        return []


def trusted_ssids(win) -> list:
    try:
        return list(getattr(win.config, 'smart_lock_wifi_ssids', None) or ())
    except Exception:
        return []


class SmartLockSection(settings_page._Section):
    key = 'smart_lock'
    title = 'Smart Lock'

    def build(self, win) -> list:
        from gi.repository import Gtk, Pango

        status_card = settings_page._section_card(spacing=8)
        status_label = Gtk.Label(label='Off', xalign=0)
        status_label.set_wrap(True)
        status_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        status_label.set_max_width_chars(24)
        win._slock_status_label = status_label
        status_card.append(status_label)
        detail_label = Gtk.Label(label='', xalign=0)
        detail_label.add_css_class('dim-label')
        detail_label.set_wrap(True)
        detail_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        detail_label.set_max_width_chars(26)
        win._slock_detail_label = detail_label
        status_card.append(detail_label)
        reauth = Gtk.Label(label=reauth_text(), xalign=0)
        reauth.add_css_class('dim-label')
        reauth.set_wrap(True)
        reauth.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        reauth.set_max_width_chars(26)
        status_card.append(reauth)

        bt_card = settings_page._section_card(spacing=8)
        bt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        win._slock_bt_box = bt_box
        bt_card.append(bt_box)
        bt_add = Gtk.Button(label='Trust a device…')
        bt_add.add_css_class('flat')
        bt_add.add_css_class('action-link')
        settings_page._shrink_button_label(bt_add)
        bt_add.connect('clicked', lambda btn: self._pick_bt_device(win, btn))
        bt_card.append(bt_add)

        wifi_card = settings_page._section_card(spacing=8)
        wifi_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        win._slock_wifi_box = wifi_box
        wifi_card.append(wifi_box)
        wifi_add = Gtk.Button(label='Trust a network…')
        wifi_add.add_css_class('flat')
        wifi_add.add_css_class('action-link')
        settings_page._shrink_button_label(wifi_add)
        wifi_add.connect('clicked', lambda btn: self._pick_ssid(win, btn))
        wifi_card.append(wifi_add)

        widgets = [settings_page._section_title(self.title), status_card,
                   settings_page._section_title('Trusted devices'), bt_card,
                   settings_page._section_title('Trusted networks'),
                   wifi_card]
        try:
            import smart_lock
            if smart_lock.on_body_supported():
                widgets.extend(self._build_on_body(win))
        except Exception:
            pass
        self.refresh(win)
        return widgets

    def _build_on_body(self, win) -> list:
        from gi.repository import Gtk, Pango

        card = settings_page._section_card(spacing=8)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label='Stay unlocked while on body', xalign=0)
        label.set_hexpand(True)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(18)
        switch = Gtk.Switch()
        switch.set_active(False)
        row.append(label)
        row.append(switch)
        card.append(row)
        return [settings_page._section_title('On body'), card]

    def refresh(self, win) -> None:
        self._render_status(win)
        self._render_bt(win)
        self._render_wifi(win)

    def _render_status(self, win) -> None:
        status, detail = trust_status(win)
        for attr, text in (('_slock_status_label', status),
                           ('_slock_detail_label', detail)):
            label = getattr(win, attr, None)
            if label is not None:
                try:
                    label.set_text(text)
                except Exception:
                    pass

    def _render_bt(self, win) -> None:
        from gi.repository import Gtk, Pango

        box = getattr(win, '_slock_bt_box', None)
        if box is None:
            return
        rows = []
        for address in trusted_bt(win):
            name = Gtk.Label(label=address, xalign=0)
            name.add_css_class('dim-label')
            name.set_hexpand(True)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(18)
            remove = settings_page._link_button('Remove', 'dim-label')
            remove.connect(
                'clicked',
                lambda _b, addr=address: self._remove_bt(win, addr))
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            line.append(name)
            line.append(remove)
            rows.append(line)
        settings_page._replace_box_children(
            box, rows or [settings_page._section_placeholder(
                'No trusted devices.')])

    def _render_wifi(self, win) -> None:
        from gi.repository import Gtk, Pango

        box = getattr(win, '_slock_wifi_box', None)
        if box is None:
            return
        rows = []
        for ssid in trusted_ssids(win):
            name = Gtk.Label(label=ssid, xalign=0)
            name.add_css_class('dim-label')
            name.set_hexpand(True)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(18)
            remove = settings_page._link_button('Remove', 'dim-label')
            remove.connect(
                'clicked',
                lambda _b, net=ssid: self._remove_ssid(win, net))
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            line.append(name)
            line.append(remove)
            rows.append(line)
        settings_page._replace_box_children(
            box, rows or [settings_page._section_placeholder(
                'No trusted networks.')])

    def _remove_bt(self, win, address: str) -> None:
        try:
            kept = [entry for entry in trusted_bt(win)
                    if str(entry).strip().upper() != str(address).strip().upper()]
            win.config.set_smart_lock_bt_devices(kept)
        except Exception:
            pass
        self._render_bt(win)

    def _remove_ssid(self, win, ssid: str) -> None:
        try:
            kept = [entry for entry in trusted_ssids(win) if entry != ssid]
            win.config.set_smart_lock_wifi_ssids(kept)
        except Exception:
            pass
        self._render_wifi(win)

    def _pick_bt_device(self, win, anchor) -> None:
        try:
            run_bg = win._run_bg
        except AttributeError:
            return
        import system_settings
        run_bg(system_settings.list_bt_devices,
               lambda devices: self._show_bt_picker(win, anchor, devices))

    def _show_bt_picker(self, win, anchor, devices) -> None:
        if devices is None:
            self._announce(win, 'Bluetooth unavailable.')
            return
        paired = [dev for dev in devices
                  if getattr(dev, 'paired', False)]
        if not paired:
            self._announce(win, 'No paired devices — pair in Bluetooth.')
            return
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        known = {str(entry).strip().upper() for entry in trusted_bt(win)}
        choices = [(_device_label(dev),
                    lambda dev=dev: self._add_bt(win, dev.address))
                   for dev in paired
                   if str(getattr(dev, 'address', '')).strip().upper() not in known]
        if not choices:
            self._announce(win, 'Every paired device is trusted.')
            return
        actions._show_action_menu(anchor, 'Trust a device', choices)

    def _add_bt(self, win, address: str) -> None:
        try:
            win.config.set_smart_lock_bt_devices(
                trusted_bt(win) + [address])
        except Exception:
            pass
        self._render_bt(win)
        self._render_status(win)

    def _pick_ssid(self, win, anchor) -> None:
        try:
            run_bg = win._run_bg
        except AttributeError:
            return
        import system_settings
        run_bg(system_settings.saved_wifi_ssids,
               lambda ssids: self._show_ssid_picker(win, anchor, ssids))

    def _show_ssid_picker(self, win, anchor, ssids) -> None:
        if ssids is None:
            self._announce(win, 'NetworkManager unavailable.')
            return
        known = set(trusted_ssids(win))
        choices = [(ssid, lambda ssid=ssid: self._add_ssid(win, ssid))
                   for ssid in ssids if ssid not in known]
        if not choices:
            self._announce(win, 'No other saved networks.')
            return
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        actions._show_action_menu(anchor, 'Trust a network', choices)

    def _add_ssid(self, win, ssid: str) -> None:
        try:
            win.config.set_smart_lock_wifi_ssids(trusted_ssids(win) + [ssid])
        except Exception:
            pass
        self._render_wifi(win)
        self._render_status(win)

    @staticmethod
    def _announce(win, text: str) -> None:
        try:
            win._show_status(text)
        except Exception:
            pass


def _device_label(dev) -> str:
    name = str(getattr(dev, 'name', '') or '').strip()
    address = str(getattr(dev, 'address', '') or '').strip()
    if name and address and name != address:
        return '%s (%s)' % (name, address)
    return name or address or '?'
