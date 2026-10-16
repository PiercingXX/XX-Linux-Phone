"""Battery saver & app standby settings (UI over app_standby).

Saver toggle, auto-on threshold, what the saver does, the freeze timeout
(5/15/30 min or never), per-app never-freeze exemptions picked from the
drawer, the built-in exemptions (read-only), and the live frozen count.
"""
from __future__ import annotations

import settings_page

TIMEOUT_CHOICES = ((5, '5 min'), (15, '15 min'), (30, '30 min'), (0, 'Never'))
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
        from gi.repository import Gtk, Pango

        saver_card = settings_page._section_card(spacing=8)

        saver_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        saver_label = Gtk.Label(label='Battery saver', xalign=0)
        saver_label.set_hexpand(True)
        saver_label.set_wrap(True)
        saver_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        saver_label.set_max_width_chars(18)
        saver_switch = Gtk.Switch()
        saver_switch.set_active(saver_is_on(win))
        saver_switch.connect(
            'notify::active',
            lambda sw, _p: self._on_saver_flipped(win, sw))
        win._saver_switch = saver_switch
        saver_row.append(saver_label)
        saver_row.append(saver_switch)

        auto_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        auto_label = Gtk.Label(label='Auto-on at low battery', xalign=0)
        auto_label.set_hexpand(True)
        auto_label.set_wrap(True)
        auto_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        auto_label.set_max_width_chars(18)
        auto_switch = Gtk.Switch()
        auto_switch.set_active(bool(_cfg(win, 'battery_saver_auto', True)))
        auto_switch.connect(
            'notify::active',
            lambda sw, _p: self._on_auto_flipped(win, sw))
        win._saver_auto_switch = auto_switch
        auto_row.append(auto_label)
        auto_row.append(auto_switch)

        threshold_btn = Gtk.Button(label=self._threshold_text(win))
        threshold_btn.add_css_class('flat')
        threshold_btn.add_css_class('action-link')
        settings_page._shrink_button_label(threshold_btn)
        threshold_btn.connect(
            'clicked', lambda btn: self._pick_threshold(win, btn))
        win._saver_threshold_btn = threshold_btn

        saver_card.append(saver_row)
        saver_card.append(auto_row)
        saver_card.append(threshold_btn)
        for effect in SAVER_EFFECTS:
            line = Gtk.Label(label='· ' + effect, xalign=0)
            line.add_css_class('dim-label')
            line.set_wrap(True)
            line.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            line.set_max_width_chars(26)
            saver_card.append(line)
        frozen_label = Gtk.Label(label=_frozen_text(win), xalign=0)
        frozen_label.add_css_class('dim-label')
        frozen_label.set_wrap(True)
        frozen_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        frozen_label.set_max_width_chars(26)
        win._standby_frozen_label = frozen_label
        saver_card.append(frozen_label)

        standby_card = settings_page._section_card(spacing=8)
        timeout_btn = Gtk.Button(label=self._timeout_text(win))
        timeout_btn.add_css_class('flat')
        timeout_btn.add_css_class('action-link')
        settings_page._shrink_button_label(timeout_btn)
        timeout_btn.connect(
            'clicked', lambda btn: self._pick_timeout(win, btn))
        win._standby_timeout_btn = timeout_btn
        standby_card.append(timeout_btn)

        exempt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        win._standby_exempt_box = exempt_box
        standby_card.append(exempt_box)

        add_btn = Gtk.Button(label='Never freeze an app…')
        add_btn.add_css_class('flat')
        add_btn.add_css_class('action-link')
        settings_page._shrink_button_label(add_btn)
        add_btn.connect('clicked', lambda _b: self._on_add_exempt(win))
        standby_card.append(add_btn)

        builtin_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        try:
            from app_standby import builtin_exemption_labels
            labels = builtin_exemption_labels()
        except Exception:
            labels = ()
        for label_text in labels:
            line = Gtk.Label(label='· %s (always)' % label_text, xalign=0)
            line.add_css_class('dim-label')
            line.set_wrap(True)
            line.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            line.set_max_width_chars(26)
            builtin_box.append(line)
        standby_card.append(builtin_box)

        self._render_exempt(win)
        return [settings_page._section_title('Battery saver'), saver_card,
                settings_page._section_title('App standby'), standby_card]

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
        btn = getattr(win, '_saver_threshold_btn', None)
        if btn is not None:
            try:
                btn.set_label(self._threshold_text(win))
            except Exception:
                pass

    def _pick_threshold(self, win, anchor) -> None:
        actions = getattr(win, 'item_actions', None)
        if actions is None:
            return
        choices = [('%d%%' % pct,
                    lambda pct=pct: self._set_threshold(win, pct))
                   for pct in THRESHOLD_CHOICES]
        actions._show_action_menu(anchor, 'Auto-on level', choices)

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

    def _render_timeout(self, win) -> None:
        btn = getattr(win, '_standby_timeout_btn', None)
        if btn is not None:
            try:
                btn.set_label(self._timeout_text(win))
            except Exception:
                pass

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
        from gi.repository import Gtk, Pango

        box = getattr(win, '_standby_exempt_box', None)
        if box is None:
            return
        rows = []
        for app_id in exempt_ids(win):
            name = Gtk.Label(label=display_name(win, app_id), xalign=0)
            name.add_css_class('dim-label')
            name.set_hexpand(True)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(18)
            remove = settings_page._link_button('Remove', 'dim-label')
            remove.connect(
                'clicked',
                lambda _b, aid=app_id: self._on_remove_exempt(win, aid))
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
