"""App permissions & privacy indicators settings.

Per-app camera/microphone/location grants from the Flatpak PermissionStore
with toggles and reset-to-ask, per-app network blocks (where the helper
supports them), a per-permission view, the privacy-indicator switch, and
the last-24-hours recently-used log from privacy_monitor.
"""
from __future__ import annotations

import time

import settings_page

PERM_KINDS = (('camera', 'Camera'), ('microphone', 'Microphone'),
              ('location', 'Location'), ('network', 'Network'))
_RECENT_KIND_LABELS = {'mic': 'mic', 'camera': 'cam', 'location': 'loc'}


def _normalize(app_id: str) -> str:
    try:
        from app_permissions import normalize_app_id
        return normalize_app_id(app_id)
    except ImportError:
        text = str(app_id or '').strip()
        if text.lower().endswith('.desktop'):
            text = text[:-8]
        return text.casefold()


def _entries(win) -> list:
    try:
        return list(getattr(getattr(win, 'app_index', None), 'entries', None)
                    or ())
    except Exception:
        return []


def _display_name(win, entry) -> str:
    try:
        return win.config.label_for(entry.app_id, entry.name)
    except Exception:
        return getattr(entry, 'name', None) or getattr(entry, 'app_id', '?')


def _is_flatpak(entry) -> bool:
    try:
        from app_permissions import app_is_flatpak
        return bool(app_is_flatpak(entry))
    except Exception:
        return False


def query_store_maps():
    """(worker) PermissionStore grants per device, or None when the
    store is unreachable. Synchronous D-Bus: never call on GTK."""
    from gi.repository import Gio
    import app_permissions as perms
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception:
        return None
    try:
        return {
            'camera': perms.lookup_permissions(
                bus, perms.DEVICES_TABLE, perms.DEVICE_CAMERA),
            'microphone': perms.lookup_permissions(
                bus, perms.DEVICES_TABLE, perms.DEVICE_MICROPHONE),
            'location': perms.lookup_permissions(
                bus, perms.LOCATION_TABLE, perms.LOCATION_ID),
        }
    except Exception:
        return None


def query_recent():
    """(worker) Last-24-h usage entries from the privacy log on disk."""
    try:
        from privacy_monitor import UsageLog
        return UsageLog().recent()
    except Exception:
        return []


def query_all():
    """(worker) One round trip for refresh: store maps plus the log."""
    return {'maps': query_store_maps(), 'recent': query_recent(),
            'at': time.time()}


def stored_state(maps, kind: str, app_id: str):
    """Stored True/False grant for one app, None for ask/unset."""
    if not isinstance(maps, dict):
        return None
    table = maps.get(kind)
    if not isinstance(table, dict):
        return None
    try:
        from app_permissions import permission_allowed
    except ImportError:
        return None
    wanted = _normalize(app_id)
    for stored_app, stored_perms in table.items():
        if _normalize(stored_app) == wanted:
            return permission_allowed(stored_perms)
    return None


def apply_store_perm(kind: str, app_id: str, allowed) -> None:
    """(worker) Grant/deny/reset one PermissionStore device for one app."""
    from gi.repository import Gio
    import app_permissions as perms
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if kind == 'camera':
        perms.set_device_permission(bus, perms.DEVICE_CAMERA, app_id, allowed)
    elif kind == 'microphone':
        perms.set_device_permission(
            bus, perms.DEVICE_MICROPHONE, app_id, allowed)
    elif kind == 'location':
        perms.set_location_permission(bus, app_id, allowed)
    else:
        raise ValueError('no store entry for %r' % (kind,))


def reset_store_perms(app_id: str) -> None:
    """(worker) Reset camera/mic/location to ask for one app."""
    for kind in ('camera', 'microphone', 'location'):
        apply_store_perm(kind, app_id, None)


def network_denied(win, app_id: str) -> bool:
    try:
        denied = list(getattr(win.config, 'network_denied_apps', None) or ())
    except Exception:
        return False
    wanted = _normalize(app_id)
    return any(_normalize(entry) == wanted for entry in denied)


def set_network_denied(win, app_id: str, denied: bool) -> None:
    try:
        current = list(getattr(win.config, 'network_denied_apps', None) or ())
    except Exception:
        current = []
    wanted = _normalize(app_id)
    kept = [entry for entry in current if _normalize(entry) != wanted]
    if denied:
        kept.append(app_id)
    try:
        win.config.set_network_denied_apps(kept)
    except Exception:
        pass


