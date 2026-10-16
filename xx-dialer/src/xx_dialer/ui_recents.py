"""Recents page: call log with grouping, chips and verdict lines.

Consumes the pure shapes in ``recents.py`` over the warm SQLite store.
Lists follow the phone rules: ``Gtk.ListView`` with
``set_single_click_activate(True)`` over a ``Gtk.NoSelection`` model
(single click calls back), long-press menus via one ``Gtk.GestureLongPress``
that resolves the row under the pointer (``pick`` + walk up), never the
selection. Call rows carry Android-style millisecond ids, so the model
item keeps them in int64 properties (``GObject.TYPE_INT64``).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GObject, Gtk, Pango  # noqa: E402

from xx_dialer import contacts_client, policy, recents  # noqa: E402
from xx_dialer.store import (  # noqa: E402
    CALL_TYPE_INCOMING,
    CALL_TYPE_MISSED,
    CALL_TYPE_OUTGOING,
    Store,
)


@dataclass
class RecentsDeps:
    db_file: Path
    home: Path | None = None
    voice: object = None
    contacts: object = None
    launch_sms: object = None
    copy_text: object = None
    notice: object = None
    now_ms: object = None


def _default_launch_sms(uri: str) -> None:
    Gio.AppInfo.launch_default_for_uri(uri, None)


def _default_copy_text(text: str) -> None:
    display = Gdk.Display.get_default()
    if display is None:
        raise RuntimeError("no display for clipboard")
    display.get_clipboard().set(text)


class RecentItem(GObject.Object):
    __gtype_name__ = "XxRecentItem"

    kind = GObject.Property(type=str, default="item")
    row_id = GObject.Property(type=GObject.TYPE_INT64, default=0)
    date = GObject.Property(type=GObject.TYPE_INT64, default=0)
    title = GObject.Property(type=str, default="")
    number = GObject.Property(type=str, default="")
    when = GObject.Property(type=str, default="")
    chip = GObject.Property(type=str, default="")
    meta = GObject.Property(type=str, default="")
    verdict = GObject.Property(type=str, default="")
    count = GObject.Property(type=int, default=0)
    expanded = GObject.Property(type=bool, default=False)
    identity = GObject.Property(type=str, default="")


class RecentsController:
    """Store-backed Recents state plus every row action.

    The view is thin: it renders ``items`` and forwards gestures here.
    All side effects run through the injected deps, so headless tests
    drive this with fakes and a real seeded store.
    """

    FILTERS = (recents.FILTER_ALL, recents.FILTER_MISSED,
               recents.FILTER_SILENCED, recents.FILTER_BLOCKED)

    def __init__(self, deps: RecentsDeps) -> None:
        self._deps = deps
        self.filter_name = recents.FILTER_ALL
        self.deeplink: str | None = None
        self.grouping_on = True
        self.expanded: set[str] = set()
        self.merged: list[recents.MergedCall] = []
        self.starred: list[tuple[str, str, str]] = []
        self.unread = 0
        self._by_key: dict[int, recents.MergedCall] = {}

    # ---- loading -------------------------------------------------------

    def _now_ms(self) -> int:
        if self._deps.now_ms is not None:
            return int(self._deps.now_ms())
        return int(time.time() * 1000)

    def load(self) -> None:
        with Store(self._deps.db_file) as store:
            try:
                from xx_dialer import config as app_config

                self.grouping_on = bool(app_config.load(
                    self._deps.home).get("group_recents", True))
            except Exception:
                self.grouping_on = True
            call_rows = store.recent_calls()
            screen_rows = store.last_screened(400)
            mirror = store.all_mirror()
            viewed_at = store.get_recents_viewed()
        names = {row.e164: row.display_name for row in mirror
                 if row.display_name}
        outgoing: dict[str, list[int]] = {}
        platform = []
        for row in call_rows:
            e164 = policy.normalize_number(row.number or "")
            missed = row.call_type == CALL_TYPE_MISSED \
                or (row.call_type == CALL_TYPE_INCOMING
                    and (row.duration or 0) <= 0)
            platform.append(recents.PlatformCall(
                key=row.row_id, time_ms=row.date, raw=row.number,
                e164=e164, cached_name=row.cached_name, missed=missed,
                outgoing=row.call_type == CALL_TYPE_OUTGOING,
                duration_s=row.duration or 0))
            if row.call_type == CALL_TYPE_OUTGOING and e164:
                outgoing.setdefault(e164, []).append(row.date)
        logs = [recents.ScreenEntry(
            row_id=row.row_id, at=row.at, e164=row.e164,
            verdict=row.verdict, reason=row.reason, mode=row.mode,
            answered=row.answered) for row in screen_rows]
        answered_by_screen = {entry.row_id: entry.answered
                              for entry in logs}
        self.merged = recents.merge(platform, logs)
        for call in self.merged:
            if call.display_name is None and call.e164:
                call.display_name = names.get(call.e164)
            if (call.screen_id is not None
                    and answered_by_screen.get(call.screen_id)
                    and not call.outgoing):
                call.missed = False
            if call.e164 and call.e164 in outgoing:
                weekday = recents.recent_outgoing_weekday(
                    outgoing[call.e164], call.time_ms)
                if weekday and call.reason.startswith("you called them"):
                    call.reason = "you called them %s" % weekday
        self._by_key = {call.key: call for call in self.merged}
        self.starred = recents.starred_strip(mirror)
        self.unread = recents.unread_missed(self.merged, viewed_at)

    def visible_calls(self) -> list[recents.MergedCall]:
        return [call for call in self.merged
                if recents.passes(self.filter_name, call)
                and (self.deeplink is None
                     or call.identity == self.deeplink)]

    def items(self) -> list[RecentItem]:
        now = self._now_ms()
        out: list[RecentItem] = []
        for grouped in recents.group(self.visible_calls(),
                                     self.grouping_on, self.expanded):
            if isinstance(grouped, recents.Group):
                first = grouped.calls[0]
                item = RecentItem()
                item.kind = "header"
                item.identity = grouped.identity
                item.title = recents.display_name_of(first)
                item.number = grouped.identity
                item.count = len(grouped.calls)
                item.expanded = grouped.expanded
                item.date = first.time_ms
                out.append(item)
                if grouped.expanded:
                    for call in grouped.calls:
                        out.append(self._row_item(call, now))
            else:
                out.append(self._row_item(grouped.call, now))
        return out

    def _row_item(self, call: recents.MergedCall,
                  now_ms: int) -> RecentItem:
        item = RecentItem()
        item.kind = "item"
        item.row_id = call.key
        item.date = call.time_ms
        item.title = recents.display_name_of(call)
        item.number = call.e164 or recents.format_number(call.raw)
        item.when = recents.format_when(call.time_ms, now_ms,
                                        self._local_tz())
        item.chip = recents.direction_chip(call)
        duration = recents.duration_text(call.duration_s, call.missed)
        item.meta = duration
        item.verdict = recents.verdict_line(call.glyph, call.reason,
                                            call.observed)
        if call.identity:
            item.identity = call.identity
        return item

    @staticmethod
    def _local_tz():
        from datetime import datetime

        return datetime.now().astimezone().tzinfo

    def find(self, key: int) -> recents.MergedCall | None:
        return self._by_key.get(key)

    # ---- viewing --------------------------------------------------------

    def mark_viewed(self) -> None:
        if recents.should_clear(True, self.filter_name
                                == recents.FILTER_MISSED):
            try:
                with Store(self._deps.db_file) as store:
                    store.set_recents_viewed(self._now_ms())
            except Exception:
                pass
            self.unread = 0

    def toggle_group(self, identity: str) -> None:
        if identity in self.expanded:
            self.expanded.remove(identity)
        else:
            self.expanded.add(identity)

    # ---- row actions ------------------------------------------------------

    def _notice(self, text: str) -> None:
        if self._deps.notice is not None:
            try:
                self._deps.notice(text)
            except Exception:
                pass

    @staticmethod
    def _dialable(call: recents.MergedCall) -> str | None:
        if call.e164:
            return call.e164
        if call.raw and call.raw.strip():
            return call.raw.strip()
        return None

    def _uid_for(self, call: recents.MergedCall) -> str | None:
        if not call.e164:
            return None
        try:
            with Store(self._deps.db_file) as store:
                row = store.find_mirror(call.e164)
        except Exception:
            return None
        return row.lookup_key if row is not None else None

    def call_back(self, call: recents.MergedCall) -> None:
        number = self._dialable(call)
        if not number:
            self._notice("This entry has no number to call.")
            return
        self.place(number)

    def place(self, number: str) -> None:
        voice = self._deps.voice
        if voice is None:
            self._notice("No modem is available to place the call.")
            return

        def _done(ok: bool, detail) -> None:
            if not ok:
                self._notice("Could not place the call: %s"
                             % (detail or "modem error"))
                return
            self.mark_viewed()

        try:
            voice.place(number, _done)
        except Exception as err:
            self._notice("Could not place the call: %s" % (err,))

    def message(self, call: recents.MergedCall) -> None:
        number = self._dialable(call)
        if not number:
            self._notice("This entry has no number to message.")
            return
        launcher = self._deps.launch_sms or _default_launch_sms
        try:
            launcher("sms:%s" % number)
        except Exception:
            self._notice("No messaging app is available.")

    def copy_number(self, call: recents.MergedCall) -> None:
        number = self._dialable(call)
        if not number:
            return
        copier = self._deps.copy_text or _default_copy_text
        try:
            copier(number)
        except Exception:
            self._notice("Could not copy the number.")
            return
        self._notice("Copied %s" % recents.format_number(number))

    def add_contact(self, call: recents.MergedCall) -> None:
        number = self._dialable(call)
        if not number:
            self._notice("This entry has no number to save.")
            return
        self.add_number_to_contacts(number, call.display_name)

    def add_number_to_contacts(self, number: str,
                               name: str | None = None) -> None:
        client = self._deps.contacts
        if client is None:
            self._notice("Contacts app is not installed.")
            return
        vcard = contacts_client.new_contact_vcard(number, name)

        def _done(_uid: str) -> None:
            self._notice("Contact saved.")
            self.load()

        def _failed(_err) -> None:
            self._notice("Could not save the contact.")

        try:
            client.create_async(vcard, _done, _failed)
        except Exception:
            self._notice("Could not save the contact.")

    def block(self, call: recents.MergedCall) -> None:
        number = self._dialable(call)
        if not number:
            return
        uid = self._uid_for(call)
        client = self._deps.contacts
        if uid is not None and client is not None:
            try:
                client.set_blocked(uid, True)
            except Exception:
                pass
        else:
            try:
                with Store(self._deps.db_file) as store:
                    store.add_blocked(policy.normalize_number(number)
                                      or number)
            except Exception:
                pass
        self._notice("Blocked %s" % recents.format_number(number))
        self.load()

    def ring_next_time(self, call: recents.MergedCall) -> None:
        uid = self._uid_for(call)
        client = self._deps.contacts
        if uid is None or client is None:
            self._notice("No contact for this number — "
                         "save it to contacts first.")
            return
        try:
            client.set_star(uid, True)
        except Exception:
            self._notice("Could not star the contact.")
            return
        self._notice("Starred — the next call will ring.")
        self.load()

    def add_to_business(self, call: recents.MergedCall) -> None:
        uid = self._uid_for(call)
        client = self._deps.contacts
        if uid is None or client is None:
            self._notice("No contact for this number — "
                         "save it to contacts first.")
            return
        try:
            client.set_business(uid, True)
        except Exception:
            self._notice("Could not update the contact.")
            return
        self._notice("Added to Business.")
        self.load()

    def delete(self, call: recents.MergedCall) -> bool:
        try:
            with Store(self._deps.db_file) as store:
                removed = store.delete_call(call.key)
        except Exception:
            removed = False
        if removed:
            self._notice("Deleted.")
            self.load()
        else:
            self._notice("Could not delete this entry.")
        return removed


def _menu_buttons(call: recents.MergedCall,
                  controller: RecentsController) -> list[tuple[str, object]]:
    number = RecentsController._dialable(call)
    entries: list[tuple[str, object]] = []
    if number is not None:
        entries.extend([
            ("Call back", controller.call_back),
            ("Message", controller.message),
            ("Add to contacts", controller.add_contact),
            ("Copy number", controller.copy_number),
            ("Block", controller.block),
        ])
    if call.glyph == recents.GLYPH_SILENCED and call.e164:
        entries.extend([
            ("Ring next time", controller.ring_next_time),
            ("Add to Business", controller.add_to_business),
        ])
    entries.append(("Delete", controller.delete))
    return entries


class RecentsView:
    """The Recents page widget plus its refresh entry points."""

    def __init__(self, controller: RecentsController) -> None:
        self.controller = controller
        self._store = Gio.ListStore(item_type=RecentItem)
        self._suppress_activate = False
        self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             spacing=0)
        self._build()
        self.refresh()

    @property
    def widget(self) -> Gtk.Widget:
        return self._root

    def _build(self) -> None:
        strip_label = Gtk.Label(label="Favorites", xalign=0.0)
        strip_label.add_css_class("xx-muted")
        self._star_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=6)
        star_scroll = Gtk.ScrolledWindow()
        star_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                               Gtk.PolicyType.NEVER)
        star_scroll.set_child(self._star_bar)
        star_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                           spacing=2)
        star_box.append(strip_label)
        star_box.append(star_scroll)
        self._star_wrap = star_box
        self._root.append(star_box)

        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                        spacing=6)
        chips.set_halign(Gtk.Align.CENTER)
        self._chip_buttons: dict[str, Gtk.ToggleButton] = {}
        for name in RecentsController.FILTERS:
            button = Gtk.ToggleButton(label=name.capitalize())
            button.add_css_class("xx-chip")
            if name == recents.FILTER_ALL:
                button.set_active(True)
            button.connect("toggled", self._on_chip, name)
            self._chip_buttons[name] = button
            chips.append(button)
        self._root.append(chips)

        self._banner = Gtk.Label(label="", xalign=0.5)
        self._banner.add_css_class("xx-muted")
        self._root.append(self._banner)

        self._deep_note = Gtk.Button(label="")
        self._deep_note.connect("clicked", self._on_clear_deep)
        self._root.append(self._deep_note)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        selection = Gtk.NoSelection(model=self._store)
        self._list = Gtk.ListView(model=selection, factory=factory)
        self._list.set_single_click_activate(True)
        self._list.connect("activate", self._on_activate)
        long_press = Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_long_press)
        self._list.add_controller(long_press)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(self._list)
        self._root.append(scroll)

        self._empty = Gtk.Label(label="No calls yet.")
        self._empty.add_css_class("xx-muted")
        self._root.append(self._empty)

    # ---- factory ----------------------------------------------------------

    def _on_setup(self, _factory, list_item) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       spacing=2)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                         spacing=6)
        arrow = Gtk.Label(label="▸", xalign=0.0)
        title = Gtk.Label(label="", xalign=0.0, hexpand=True)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        count = Gtk.Label(label="", xalign=1.0)
        count.add_css_class("xx-muted")
        header.append(arrow)
        header.append(title)
        header.append(count)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                      spacing=6)
        name = Gtk.Label(label="", xalign=0.0, hexpand=True)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        when = Gtk.Label(label="", xalign=1.0)
        when.add_css_class("xx-muted")
        top.append(name)
        top.append(when)
        sub = Gtk.Label(label="", xalign=0.0)
        sub.add_css_class("xx-muted")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        verdict = Gtk.Label(label="", xalign=0.0)
        verdict.add_css_class("xx-muted")
        verdict.set_wrap(True)
        root.append(header)
        root.append(top)
        root.append(sub)
        root.append(verdict)
        list_item.set_child(root)
        list_item.xx_parts = (root, header, arrow, title, count,
                              top, name, when, sub, verdict)

    def _on_bind(self, _factory, list_item) -> None:
        item = list_item.get_item()
        parts = list_item.xx_parts
        (root, header, arrow, title, count,
         top, name, when, sub, verdict) = parts
        is_header = item.kind == "header"
        header.set_visible(is_header)
        top.set_visible(not is_header)
        sub.set_visible(not is_header)
        verdict.set_visible(not is_header
                            and bool(item.verdict))
        if is_header:
            arrow.set_text("▾" if item.expanded else "▸")
            title.set_text(item.title or item.number)
            count.set_text("×%d" % item.count)
            root.recent_key = "header:%s" % item.identity
        else:
            name.set_text(item.title)
            when.set_text(item.when)
            chip_line = item.chip
            if item.meta:
                chip_line = "%s · %s" % (item.chip, item.meta)
            sub.set_text(chip_line)
            verdict.set_text(item.verdict)
            root.recent_key = "row:%d" % item.row_id

    # ---- gestures -----------------------------------------------------------

    def _on_activate(self, _view, position: int) -> None:
        if self._suppress_activate:
            self._suppress_activate = False
            return
        item = self._store.get_item(position)
        if item is None:
            return
        if item.kind == "header":
            self.controller.toggle_group(item.identity)
            self.refresh()
            return
        call = self.controller.find(int(item.row_id))
        if call is not None:
            self.controller.call_back(call)
            self.refresh()

    def _on_long_press(self, gesture, x: float, y: float) -> None:
        widget = gesture.get_widget()
        target = widget.pick(x, y, Gtk.PickFlags.DEFAULT)
        while target is not None and target is not widget:
            key = getattr(target, "recent_key", None)
            if key is not None:
                break
            target = target.get_parent()
        else:
            target = None
        if target is None:
            return
        self._suppress_activate = True
        self._open_menu(str(key), target)

    def _resolve_key(self, key: str) -> recents.MergedCall | None:
        if key.startswith("row:"):
            try:
                return self.controller.find(int(key[4:]))
            except ValueError:
                return None
        return None

    def _open_menu(self, key: str, anchor: Gtk.Widget) -> None:
        call = self._resolve_key(key)
        if call is None:
            if key.startswith("header:"):
                self.controller.toggle_group(key[len("header:"):])
                self.refresh()
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                      spacing=0)
        popover = Gtk.Popover()
        popover.set_parent(anchor)
        point = Gdk.Rectangle()
        point.x, point.y, point.width, point.height = 0, 0, 1, 1
        popover.set_pointing_to(point)
        popover.set_child(box)
        for label, action in _menu_buttons(call, self.controller):
            button = Gtk.Button(label=label)
            button.set_halign(Gtk.Align.FILL)

            def _clicked(_btn, step=action) -> None:
                popover.popdown()
                step(call)
                self.refresh()

            button.connect("clicked", _clicked)
            box.append(button)
        popover.popup()

    def _on_chip(self, button: Gtk.ToggleButton, name: str) -> None:
        if not button.get_active():
            if self.controller.filter_name == name:
                button.set_active(True)
            return
        for other_name, other in self._chip_buttons.items():
            if other_name != name and other.get_active():
                other.set_active(False)
        self.controller.filter_name = name
        if name == recents.FILTER_MISSED:
            self.controller.mark_viewed()
        self.refresh()

    def _on_clear_deep(self, _button) -> None:
        self.controller.deeplink = None
        self.refresh()

    def _on_star(self, _button, e164: str) -> None:
        self.controller.deeplink = e164
        self.refresh()

    # ---- refresh --------------------------------------------------------------

    def refresh(self) -> None:
        self.controller.load()
        while self._store.get_n_items():
            self._store.remove(0)
        for item in self.controller.items():
            self._store.append(item)
        self._render_chrome()

    def _render_chrome(self) -> None:
        while True:
            child = self._star_bar.get_first_child()
            if child is None:
                break
            self._star_bar.remove(child)
        starred = self.controller.starred
        self._star_wrap.set_visible(bool(starred))
        for uid, name, e164 in starred:
            from xx_dialer import t9

            label = "%s\n%s" % (t9.initials(name or e164), name or e164)
            button = Gtk.Button(label=label)
            button.set_tooltip_text(name or e164)
            button.connect("clicked", self._on_star, e164)
            self._star_bar.append(button)
        unread = self.controller.unread
        self._banner.set_text(
            recents.missed_title(unread) if unread else "")
        self._banner.set_visible(bool(unread))
        deeplink = self.controller.deeplink
        self._deep_note.set_visible(deeplink is not None)
        if deeplink is not None:
            self._deep_note.set_label(
                "Showing calls with %s — show all"
                % recents.format_number(deeplink))
        empty = self._store.get_n_items() == 0
        self._empty.set_visible(empty)
        if empty:
            if self.controller.merged:
                self._empty.set_text("No calls match this filter.")
            else:
                self._empty.set_text("No calls yet.")


def build_recents(controller: RecentsController) -> RecentsView:
    return RecentsView(controller)
