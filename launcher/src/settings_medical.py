"""Medical ID editor settings section.

Edits the config Medical ID fields (name, blood type, allergies,
medications, conditions, emergency contacts) plus the "Show on lock
screen" switch. Emergency contacts are picked through the xx-contacts
session service ``io.piercingxx.Contacts1`` Search (async, never
call_sync on the main loop); the shapes below mirror its introspection
XML (bus io.piercingxx.Contacts, path /io/piercingxx/Contacts,
`Search(s query, u limit) -> a(sss)` = uid, display name, primary
number — a static test pins them to ref/xx-contacts).
"""
from __future__ import annotations

import settings_page

CONTACTS_BUS = 'io.piercingxx.Contacts'
CONTACTS_PATH = '/io/piercingxx/Contacts'
CONTACTS_IFACE = 'io.piercingxx.Contacts1'
CONTACTS_SEARCH_REPLY = '(a(sss))'

_SEARCH_TIMEOUT_MS = 8_000
_SEARCH_LIMIT = 8

BLOOD_TYPES = ('', 'A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-')

MEDICAL_FIELDS: tuple = (
    ('name', 'Name'),
    ('blood_type', 'Blood type'),
    ('allergies', 'Allergies'),
    ('medications', 'Medications'),
    ('conditions', 'Conditions'),
)


def parse_search_results(value) -> list:
    """a(sss) rows into [(uid, name, number)]; bad shapes give []."""
    rows = []
    if not isinstance(value, (list, tuple)):
        return rows
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            continue
        uid, name, number = (str(part) for part in row)
        if name or number:
            rows.append((uid, name, number))
    return rows


def search_contacts(query: str, on_done, limit: int = _SEARCH_LIMIT) -> None:
    """Async xx-contacts Search: on_done(True, [(uid, name, number)]) or
    (False, error). Missing service degrades to failure, never raises."""
    try:
        from gi.repository import Gio, GLib
    except ImportError as err:
        on_done(False, f'D-Bus unavailable: {err}')
        return

    def _fail(message: str) -> None:
        on_done(False, message)

    def _on_bus(source, result) -> None:
        try:
            bus = Gio.bus_get_finish(result)
        except Exception as err:
            _fail(str(err))
            return

        def _on_reply(conn, reply_result) -> None:
            try:
                reply = conn.call_finish(reply_result)
            except Exception as err:
                _fail(str(err))
                return
            try:
                rows = reply.unpack()[0]
            except Exception as err:
                _fail(str(err))
                return
            on_done(True, parse_search_results(rows))

        try:
            bus.call(
                CONTACTS_BUS, CONTACTS_PATH, CONTACTS_IFACE, 'Search',
                GLib.Variant('(su)', (str(query or ''), int(limit))),
                GLib.VariantType.new(CONTACTS_SEARCH_REPLY),
                Gio.DBusCallFlags.NONE, _SEARCH_TIMEOUT_MS, None, _on_reply,
            )
        except Exception as err:
            _fail(str(err))

    try:
        Gio.bus_get(Gio.BusType.SESSION, None, _on_bus)
    except Exception as err:
        _fail(str(err))


