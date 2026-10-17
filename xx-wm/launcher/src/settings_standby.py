"""Battery saver & app standby settings (UI over app_standby).

Saver toggle, auto-on threshold, what the saver does, the freeze timeout
(5/15/30 min or never), per-app never-freeze exemptions picked from the
drawer, the built-in exemptions (read-only), and the live frozen count.
"""
from __future__ import annotations

import settings_page

TIMEOUT_CHOICES = ((5, '5 min'), (15, '15 min'), (30, '30 min'),
                   (0, 'Never'))
THRESHOLD_CHOICES = (10, 15, 20, 25)
SAVER_EFFECTS = (
    'Shorter wait before unused apps freeze.',
    'Freezes 30 s after blank instead of 2 min.',
    'Pauses auto weather and update checks.',
)


def _cfg(win, name: str, default):
    try:
        value = getattr(getattr(win, 'config', None), name, default)
    except Exception:
        return default
    return default if value is None else value


def _controller(win):
    ctl = getattr(win, 'standby', None)
    if ctl is not None:
        return ctl
    try:
        from app_standby import get_standby
        return get_standby()
    except Exception:
        return None


def saver_is_on(win) -> bool:
    ctl = _controller(win)
    if ctl is not None:
        try:
            return bool(ctl.saver_effective)
        except Exception:
            pass
    return bool(_cfg(win, 'battery_saver', False))


def frozen_count(win) -> int | None:
    ctl = _controller(win)
    if ctl is None:
        return None
    try:
        return len(ctl.frozen_apps)
    except Exception:
        return None


def exempt_ids(win) -> list:
    try:
        return list(getattr(win.config, 'standby_exempt', None) or ())
    except Exception:
        return []


def display_name(win, app_id: str) -> str:
    try:
        entries = getattr(getattr(win, 'app_index', None), 'entries', None) or ()
        for entry in entries:
            if getattr(entry, 'app_id', None) == app_id:
                try:
                    return win.config.label_for(entry.app_id, entry.name)
                except Exception:
                    return entry.name
    except Exception:
        pass
    return str(app_id)


def _frozen_text(win) -> str:
    count = frozen_count(win)
    if count is None:
        return 'Frozen now: checking…'
    return 'Frozen now: %d' % count


