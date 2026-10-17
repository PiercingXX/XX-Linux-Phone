"""People page: the contacts cache sectioned for the engine.

Sections come from the warm SQLite mirror (kept by
``ContactsClient`` from xx-contacts, or edited locally when that
service is absent): Starred, Business, Blocked, then Everyone. Every
contact appears exactly once; a blocked contact sits in Blocked even
when also starred, because Block beats Star in the engine. Tapping a
row opens a sheet with Call, Message, and the tier switches — tag
changes go through the ``ContactsClient`` (atomic ``UpdateTags``
deltas when the service is present, recorded pending edits when
absent); a number
with no contact behind it blocks into the local block table.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GObject, Gtk, Pango  # noqa: E402

from xx_dialer import policy, recents, t9  # noqa: E402
from xx_dialer.store import BLOCKED_GROUP, BIZ_TIER, Store  # noqa: E402


@dataclass
class PeopleDeps:
    db_file: Path
    home: Path | None = None
    voice: object = None
    contacts: object = None
    launch_sms: object = None
    notice: object = None


@dataclass
class Person:
    uid: str
    display_name: str
    numbers: list[str] = field(default_factory=list)
    starred: bool = False
    business: bool = False
    blocked: bool = False

    @property
    def primary(self) -> str:
        return self.numbers[0] if self.numbers else ""


@dataclass
class PeopleSnapshot:
    sections: list[tuple[str, list[Person]]] = field(default_factory=list)
    contacts_present: bool = False
    empty_reason: str = ""


def section_people(store: Store) -> list[tuple[str, list[Person]]]:
    """Mirror + tiers + stars into Starred/Business/Blocked/Everyone."""
    tiers: dict[str, set[str]] = {}
    for key, tier, _added in store.all_tiers():
        tiers.setdefault(key, set()).add(tier)
    stars = set(store.all_starred())
    by_uid: dict[str, Person] = {}
    for row in store.all_mirror():
        person = by_uid.get(row.lookup_key)
        if person is None:
            person = Person(uid=row.lookup_key,
                            display_name=row.display_name)
            by_uid[row.lookup_key] = person
        if row.e164 not in person.numbers:
            person.numbers.append(row.e164)
        if not person.display_name and row.display_name:
            person.display_name = row.display_name
        person.starred = person.starred or row.starred \
            or row.lookup_key in stars
    for uid, person in by_uid.items():
        names = tiers.get(uid, set())
        person.business = BIZ_TIER in names
        person.blocked = BLOCKED_GROUP in names
        person.numbers.sort()
    ordered = sorted(by_uid.values(),
                     key=lambda item: (item.display_name or
                                       item.primary).lower())
    starred = [item for item in ordered if item.starred
               and not item.blocked]
    business = [item for item in ordered if item.business
                and not item.starred and not item.blocked]
    blocked = [item for item in ordered if item.blocked]
    everyone = [item for item in ordered if not item.starred
                and not item.business and not item.blocked]
    return [("Starred", starred), ("Business", business),
            ("Blocked", blocked), ("Everyone", everyone)]


def person_marks(person: Person) -> str:
    marks = ""
    if person.starred:
        marks += "* "
    if person.business:
        marks += "Biz "
    if person.blocked:
        marks += "Blocked "
    return marks.strip()


class PeopleController:
    """Store-backed People state plus the sheet actions."""

    def __init__(self, deps: PeopleDeps) -> None:
        self._deps = deps
        self._snapshot = PeopleSnapshot()

    def _notice(self, text: str) -> None:
        if self._deps.notice is not None:
            try:
                self._deps.notice(text)
            except Exception:
                pass

    def load(self) -> PeopleSnapshot:
        with Store(self._deps.db_file) as store:
            sections = section_people(store)
            empty = all(not people for _title, people in sections)
        client = self._deps.contacts
        present = bool(client is not None and getattr(
            client, "is_present", False))
        if empty and not present:
            reason = ("Contacts are unavailable: the contacts app is not "
                      "installed or not running.")
        elif empty:
            reason = "No contacts yet."
        else:
            reason = ""
        self._snapshot = PeopleSnapshot(
            sections=sections, contacts_present=present,
            empty_reason=reason)
        return self._snapshot

    def find(self, uid: str) -> Person | None:
        for _title, people in self._snapshot.sections:
            for person in people:
                if person.uid == uid:
                    return person
        return None

    def _client(self):
        return self._deps.contacts

    def _region(self) -> str:
        try:
            from xx_dialer import config as app_config

            return policy.region_of(app_config.load(self._deps.home))
        except Exception:
            return "US"

    def set_star(self, person: Person, starred: bool) -> None:
        client = self._client()
        try:
            if client is not None:
                client.set_star(person.uid, starred)
            else:
                with Store(self._deps.db_file) as store:
                    if starred:
                        store.add_star(person.uid)
                    else:
                        store.remove_star(person.uid)
        except Exception:
            self._notice("Could not update the star.")
            return
        self.load()

    def set_business(self, person: Person, business: bool) -> None:
        client = self._client()
        try:
            if client is not None:
                client.set_business(person.uid, business)
            else:
                with Store(self._deps.db_file) as store:
                    if business:
                        store.upsert_tier(person.uid, BIZ_TIER)
                    else:
                        store.remove_tier(person.uid, BIZ_TIER)
        except Exception:
            self._notice("Could not update Business.")
            return
        self.load()

    def set_blocked(self, person: Person, blocked: bool) -> None:
        client = self._client()
        try:
            if client is not None:
                client.set_blocked(person.uid, blocked)
            else:
                with Store(self._deps.db_file) as store:
                    if blocked:
                        store.upsert_tier(person.uid, BLOCKED_GROUP)
                        for number in person.numbers:
                            store.add_blocked(number)
                    else:
                        store.remove_tier(person.uid, BLOCKED_GROUP)
                        for number in person.numbers:
                            store.remove_blocked(number)
        except Exception:
            self._notice("Could not update the block.")
            return
        self._notice("Blocked %s" % (person.display_name or
                                     person.primary) if blocked
                     else "Unblocked %s" % (person.display_name or
                                            person.primary))
        self.load()

    def block_number(self, number: str) -> None:
        """Block a number with no contact behind it: local block table."""
        normalized = policy.canonical_number(number, self._region()) \
            or number.strip()
        if not normalized:
            return
        client = self._client()
        uid = None
        try:
            with Store(self._deps.db_file) as store:
                row = store.find_mirror(normalized)
                uid = row.lookup_key if row is not None else None
        except Exception:
            uid = None
        if uid is not None and client is not None:
            try:
                client.set_blocked(uid, True)
                self._notice("Blocked %s"
                             % recents.format_number(normalized))
                self.load()
                return
            except Exception:
                pass
        try:
            with Store(self._deps.db_file) as store:
                store.add_blocked(normalized)
        except Exception:
            pass
        self._notice("Blocked %s" % recents.format_number(normalized))
        self.load()

    def call(self, person: Person, number: str | None = None) -> None:
        target = number or person.primary
        if not target:
            self._notice("This contact has no number to call.")
            return
        voice = self._deps.voice
        if voice is None:
            self._notice("No modem is available to place the call.")
            return

        def _done(ok: bool, detail) -> None:
            if not ok:
                self._notice("Could not place the call: %s"
                             % (detail or "modem error"))

        try:
            voice.place(target, _done)
        except Exception as err:
            self._notice("Could not place the call: %s" % (err,))

    def message(self, person: Person, number: str | None = None) -> None:
        target = number or person.primary
        if not target:
            self._notice("This contact has no number to message.")
            return
        launcher = self._deps.launch_sms
        try:
            if launcher is not None:
                launcher("sms:%s" % target)
            else:
                Gio.AppInfo.launch_default_for_uri("sms:%s" % target,
                                                   None)
        except Exception:
            self._notice("No messaging app is available.")


class PeopleView:
    """The People page widget."""

    def __init__(self, controller: PeopleController) -> None:
        self.controller = controller
        self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             spacing=6)
        self._root.set_margin_start(12)
        self._root.set_margin_end(12)
        self._root.set_margin_top(8)
        self._root.set_margin_bottom(8)
        self._build()
        self.refresh()

    @property
    def widget(self) -> Gtk.Widget:
        return self._scroll

    def _build(self) -> None:
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_vexpand(True)
        self._scroll.set_child(self._root)
        self._empty = Gtk.Label(label="", xalign=0.5)
        self._empty.add_css_class("xx-muted")
        self._empty.set_wrap(True)
        self._root.append(self._empty)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        self._store = Gio.ListStore(item_type=PeopleItem)
        selection = Gtk.NoSelection(model=self._store)
        self._list = Gtk.ListView(model=selection, factory=factory)
        self._list.set_single_click_activate(True)
        self._list.connect("activate", self._on_activate)
        self._root.append(self._list)

    def refresh(self) -> None:
        snapshot = self.controller.load()
        while self._store.get_n_items():
            self._store.remove(0)
        for title, people in snapshot.sections:
            if not people:
                continue
            header = PeopleItem()
            header.kind = "header"
            header.title = "%s (%d)" % (title, len(people))
            self._store.append(header)
            for person in people:
                item = PeopleItem()
                item.kind = "item"
                item.uid = person.uid
                item.title = person.display_name or person.primary
                item.number = person.primary
                item.meta = person_marks(person)
                self._store.append(item)
        self._empty.set_visible(self._store.get_n_items() == 0)
        if self._store.get_n_items() == 0:
            self._empty.set_text(snapshot.empty_reason or
                                 "No contacts yet.")

    # ---- factory ----------------------------------------------------------

    def _on_setup(self, _factory, list_item) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                       spacing=8)
        avatar = Gtk.Label(label="", xalign=0.5)
        avatar.set_size_request(40, 40)
        name = Gtk.Label(label="", xalign=0.0, hexpand=True)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        meta = Gtk.Label(label="", xalign=1.0)
        meta.add_css_class("xx-muted")
        root.append(avatar)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.append(name)
        sub = Gtk.Label(label="", xalign=0.0)
        sub.add_css_class("xx-muted")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        text.append(sub)
        root.append(text)
        root.append(meta)
        list_item.set_child(root)
        list_item.xx_parts = (name, sub, meta, avatar)

    def _on_bind(self, _factory, list_item) -> None:
        item = list_item.get_item()
        name, sub, meta, avatar = list_item.xx_parts
        if item.kind == "header":
            name.set_text(item.title)
            sub.set_text("")
            meta.set_text("")
            avatar.set_text("")
            list_item.get_child().person_uid = None
        else:
            name.set_text(item.title)
            sub.set_text(item.number)
            meta.set_text(item.meta)
            avatar.set_text(t9.initials(item.title))
            list_item.get_child().person_uid = item.uid

    def _on_activate(self, _view, position: int) -> None:
        item = self._store.get_item(position)
        if item is None or item.kind != "item":
            return
        person = self.controller.find(item.uid)
        if person is None:
            return
        anchor = self._list
        self._open_sheet(person, anchor)

    def _open_sheet(self, person: Person, anchor: Gtk.Widget) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        name = Gtk.Label(label=person.display_name or person.primary,
                         xalign=0.0)
        numbers = Gtk.Label(
            label="\n".join(person.numbers) or "No numbers",
            xalign=0.0)
        numbers.add_css_class("xx-muted")
        numbers.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(name)
        box.append(numbers)
        star = Gtk.Switch()
        star.set_can_focus(False)
        star.set_active(person.starred)
        business = Gtk.Switch()
        business.set_can_focus(False)
        business.set_active(person.business)
        blocked = Gtk.Switch()
        blocked.set_can_focus(False)
        blocked.set_active(person.blocked)
        for switch, label in ((star, "Starred"), (business, "Business"),
                              (blocked, "Blocked")):
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=8)
            text = Gtk.Label(label=label, xalign=0.0, hexpand=True)
            text.set_ellipsize(Pango.EllipsizeMode.END)
            line.append(text)
            line.append(switch)
            box.append(line)

        star.connect("notify::active", self._on_sheet_star, person)
        business.connect("notify::active", self._on_sheet_business,
                         person)
        blocked.connect("notify::active", self._on_sheet_blocked,
                        person)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=8)
        call = Gtk.Button(label="Call")
        call.connect("clicked",
                     lambda _btn: self.controller.call(person))
        message = Gtk.Button(label="Message")
        message.connect("clicked",
                        lambda _btn: self.controller.message(person))
        actions.append(call)
        actions.append(message)
        box.append(actions)
        popover = Gtk.Popover()
        popover.set_parent(anchor)
        point = Gdk.Rectangle()
        point.x, point.y, point.width, point.height = 0, 0, 1, 1
        popover.set_pointing_to(point)
        popover.set_child(box)
        popover.popup()

    def _on_sheet_star(self, switch: Gtk.Switch, _spec,
                       person: Person) -> None:
        self.controller.set_star(person, switch.get_active())
        self.refresh()

    def _on_sheet_business(self, switch: Gtk.Switch, _spec,
                           person: Person) -> None:
        self.controller.set_business(person, switch.get_active())
        self.refresh()

    def _on_sheet_blocked(self, switch: Gtk.Switch, _spec,
                          person: Person) -> None:
        self.controller.set_blocked(person, switch.get_active())
        self.refresh()


class PeopleItem(GObject.Object):
    __gtype_name__ = "XxPeopleItem"

    kind = GObject.Property(type=str, default="item")
    uid = GObject.Property(type=str, default="")
    title = GObject.Property(type=str, default="")
    number = GObject.Property(type=str, default="")
    meta = GObject.Property(type=str, default="")


def build_people(deps: PeopleDeps) -> PeopleView:
    return PeopleView(PeopleController(deps))