class MedicalSection(settings_page._Section):
    """Settings section: Medical ID editor + lock-screen switch."""

    key = 'medical'
    title = 'Medical ID'

    def __init__(self) -> None:
        self._entries: dict = {}
        self._contacts_box = None
        self._results_box = None
        self._search_entry = None
        self._show_switch = None
        self._note = None
        self._draft_contacts: list = []

    def build(self, win) -> list:
        from settings_page import (_section_card, _section_title,
                                   _settings_row, _shrink_settings_entry)
        from gi.repository import Gtk, Pango

        medical = getattr(win.config, 'medical_id', {})
        card = _section_card(spacing=8)
        for field, title in MEDICAL_FIELDS:
            entry = Gtk.Entry()
            entry.set_placeholder_text(title)
            try:
                entry.set_text(medical.get(field, '')
                               if isinstance(medical, dict) else '')
            except Exception:
                pass
            _shrink_settings_entry(entry)
            self._entries[field] = entry
            card.append(_settings_row(title, entry))

        contacts_title = Gtk.Label(label='Emergency contacts', xalign=0)
        contacts_title.add_css_class('dim-label')
        card.append(contacts_title)
        self._draft_contacts = [
            dict(entry) for entry in (medical.get('emergency_contacts', [])
                                      if isinstance(medical, dict) else [])
            if isinstance(entry, dict)]
        from gi.repository import Gtk as _Gtk
        self._contacts_box = _Gtk.Box(
            orientation=_Gtk.Orientation.VERTICAL, spacing=4)
        card.append(self._contacts_box)

        self._search_entry = Gtk.Entry()
        self._search_entry.set_placeholder_text('Add: search contacts')
        _shrink_settings_entry(self._search_entry)
        self._search_entry.connect('changed', lambda _e: self._on_search())
        card.append(self._search_entry)
        self._results_box = _Gtk.Box(
            orientation=_Gtk.Orientation.VERTICAL, spacing=4)
        card.append(self._results_box)

        switch_row = _Gtk.Box(orientation=_Gtk.Orientation.HORIZONTAL,
                              spacing=8)
        switch_title = Gtk.Label(label='Show on lock screen', xalign=0)
        switch_title.set_hexpand(True)
        switch_title.set_wrap(True)
        switch_title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        switch_title.set_max_width_chars(18)
        self._show_switch = Gtk.Switch()
        try:
            self._show_switch.set_active(
                bool(getattr(win.config, 'lock_screen_medical_id', True)))
        except Exception:
            pass
        switch_row.append(switch_title)
        switch_row.append(self._show_switch)
        card.append(switch_row)

        save_btn = Gtk.Button(label='Save Medical ID')
        save_btn.add_css_class('flat')
        save_btn.add_css_class('action-link')
        save_btn.connect('clicked', lambda _b: self._on_save(win))
        card.append(save_btn)

        self._note = Gtk.Label(label='', xalign=0)
        self._note.add_css_class('dim-label')
        self._note.set_wrap(True)
        self._note.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._note.set_max_width_chars(26)
        card.append(self._note)
        self._render_contacts()
        return [_section_title(self.title), card]

    def refresh(self, win) -> None:
        return None

    # ------------------------------------------------------------ behavior

    def _render_contacts(self) -> None:
        from settings_page import (_link_button, _replace_box_children,
                                   _section_placeholder)
        from gi.repository import Gtk, Pango
        if self._contacts_box is None:
            return
        rows = []
        for index, entry in enumerate(self._draft_contacts):
            name = entry.get('name', '')
            number = entry.get('number', '')
            text = (f'{name} — {number}'.strip(' —')
                    if name or number else 'Contact')
            label = Gtk.Label(label=text, xalign=0)
            label.add_css_class('dim-label')
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(20)
            label.set_hexpand(True)
            remove = _link_button('Remove', 'dim-label')
            remove.connect(
                'clicked', lambda _b, i=index: self._remove_contact(i))
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=8)
            line.append(label)
            line.append(remove)
            rows.append(line)
        if not rows:
            rows = [_section_placeholder('No emergency contacts.')]
        _replace_box_children(self._contacts_box, rows)

    def _remove_contact(self, index: int) -> None:
        try:
            del self._draft_contacts[index]
        except IndexError:
            pass
        self._render_contacts()

    def _on_search(self) -> None:
        try:
            query = self._search_entry.get_text()
        except Exception:
            return
        if not query.strip():
            self._render_results([])
            return
        search_contacts(query, self._on_search_done)

    def _on_search_done(self, ok: bool, results) -> None:
        if not ok:
            self._render_results([], note=str(results))
            return
        self._render_results(results if isinstance(results, list) else [])

    def _render_results(self, rows: list, note: str = '') -> None:
        from settings_page import (_link_button, _replace_box_children,
                                   _section_placeholder)
        from gi.repository import Gtk, Pango
        if self._results_box is None:
            return
        widgets = []
        for uid, name, number in rows[:_SEARCH_LIMIT]:
            text = (f'{name} — {number}'.strip(' —')
                    if name or number else uid)
            label = Gtk.Label(label=text, xalign=0)
            label.add_css_class('dim-label')
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(20)
            label.set_hexpand(True)
            add = _link_button('Add', 'action-link')
            add.connect(
                'clicked',
                lambda _b, n=name, p=number: self._add_contact(n, p))
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=8)
            line.append(label)
            line.append(add)
            widgets.append(line)
        if not widgets:
            widgets = [_section_placeholder(
                note or 'No matching contacts.')]
        _replace_box_children(self._results_box, widgets)

    def _add_contact(self, name: str, number: str) -> None:
        self._draft_contacts.append({'name': name, 'number': number})
        self._render_contacts()

    def _on_save(self, win) -> None:
        draft: dict = {}
        for field, _title in MEDICAL_FIELDS:
            entry = self._entries.get(field)
            if entry is None:
                continue
            try:
                text = entry.get_text().strip()
            except Exception:
                continue
            if text:
                draft[field] = text
        draft['emergency_contacts'] = [
            {k: v for k, v in entry.items() if k in ('name', 'number',
                                                     'relation')}
            for entry in self._draft_contacts]
        try:
            win.config.set_medical_id(draft)
            win.config.set_lock_screen_medical_id(
                bool(self._show_switch.get_active()))
        except Exception as err:
            self._say(f'Could not save: {err}')
            return
        self._say('Saved.')

    def _say(self, text: str) -> None:
        try:
            if self._note is not None:
                self._note.set_text(text)
        except Exception:
            pass