class StandbySection(settings_page._Section):
    key = 'standby'
    title = 'Battery saver'

    def build(self, win) -> list:
        from gi.repository import Gtk
        from xx_ui import widgets

        saver_head = settings_page._section_title('Battery saver')
        saver_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        saver_switch = widgets.make_switch(saver_is_on(win))
        saver_switch.connect(
            'notify::active',
            lambda sw, _p: self._on_saver_flipped(win, sw))
        win._saver_switch = saver_switch
        saver_body.append(widgets.setting_row('Battery saver', '', saver_switch))
        auto_switch = widgets.make_switch(
            bool(_cfg(win, 'battery_saver_auto', True)))
        auto_switch.connect(
            'notify::active',
            lambda sw, _p: self._on_auto_flipped(win, sw))
        win._saver_auto_switch = auto_switch
        saver_body.append(widgets.setting_row(
            'Auto-on at low battery', self._threshold_text(win), auto_switch))
        self._threshold_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8)
        saver_body.append(self._threshold_box)
        self._render_threshold(win)
        for effect in SAVER_EFFECTS:
            saver_body.append(
                settings_page._section_placeholder('· ' + effect))
        frozen_label = settings_page._section_placeholder(_frozen_text(win))
        win._standby_frozen_label = frozen_label
        saver_body.append(frozen_label)

        standby_head = settings_page._section_title('App standby')
        standby_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._timeout_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8)
        standby_body.append(self._timeout_box)
        self._render_timeout(win)

        exempt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win._standby_exempt_box = exempt_box
        standby_body.append(exempt_box)

        standby_body.append(settings_page._outline_action(
            'Never freeze an app…', lambda: self._on_add_exempt(win)))

        try:
            from app_standby import builtin_exemption_labels
            labels = builtin_exemption_labels()
        except Exception:
            labels = ()
        for label_text in labels:
            standby_body.append(settings_page._section_placeholder(
                '· %s (always)' % label_text))

        self._render_exempt(win)
        return [saver_head, saver_body, standby_head, standby_body]

    def refresh(self, win) -> None:
        self._sync_switches(win)
        self._render_threshold(win)
        self._render_timeout(win)
        self._render_exempt(win)
        self._render_frozen(win)

    def _sync_switches(self, win) -> None:
        win._standby_updating = True
        try:
            switch = getattr(win, '_saver_switch', None)
            if switch is not None:
                switch.set_active(saver_is_on(win))
            auto = getattr(win, '_saver_auto_switch', None)
            if auto is not None:
                auto.set_active(bool(_cfg(win, 'battery_saver_auto', True)))
        finally:
            win._standby_updating = False

    def _on_saver_flipped(self, win, switch) -> None:
        if getattr(win, '_standby_updating', False):
            return
        on = bool(switch.get_active())
        ctl = _controller(win)
        if ctl is not None:
            try:
                ctl.set_saver(on)
            except Exception:
                pass
        try:
            win.config.set_battery_saver(on)
        except Exception:
            pass
        self._render_frozen(win)

    def _on_auto_flipped(self, win, switch) -> None:
        if getattr(win, '_standby_updating', False):
            return
        try:
            win.config.set_battery_saver_auto(bool(switch.get_active()))
        except Exception:
            pass

    def _threshold_text(self, win) -> str:
        return 'Auto-on at %d%%' % _threshold_pct(win)

    def _render_threshold(self, win) -> None:
        box = getattr(self, '_threshold_box', None)
        if box is None:
            return
        from xx_ui import widgets
        current = _threshold_pct(win)
        settings_page._replace_box_children(box, [widgets.segmented([
            ('%d%%' % pct, pct == current,
             lambda pct=pct: self._set_threshold(win, pct))
            for pct in THRESHOLD_CHOICES])])

    def _set_threshold(self, win, pct: int) -> None:
        try:
            win.config.set_battery_saver_auto_pct(pct)
        except Exception:
            pass
        self._render_threshold(win)

    def _timeout_text(self, win) -> str:
        minutes = _standby_minutes(win)
        for value, label in TIMEOUT_CHOICES:
            if value == minutes:
                return 'Freeze after: %s' % label
        return 'Freeze after: %d min' % minutes

    def _timeout_label(self, win) -> str:
        minutes = _standby_minutes(win)
        for value, label in TIMEOUT_CHOICES:
            if value == minutes:
                return label
        return '%d min' % minutes

    def _render_timeout(self, win) -> None:
        # Five timeout values cannot fit one segmented row (minimum
        # 383 px on a 320 px content), so the standby delay stays a
        # value row opening the same menu — always showing the current
        # delay, including the stock 10 min default the menu omits.
        box = getattr(self, '_timeout_box', None)
        if box is None:
            return
        from xx_ui import widgets
        row = widgets.list_row(
            'Freeze after', self._timeout_label(win),
            trailing=widgets.chevron(), last=True)
        row.connect(
            'clicked', lambda _b, r=row: self._pick_timeout(win, r))
        settings_page._replace_box_children(box, [row])

    def _pick_timeout(self, win, anchor) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        choices = [(label, lambda minutes=minutes: self._set_timeout(win, minutes))
                   for minutes, label in TIMEOUT_CHOICES]
        actions._show_action_menu(anchor, 'Freeze after', choices)

    def _set_timeout(self, win, minutes: int) -> None:
        try:
            win.config.set_app_standby_minutes(minutes)
        except Exception:
            pass
        self._render_timeout(win)

    def _render_exempt(self, win) -> None:
        from gi.repository import Gtk
        from xx_ui import widgets

        box = getattr(win, '_standby_exempt_box', None)
        if box is None:
            return
        rows = []
        for app_id in exempt_ids(win):
            name = widgets.label(display_name(win, app_id), 'xx-secondary')
            name.set_hexpand(True)
            remove = settings_page._text_action(
                'Remove', lambda aid=app_id: self._on_remove_exempt(win, aid))
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            line.append(name)
            line.append(remove)
            rows.append(line)
        settings_page._replace_box_children(
            box, rows or [settings_page._section_placeholder(
                'Every app may freeze.')])

    def _on_add_exempt(self, win) -> None:
        try:
            win._pick_exempt_mode = True
            win.stack.set_visible_child_name('apps')
            win._show_status('Tap an app to never freeze.')
        except Exception:
            pass

    def _on_remove_exempt(self, win, app_id: str) -> None:
        try:
            from app_standby import normalize_app_id
        except ImportError:
            def normalize_app_id(value: str) -> str:
                text = str(value or '').strip()
                if text.lower().endswith('.desktop'):
                    text = text[:-8]
                return text.casefold()
        try:
            wanted = normalize_app_id(app_id)
            kept = [entry for entry in exempt_ids(win)
                    if normalize_app_id(entry) != wanted]
            win.config.set_standby_exempt(kept)
        except Exception:
            pass
        self._render_exempt(win)

    def _render_frozen(self, win) -> None:
        label = getattr(win, '_standby_frozen_label', None)
        if label is not None:
            try:
                label.set_text(_frozen_text(win))
            except Exception:
                pass


def _threshold_pct(win) -> int:
    try:
        from app_standby import saver_auto_threshold
        return saver_auto_threshold(getattr(win, 'config', None))
    except Exception:
        pass
    return int(_cfg(win, 'battery_saver_auto_pct', 15) or 15)


def _standby_minutes(win) -> int:
    try:
        return max(0, int(getattr(win.config, 'app_standby_minutes', 10)))
    except (AttributeError, TypeError, ValueError):
        return 10