def age_text(seen: float, now: float) -> str:
    delta = max(0.0, now - seen)
    if delta < 60:
        return 'just now'
    if delta < 3600:
        return '%dm ago' % int(delta // 60)
    return '%dh ago' % int(delta // 3600)


class PermissionsSection(settings_page._Section):
    key = 'permissions'
    title = 'App permissions'

    def build(self, win) -> list:
        from gi.repository import Gtk
        from xx_ui import widgets

        head = settings_page._section_title(self.title)
        switch = widgets.make_switch(self._indicators_on(win))
        switch.connect(
            'notify::active',
            lambda sw, _p: self._on_indicators_flipped(win, sw))
        win._perm_ind_switch = switch
        indicators = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        indicators.append(widgets.setting_row(
            'Privacy indicators', 'Mic, camera and location',
            switch))
        indicators.append(settings_page._section_title('Recently used'))
        recent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win._perm_recent_box = recent_box
        indicators.append(recent_box)

        apps_head = settings_page._section_title('Apps')
        apps_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        nav_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win._perm_nav_box = nav_box
        apps_body.append(nav_box)
        apps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win._perm_apps_box = apps_box
        apps_body.append(apps_box)
        apps_body.append(settings_page._section_placeholder(
            'Only Flatpak apps can be limited here. '
            'Native apps reach camera, mic and location directly — '
            'watch the indicators.'))

        self._render_recent(win, getattr(win, '_perm_recent', None),
                            getattr(win, '_perm_recent_at', None) or time.time())
        self._render_apps(win)
        return [head, indicators, apps_head, apps_body]

    def refresh(self, win) -> None:
        self._sync_indicators(win)
        if getattr(win, '_perm_net_supported', None) is None:
            self._query_caps(win)
        try:
            run_bg = win._run_bg
        except AttributeError:
            return
        run_bg(query_all, lambda payload: self._show_all(win, payload))

    def _indicators_on(self, win) -> bool:
        try:
            from privacy_monitor import indicators_enabled
            return bool(indicators_enabled(getattr(win, 'config', None)))
        except Exception:
            return True

    def _sync_indicators(self, win) -> None:
        switch = getattr(win, '_perm_ind_switch', None)
        if switch is None:
            return
        win._perm_updating = True
        try:
            switch.set_active(self._indicators_on(win))
        except Exception:
            pass
        finally:
            win._perm_updating = False

    def _on_indicators_flipped(self, win, switch) -> None:
        if getattr(win, '_perm_updating', False):
            return
        try:
            win.config.set_privacy_indicators(bool(switch.get_active()))
        except Exception:
            pass

    def _query_caps(self, win) -> None:
        try:
            import helper_client
        except ImportError:
            win._perm_net_supported = False
            return

        def _done(ok: bool, result) -> None:
            win._perm_net_supported = bool(
                ok and isinstance(result, dict)
                and result.get('nftables') and result.get('cgroup2'))
            self._render_apps(win)

        try:
            helper_client.get_capabilities(_done)
        except Exception:
            win._perm_net_supported = False

    def _show_all(self, win, payload) -> None:
        if not isinstance(payload, dict):
            return
        win._perm_maps = payload.get('maps')
        win._perm_recent = payload.get('recent') or []
        win._perm_recent_at = payload.get('at') or time.time()
        self._render_recent(win, win._perm_recent, win._perm_recent_at)
        self._render_apps(win)

    def _perm_counts(self, win) -> dict:
        maps = getattr(win, '_perm_maps', None)
        counts = {'camera': 0, 'microphone': 0, 'location': 0, 'network': 0}
        if isinstance(maps, dict):
            try:
                from app_permissions import permission_allowed
            except ImportError:
                permission_allowed = None
            for kind in ('camera', 'microphone', 'location'):
                table = maps.get(kind)
                if not isinstance(table, dict) or permission_allowed is None:
                    continue
                counts[kind] = sum(
                    1 for perms in table.values()
                    if permission_allowed(perms) is True)
        try:
            denied = getattr(win.config, 'network_denied_apps', None) or ()
            counts['network'] = len(list(denied))
        except Exception:
            pass
        return counts

    def _render_recent(self, win, recent, at: float) -> None:
        box = getattr(win, '_perm_recent_box', None)
        if box is None:
            return
        rows = []
        if isinstance(recent, list):
            from xx_ui import widgets
            items = [item for item in recent[:8]
                     if isinstance(item, dict) and item.get('app')]
            for index, item in enumerate(items):
                kind = _RECENT_KIND_LABELS.get(item.get('kind'),
                                              item.get('kind'))
                try:
                    seen = float(item.get('at'))
                except (TypeError, ValueError):
                    continue
                rows.append(widgets.setting_row(
                    item.get('app'),
                    '%s · %s' % (kind, age_text(seen, at)),
                    last=index == len(items) - 1))
        settings_page._replace_box_children(
            box, rows or [settings_page._section_placeholder(
                'No use in the last 24 h.')])

    def _render_apps(self, win) -> None:
        from xx_ui import widgets
        nav = getattr(win, '_perm_nav_box', None)
        box = getattr(win, '_perm_apps_box', None)
        if nav is None or box is None:
            return
        counts = self._perm_counts(win)
        nav_rows = []
        kinds = list(PERM_KINDS)
        for index, (kind, label) in enumerate(kinds):
            if kind == 'network':
                detail = '%d blocked' % counts[kind]
            else:
                detail = '%d allowed' % counts[kind]
            row = widgets.list_row(
                label, detail, trailing=widgets.chevron(),
                last=index == len(kinds) - 1)
            row.connect(
                'clicked', lambda _b, k=kind: self._open_perm_view(win, k))
            nav_rows.append(row)
        settings_page._replace_box_children(nav, nav_rows)
        view = getattr(win, '_perm_view', None)
        if isinstance(view, tuple) and view and view[0] == 'app':
            self._render_app_detail(win, box, view[1])
        elif isinstance(view, tuple) and view and view[0] == 'perm':
            self._render_perm_view(win, box, view[1])
        else:
            self._render_app_list(win, box)

    def _render_app_list(self, win, box) -> None:
        from xx_ui import widgets

        entries = list(_entries(win))
        rows = []
        for index, entry in enumerate(entries):
            name = _display_name(win, entry)
            tag = ('Flatpak app' if _is_flatpak(entry)
                   else 'Native app — no limits')
            row = widgets.list_row(
                name, tag, trailing=widgets.chevron(),
                last=index == len(entries) - 1)
            row.connect(
                'clicked',
                lambda _b, e=entry: self._open_app_view(win, e.app_id))
            rows.append(row)
        settings_page._replace_box_children(
            box, rows or [settings_page._section_placeholder('No apps.')])

    def _render_perm_view(self, win, box, kind: str) -> None:
        rows = []
        if kind == 'network':
            try:
                denied = list(getattr(win.config, 'network_denied_apps',
                                      None) or ())
            except Exception:
                denied = []
            for app_id in denied:
                rows.append(settings_page._section_placeholder(
                    '%s — blocked' % app_id))
        else:
            maps = getattr(win, '_perm_maps', None)
            table = maps.get(kind) if isinstance(maps, dict) else None
            if isinstance(table, dict):
                for app_id in sorted(table):
                    state = stored_state(maps, kind, app_id)
                    text = {True: 'allowed', False: 'denied'}.get(
                        state, 'ask')
                    rows.append(settings_page._section_placeholder(
                        '%s — %s' % (app_id, text)))
            elif maps is None:
                rows.append(settings_page._section_placeholder(
                    'Permission store unavailable.'))
        back = settings_page._text_action(
            'Back to apps', lambda: self._open_app_list(win))
        rows.append(back)
        settings_page._replace_box_children(box, rows or [
            settings_page._section_placeholder('Nobody has this access.'),
            back])

    def _render_app_detail(self, win, box, app_id: str) -> None:
        from xx_ui import widgets

        rows = [widgets.label(app_id, 'xx-title')]
        maps = getattr(win, '_perm_maps', None)
        switches = {}
        kinds = list(PERM_KINDS)
        for index, (kind, label) in enumerate(kinds):
            switch = widgets.make_switch()
            if kind == 'network':
                switch.set_active(not network_denied(win, app_id))
                supported = getattr(win, '_perm_net_supported', None)
                if supported is False:
                    switch.set_sensitive(False)
            else:
                switch.set_active(stored_state(maps, kind, app_id) is True)
            switch.connect(
                'notify::active',
                lambda sw, _p, k=kind: self._on_perm_flipped(win, app_id, k, sw))
            switches[kind] = switch
            rows.append(widgets.setting_row(
                label, '', switch, last=index == len(kinds) - 1))
            if kind == 'network' and getattr(
                    win, '_perm_net_supported', None) is False:
                rows.append(settings_page._section_placeholder(
                    'Needs the helper service.'))
        win._perm_detail_switches = switches
        rows.append(settings_page._text_action(
            'Reset all to ask', lambda: self._on_reset_app(win, app_id)))
        rows.append(settings_page._text_action(
            'Back to apps', lambda: self._open_app_list(win)))
        settings_page._replace_box_children(box, rows)

    def _open_app_list(self, win) -> None:
        win._perm_view = None
        self._render_apps(win)

    def _open_app_view(self, win, app_id: str) -> None:
        win._perm_view = ('app', app_id)
        self._render_apps(win)

    def _open_perm_view(self, win, kind: str) -> None:
        win._perm_view = ('perm', kind)
        self._render_apps(win)

    def _on_perm_flipped(self, win, app_id: str, kind: str, switch) -> None:
        if getattr(win, '_perm_updating', False):
            return
        allowed = bool(switch.get_active())
        if kind == 'network':
            if getattr(win, '_perm_net_supported', None) is False:
                return
            set_network_denied(win, app_id, not allowed)
            self._render_apps(win)
            return
        try:
            run_bg = win._run_bg
        except AttributeError:
            return
        run_bg(lambda: apply_store_perm(kind, app_id, allowed),
               lambda _result: self.refresh(win))

    def _on_reset_app(self, win, app_id: str) -> None:
        set_network_denied(win, app_id, False)
        try:
            run_bg = win._run_bg
        except AttributeError:
            self._render_apps(win)
            return
        run_bg(lambda: reset_store_perms(app_id),
               lambda _result: self.refresh(win))
