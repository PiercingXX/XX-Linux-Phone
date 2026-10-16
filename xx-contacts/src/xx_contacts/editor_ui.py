"""L4 contact editor page pushed on the list's ``Adw.NavigationView``.

Phone-first 360x720 (min 320x640): name parts with a live display-name
preview, repeatable phone/email rows with human type labels (the same
mapping the detail page uses), structured addresses, touch date
pickers (``Gtk.Calendar`` in a popover, never free text), org/title,
websites, notes, groups, star/business/blocked switches, and a portal
photo pick. Every text entry takes focus only on tap, so opening the
page never pops the on-screen keyboard. Saving performs exactly ONE
service call (one global revision bump); the service emits the D-Bus
``Changed`` notice itself. Cancelling asks about discarding only when
the form is dirty.

Import-safe without a display: constructing the page needs a display,
but importing this module must not.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import os  # noqa: E402
import threading  # noqa: E402

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from xx_contacts import avatar as avatar_module  # noqa: E402
from xx_contacts import editor as editor_model  # noqa: E402
from xx_contacts import photos as photos_module  # noqa: E402
from xx_contacts import service_client as service_client_module  # noqa: E402
from xx_contacts.list_ui import install_tap_to_focus  # noqa: E402
from xx_contacts.store import ContactData, NotFoundError  # noqa: E402

PHOTO_SIZE_PX = 64
PHOTO_READ_LIMIT = 20 * 1024 * 1024


class _TypedRow:
    """One repeatable row: value entry + human-label type picker."""

    def __init__(self, page, value_text: str = "", type_text: str = "",
                 label_text: str = "", primary: bool = False,
                 placeholder: str = "", kind: str = "phone") -> None:
        self._page = page
        self._original_type = type_text
        self._touched = False
        self._kind = kind
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        if placeholder:
            entry.set_placeholder_text(placeholder)
        if value_text:
            entry.set_text(value_text)
        box.append(entry)
        self.entry = entry
        options = editor_model.type_options()
        self._tokens = [token for token, _label in options]
        dropdown = Gtk.DropDown.new_from_strings(
            [label for _token, label in options])
        dropdown.set_selected(self._index_for(type_text))
        dropdown.connect("notify::selected", self._on_type_picked)
        box.append(dropdown)
        self.dropdown = dropdown
        check = Gtk.CheckButton(label="Main")
        check.set_active(bool(primary))
        check.connect("toggled", page._on_primary_toggled, self._kind)
        box.append(check)
        self.check = check
        label_entry = Gtk.Entry()
        label_entry.set_hexpand(True)
        label_entry.set_placeholder_text("Label (optional)")
        if label_text:
            label_entry.set_text(label_text)
        remove = Gtk.Button(label="Remove")
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        remove.connect("clicked", page._on_row_remove, outer)
        outer.append(box)
        sub = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        sub.append(label_entry)
        sub.append(remove)
        outer.append(sub)
        self.label_entry = label_entry
        self.box = outer
        self.inner = box
        install_tap_to_focus(entry)
        install_tap_to_focus(label_entry)
        page._tap_entries.extend((entry, label_entry))

    def _index_for(self, type_text: str) -> int:
        for token in (type_text or "").replace(";", ",").split(","):
            cleaned = token.strip().upper()
            if cleaned in self._tokens:
                return self._tokens.index(cleaned)
        return 0

    def _on_type_picked(self, _dropdown: Gtk.DropDown, _pspec) -> None:
        self._touched = True

    def current_type(self) -> str:
        if self._touched:
            return self._tokens[self.dropdown.get_selected()]
        return self._original_type

    def value(self) -> str:
        return self.entry.get_text()

    def label(self) -> str:
        return self.label_entry.get_text()

    def primary(self) -> bool:
        return self.check.get_active()


class _AddressRow:
    """One structured address block with a type picker."""

    FIELDS = (("po_box", "PO box"), ("extended", "Extended"),
              ("street", "Street"), ("locality", "City"),
              ("region", "Region"), ("postal_code", "Postal code"),
              ("country", "Country"))

    def __init__(self, page, data=None) -> None:
        self._page = page
        self._original_type = (data.type if data is not None else "")
        self._touched = False
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.entries: dict[str, Gtk.Entry] = {}
        for key, placeholder in self.FIELDS:
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_placeholder_text(placeholder)
            if data is not None and getattr(data, key, ""):
                entry.set_text(getattr(data, key))
            outer.append(entry)
            self.entries[key] = entry
            install_tap_to_focus(entry)
            page._tap_entries.append(entry)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        options = editor_model.type_options()
        self._tokens = [token for token, _label in options]
        dropdown = Gtk.DropDown.new_from_strings(
            [label for _token, label in options])
        dropdown.set_selected(self._index_for(self._original_type))
        dropdown.connect("notify::selected", self._on_type_picked)
        row.append(dropdown)
        self.dropdown = dropdown
        check = Gtk.CheckButton(label="Main")
        if data is not None:
            check.set_active(bool(data.is_primary))
        check.connect("toggled", page._on_primary_toggled, "address")
        row.append(check)
        self.check = check
        remove = Gtk.Button(label="Remove")
        remove.connect("clicked", page._on_row_remove, outer)
        row.append(remove)
        outer.append(row)
        self.box = outer
        self.inner = row

    def _index_for(self, type_text: str) -> int:
        for token in (type_text or "").replace(";", ",").split(","):
            cleaned = token.strip().upper()
            if cleaned in self._tokens:
                return self._tokens.index(cleaned)
        return 0

    def _on_type_picked(self, _dropdown: Gtk.DropDown, _pspec) -> None:
        self._touched = True

    def current_type(self) -> str:
        if self._touched:
            return self._tokens[self.dropdown.get_selected()]
        return self._original_type

    def primary(self) -> bool:
        return self.check.get_active()


class _UrlRow:
    """One repeatable website row: value entry + remove."""

    def __init__(self, page, value_text: str = "") -> None:
        self._page = page
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_placeholder_text("https://…")
        if value_text:
            entry.set_text(value_text)
        box.append(entry)
        self.entry = entry
        remove = Gtk.Button(label="Remove")
        remove.connect("clicked", page._on_row_remove, box)
        box.append(remove)
        self.box = box
        install_tap_to_focus(entry)
        page._tap_entries.append(entry)

    def value(self) -> str:
        return self.entry.get_text()


class ContactEditorPage(Adw.NavigationPage):
    """New-or-edit contact form; pops itself after save or discard."""

    def __init__(self, navigation: Adw.NavigationView,
                 parent: Gtk.Window, store, uid: str | None, db_path: str,
                 photos_dir: str, config=None, config_path: str = "",
                 default_region: str = "US", prefill_phone: str = "",
                 on_saved=None) -> None:
        super().__init__()
        self._navigation = navigation
        self._parent = parent
        self._store = store
        self._uid = uid
        self._db_path = db_path
        self._photos_dir = photos_dir
        self._config = config
        self._config_path = config_path
        self._region = ((getattr(config, "default_region", None)
                         or default_region or "US").upper())
        self._on_saved = on_saved
        self._alive = True
        self._tap_entries: list[Gtk.Widget] = []
        self._phone_rows: list[_TypedRow] = []
        self._email_rows: list[_TypedRow] = []
        self._address_rows: list[_AddressRow] = []
        self._url_rows: list[_UrlRow] = []
        self._saved_snapshot = editor_model.blank_form()
        self._original_photo_path = ""
        self._pending_photo: bytes | None = None
        self._remove_photo = False
        self._saving = False

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        self._cancel_button = Gtk.Button(label="Cancel")
        self._cancel_button.connect("clicked", self._on_cancel_clicked)
        header.pack_start(self._cancel_button)
        self._save_button = Gtk.Button(label="Save")
        self._save_button.add_css_class("suggested-action")
        self._save_button.connect("clicked", self._on_save_clicked)
        header.pack_end(self._save_button)
        toolbar.add_top_bar(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_hexpand(True)
        body.set_margin_start(12)
        body.set_margin_end(12)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        scrolled.set_child(body)
        toolbar.set_content(scrolled)

        self._error = Gtk.Label()
        self._error.set_visible(False)
        self._error.set_wrap(True)
        self._error.add_css_class("error")
        body.append(self._error)

        photo_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=12)
        avatar_box, avatar_image, avatar_fallback = (
            avatar_module.build_avatar(PHOTO_SIZE_PX))
        photo_row.append(avatar_box)
        self._avatar_box = avatar_box
        self._avatar_image = avatar_image
        self._avatar_fallback = avatar_fallback
        photo_buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=6)
        photo_buttons.set_valign(Gtk.Align.CENTER)
        self._photo_pick = Gtk.Button(label="Choose photo")
        self._photo_pick.connect("clicked", self._on_photo_pick)
        photo_buttons.append(self._photo_pick)
        self._photo_remove = Gtk.Button(label="Remove photo")
        self._photo_remove.connect("clicked", self._on_photo_remove)
        photo_buttons.append(self._photo_remove)
        photo_row.append(photo_buttons)
        body.append(photo_row)

        name_group = Adw.PreferencesGroup()
        name_group.set_title("Name")
        self._entries: dict[str, Adw.EntryRow] = {}
        for key, title in (("given", "First name"),
                           ("family", "Last name"),
                           ("prefix", "Prefix"),
                           ("suffix", "Suffix"),
                           ("nickname", "Nickname")):
            row = Adw.EntryRow()
            row.set_title(title)
            name_group.add(row)
            self._entries[key] = row
            install_tap_to_focus(row)
            self._tap_entries.append(row)
            if key in ("given", "family"):
                row.connect("changed", self._on_name_changed)
        body.append(name_group)
        self._preview = Gtk.Label()
        self._preview.set_xalign(0.0)
        body.append(self._preview)

        body.append(self._section_label("Phone numbers"))
        self._phone_rows_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.append(self._phone_rows_box)
        add_phone = Gtk.Button(label="Add phone number")
        add_phone.connect("clicked", self._on_add_phone)
        body.append(add_phone)

        body.append(self._section_label("Email addresses"))
        self._email_rows_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.append(self._email_rows_box)
        add_email = Gtk.Button(label="Add email address")
        add_email.connect("clicked", self._on_add_email)
        body.append(add_email)

        body.append(self._section_label("Addresses"))
        self._address_rows_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.append(self._address_rows_box)
        add_address = Gtk.Button(label="Add address")
        add_address.connect("clicked", self._on_add_address)
        body.append(add_address)

        body.append(self._section_label("Dates"))
        dates = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._birthday_display = Gtk.Label(label="No birthday set")
        self._birthday_display.set_xalign(0.0)
        dates.append(self._birthday_display)
        birthday_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=8)
        self._birthday_pick = Gtk.Button(label="Pick birthday")
        self._birthday_pick.connect("clicked", self._on_birthday_pick)
        birthday_row.append(self._birthday_pick)
        self._birthday_clear = Gtk.Button(label="Clear")
        self._birthday_clear.connect("clicked", self._on_birthday_clear)
        birthday_row.append(self._birthday_clear)
        dates.append(birthday_row)
        self._anniversary_display = Gtk.Label(label="No anniversary set")
        self._anniversary_display.set_xalign(0.0)
        dates.append(self._anniversary_display)
        anniversary_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._anniversary_pick = Gtk.Button(label="Pick anniversary")
        self._anniversary_pick.connect("clicked",
                                       self._on_anniversary_pick)
        anniversary_row.append(self._anniversary_pick)
        self._anniversary_clear = Gtk.Button(label="Clear")
        self._anniversary_clear.connect("clicked",
                                        self._on_anniversary_clear)
        anniversary_row.append(self._anniversary_clear)
        dates.append(anniversary_row)
        body.append(dates)
        self._birthday_popover = self._build_date_popover(
            self._birthday_pick, self._birthday_display, "birthday")
        self._anniversary_popover = self._build_date_popover(
            self._anniversary_pick, self._anniversary_display,
            "anniversary")

        org_group = Adw.PreferencesGroup()
        org_group.set_title("Organization")
        for key, title in (("org", "Company or organization"),
                           ("title", "Job title")):
            row = Adw.EntryRow()
            row.set_title(title)
            org_group.add(row)
            self._entries[key] = row
            install_tap_to_focus(row)
            self._tap_entries.append(row)
        body.append(org_group)

        body.append(self._section_label("Websites"))
        self._url_rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                     spacing=8)
        body.append(self._url_rows_box)
        add_url = Gtk.Button(label="Add website")
        add_url.connect("clicked", self._on_add_url)
        body.append(add_url)

        notes_group = Adw.PreferencesGroup()
        notes_group.set_title("Notes and groups")
        self._note_view = Gtk.TextView()
        self._note_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._note_view.set_size_request(-1, 96)
        notes_group.add(self._note_view)
        install_tap_to_focus(self._note_view)
        self._tap_entries.append(self._note_view)
        groups_row = Adw.EntryRow()
        groups_row.set_title("Groups (comma separated)")
        notes_group.add(groups_row)
        self._entries["groups"] = groups_row
        install_tap_to_focus(groups_row)
        self._tap_entries.append(groups_row)
        body.append(notes_group)

        flags_group = Adw.PreferencesGroup()
        flags_group.set_title("Tags")
        self._switches: dict[str, Gtk.Switch] = {}
        for key, title in (("starred", "Starred"),
                           ("business", "Business"),
                           ("blocked", "Blocked")):
            row = Adw.ActionRow()
            row.set_title(title)
            switch = Gtk.Switch()
            switch.set_valign(Gtk.Align.CENTER)
            row.add_suffix(switch)
            flags_group.add(row)
            self._switches[key] = switch
        body.append(flags_group)

        self.set_child(toolbar)
        self.set_title("New contact" if uid is None else "Edit contact")
        self.connect("map", self._on_map_clear_focus)
        self.connect("unrealize", self._on_unrealize)
        if uid is None:
            self._add_phone_row()
            self._add_email_row()
            self._add_url_row()
            if (prefill_phone or "").strip() and self._phone_rows:
                self._phone_rows[0].entry.set_text(
                    (prefill_phone or "").strip())
            self._saved_snapshot = self._read_form()
            self._refresh_preview()
            self._refresh_photo_preview()
        else:
            self._schedule_load()

    # ------------------------------------------------------------------ build

    def _section_label(self, title: str) -> Gtk.Label:
        label = Gtk.Label(label=title)
        label.set_xalign(0.0)
        label.add_css_class("xx-section-header")
        return label

    def _build_date_popover(self, anchor: Gtk.Button, display: Gtk.Label,
                            kind: str) -> Gtk.Popover:
        popover = Gtk.Popover()
        popover.set_parent(anchor)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        calendar = Gtk.Calendar()
        calendar.connect("day-selected", self._on_calendar_picked, kind)
        box.append(calendar)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=8)
        set_button = Gtk.Button(label="Set date")
        set_button.connect("clicked", self._on_calendar_set, kind)
        actions.append(set_button)
        clear_button = Gtk.Button(label="Clear")
        clear_button.connect("clicked", self._on_calendar_clear, kind)
        actions.append(clear_button)
        box.append(actions)
        popover.set_child(box)
        if kind == "birthday":
            self._birthday_calendar = calendar
        else:
            self._anniversary_calendar = calendar
        _ = display
        return popover

    def _calendar_for(self, kind: str) -> Gtk.Calendar:
        if kind == "birthday":
            return self._birthday_calendar
        return self._anniversary_calendar

    def _display_for(self, kind: str) -> Gtk.Label:
        if kind == "birthday":
            return self._birthday_display
        return self._anniversary_display

    def _popover_for(self, kind: str) -> Gtk.Popover:
        if kind == "birthday":
            return self._birthday_popover
        return self._anniversary_popover

    # -------------------------------------------------------------------- rows

    def _add_phone_row(self, data=None) -> None:
        row = _TypedRow(
            self,
            value_text=data.number_raw if data is not None else "",
            type_text=data.type if data is not None else "CELL",
            label_text=data.label if data is not None else "",
            primary=bool(data.is_primary) if data is not None else False,
            placeholder="Phone number", kind="phone")
        self._phone_rows.append(row)
        self._phone_rows_box.append(row.box)

    def _add_email_row(self, data=None) -> None:
        row = _TypedRow(
            self,
            value_text=data.address if data is not None else "",
            type_text=data.type if data is not None else "HOME",
            label_text=data.label if data is not None else "",
            primary=bool(data.is_primary) if data is not None else False,
            placeholder="Email address", kind="email")
        self._email_rows.append(row)
        self._email_rows_box.append(row.box)

    def _add_address_row(self, data=None) -> None:
        row = _AddressRow(self, data)
        self._address_rows.append(row)
        self._address_rows_box.append(row.box)

    def _add_url_row(self, value: str = "") -> None:
        row = _UrlRow(self, value)
        self._url_rows.append(row)
        self._url_rows_box.append(row.box)

    def _on_add_phone(self, _button: Gtk.Button) -> None:
        self._add_phone_row()

    def _on_add_email(self, _button: Gtk.Button) -> None:
        self._add_email_row()

    def _on_add_address(self, _button: Gtk.Button) -> None:
        self._add_address_row()

    def _on_add_url(self, _button: Gtk.Button) -> None:
        self._add_url_row()

    def _on_row_remove(self, _button: Gtk.Button, box: Gtk.Widget) -> None:
        self._phone_rows = [r for r in self._phone_rows if r.box is not box]
        self._email_rows = [r for r in self._email_rows if r.box is not box]
        self._address_rows = [r for r in self._address_rows
                              if r.box is not box]
        self._url_rows = [r for r in self._url_rows if r.box is not box]
        parent = box.get_parent()
        if parent is not None:
            parent.remove(box)

    def _on_primary_toggled(self, check: Gtk.CheckButton,
                            kind: str) -> None:
        """One primary per kind: checking a row clears its siblings."""
        if not check.get_active():
            return
        rows = {"phone": self._phone_rows, "email": self._email_rows,
                "address": self._address_rows}.get(kind, [])
        for row in rows:
            if row.check is not check and row.check.get_active():
                row.check.set_active(False)

    def _on_name_changed(self, _entry) -> None:
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        try:
            preview = editor_model.display_preview(self._read_form())
        except Exception:
            preview = ""
        self._preview.set_text(f"Shows as: {preview}" if preview
                               else "Shows as: (empty)")

    # ------------------------------------------------------------------- dates

    def _seed_calendar(self, kind: str) -> None:
        calendar = self._calendar_for(kind)
        text = (self._display_for(kind).get_text() or "").strip()
        year, month, day = None, None, None
        if editor_model.is_valid_date(text):
            year, month, day = (int(text[0:4]), int(text[5:7]),
                                int(text[8:10]))
        moment = GLib.DateTime.new_now_local()
        if year is not None:
            try:
                moment = GLib.DateTime.new_local(year, month, day, 0, 0, 0.0)
            except Exception:
                moment = GLib.DateTime.new_now_local()
        calendar.select_day(moment)

    def _on_birthday_pick(self, _button: Gtk.Button) -> None:
        self._seed_calendar("birthday")
        self._birthday_popover.popup()

    def _on_anniversary_pick(self, _button: Gtk.Button) -> None:
        self._seed_calendar("anniversary")
        self._anniversary_popover.popup()

    def _on_calendar_picked(self, _calendar: Gtk.Calendar,
                            kind: str) -> None:
        return None

    def _on_calendar_set(self, _button: Gtk.Button, kind: str) -> None:
        moment = self._calendar_for(kind).get_date()
        text = (f"{moment.get_year():04d}-{moment.get_month():02d}-"
                f"{moment.get_day_of_month():02d}")
        self._display_for(kind).set_text(text)
        self._popover_for(kind).popdown()

    def _on_calendar_clear(self, _button: Gtk.Button, kind: str) -> None:
        self._display_for(kind).set_text("")
        self._popover_for(kind).popdown()

    def _on_birthday_clear(self, _button: Gtk.Button) -> None:
        self._birthday_display.set_text("")

    def _on_anniversary_clear(self, _button: Gtk.Button) -> None:
        self._anniversary_display.set_text("")

    def _date_value(self, display: Gtk.Label, empty_text: str) -> str:
        text = (display.get_text() or "").strip()
        if not text or text == empty_text:
            return ""
        return text

    # ------------------------------------------------------------------- photo

    def _refresh_photo_preview(self) -> None:
        if self._pending_photo is not None:
            pixbuf = avatar_module.pixbuf_from_bytes(self._pending_photo,
                                                     PHOTO_SIZE_PX)
            if pixbuf is not None:
                self._avatar_image.set_from_pixbuf(pixbuf)
                self._avatar_image.set_visible(True)
                self._avatar_fallback.set_visible(False)
                return
            self._pending_photo = None
        avatar_module.set_avatar(
            self._avatar_image, self._avatar_fallback,
            self._entries["given"].get_text() + " "
            + self._entries["family"].get_text()
            if "given" in self._entries else "",
            None if self._remove_photo else (self._original_photo_path
                                             or None),
            True, PHOTO_SIZE_PX)

    def _on_photo_pick(self, _button: Gtk.Button) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Choose a contact photo")
        images = Gtk.FileFilter()
        images.set_name("Images")
        images.add_mime_type("image/*")
        images.add_pattern("*.png")
        images.add_pattern("*.jpg")
        images.add_pattern("*.jpeg")
        images.add_pattern("*.webp")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(images)
        dialog.set_filters(store)
        dialog.open(self._parent, None, self._on_photo_chosen)

    def _on_photo_chosen(self, dialog: Gtk.FileDialog,
                         result: Gio.AsyncResult) -> None:
        if not self._alive:
            return
        try:
            target = dialog.open_finish(result)
        except GLib.Error:
            return
        path = target.get_path() if target is not None else None
        if not path:
            return
        worker = threading.Thread(target=self._read_photo_in_worker,
                                  args=(path,), daemon=True)
        worker.start()

    def _read_photo_in_worker(self, path: str) -> None:
        try:
            if os.path.getsize(path) > PHOTO_READ_LIMIT:
                raise OSError("image is too large")
            with open(path, "rb") as handle:
                raw = handle.read(PHOTO_READ_LIMIT + 1)
        except OSError:
            raw = b""
        GLib.idle_add(self._apply_picked_photo, raw)

    def _apply_picked_photo(self, raw: bytes) -> bool:
        if not self._alive:
            return False
        if not raw:
            self._show_error("Could not read that image.")
            return False
        try:
            photos_module.process_photo(raw)
        except photos_module.PhotoError:
            self._show_error("That file is not a readable image.")
            return False
        self._pending_photo = raw
        self._remove_photo = False
        self._hide_error()
        self._refresh_photo_preview()
        return False

    def _on_photo_remove(self, _button: Gtk.Button) -> None:
        self._pending_photo = None
        self._remove_photo = True
        self._refresh_photo_preview()

    # -------------------------------------------------------------------- load

    def _schedule_load(self) -> None:
        worker = threading.Thread(target=self._load_in_worker, daemon=True)
        worker.start()

    def _load_in_worker(self) -> None:
        try:
            record = self._store.get(self._uid)
        except NotFoundError:
            record = None
        GLib.idle_add(self._apply_record, record)

    def _apply_record(self, record) -> bool:
        if not self._alive:
            return False
        if record is None:
            self._show_error("This contact no longer exists.")
            self._save_button.set_sensitive(False)
            return False
        form = editor_model.form_from_record(record)
        for key in ("given", "family", "prefix", "suffix", "nickname",
                    "org", "title"):
            self._entries[key].set_text(getattr(form, key))
        self._entries["groups"].set_text(", ".join(form.groups))
        self._note_view.get_buffer().set_text(form.note, -1)
        for phone in form.phones:
            self._add_phone_row(_PhoneData(
                phone.number, phone.type, phone.label,
                phone.is_primary))
        if not form.phones:
            self._add_phone_row()
        for email in form.emails:
            self._add_email_row(_EmailData(
                email.address, email.type, email.label,
                email.is_primary))
        if not form.emails:
            self._add_email_row()
        for address in form.addresses:
            self._add_address_row(address)
        for url in form.urls:
            self._add_url_row(url.value)
        if not form.urls:
            self._add_url_row()
        self._birthday_display.set_text(
            form.birthday or "No birthday set")
        self._anniversary_display.set_text(
            form.anniversary or "No anniversary set")
        for key in ("starred", "business", "blocked"):
            self._switches[key].set_active(bool(getattr(form, key)))
        self._original_photo_path = record.photo_path or ""
        self._saved_snapshot = self._read_form()
        self._refresh_preview()
        self._refresh_photo_preview()
        return False

    # -------------------------------------------------------------------- form

    def _read_form(self):
        form = editor_model.EditorForm()
        for key in ("given", "family", "prefix", "suffix", "nickname",
                    "org", "title"):
            setattr(form, key, self._entries[key].get_text())
        form.groups = [part for part in
                       (self._entries["groups"].get_text() or ""
                        ).split(",")]
        start, end = self._note_view.get_buffer().get_bounds()
        form.note = self._note_view.get_buffer().get_text(
            start, end, False)
        for row in self._phone_rows:
            form.phones.append(editor_model.PhoneForm(
                number=row.value(), type=row.current_type(),
                label=row.label(), is_primary=row.primary()))
        for row in self._email_rows:
            form.emails.append(editor_model.EmailForm(
                address=row.value(), type=row.current_type(),
                label=row.label(), is_primary=row.primary()))
        for row in self._address_rows:
            form.addresses.append(editor_model.AddressForm(
                po_box=row.entries["po_box"].get_text(),
                extended=row.entries["extended"].get_text(),
                street=row.entries["street"].get_text(),
                locality=row.entries["locality"].get_text(),
                region=row.entries["region"].get_text(),
                postal_code=row.entries["postal_code"].get_text(),
                country=row.entries["country"].get_text(),
                type=row.current_type(), label="",
                is_primary=row.primary()))
        for row in self._url_rows:
            form.urls.append(editor_model.UrlForm(value=row.value()))
        form.birthday = self._date_value(self._birthday_display,
                                         "No birthday set")
        form.anniversary = self._date_value(self._anniversary_display,
                                            "No anniversary set")
        for key in ("starred", "business", "blocked"):
            setattr(form, key, self._switches[key].get_active())
        return form

    def _show_error(self, text: str) -> None:
        self._error.set_text(text)
        self._error.set_visible(True)

    def _hide_error(self) -> None:
        self._error.set_visible(False)

    # -------------------------------------------------------------------- save

    def _on_save_clicked(self, _button: Gtk.Button) -> None:
        if self._saving or not self._alive:
            return
        form = self._read_form()
        error = editor_model.validate(form)
        if error is not None:
            self._show_error(error)
            return
        self._hide_error()
        self._saving = True
        self._save_button.set_sensitive(False)
        worker = threading.Thread(target=self._save_in_worker,
                                  args=(form, self._pending_photo,
                                        self._remove_photo),
                                  daemon=True)
        worker.start()

    def _save_in_worker(self, form, photo_raw: bytes | None,
                        remove_photo: bool) -> None:
        try:
            if self._uid is None:
                original: ContactData = ContactData()
            else:
                try:
                    original = self._store.get(self._uid)
                except NotFoundError as exc:
                    GLib.idle_add(self._after_save_error, str(exc))
                    return
            data = editor_model.apply_form_to_data(
                original, form, self._region)
            if remove_photo:
                data.photo_path = ""
            photo_bytes: bytes | None = None
            if photo_raw is not None:
                try:
                    photo_bytes = photos_module.process_photo(photo_raw)
                except photos_module.PhotoError as exc:
                    GLib.idle_add(self._after_save_error, str(exc))
                    return
            card = service_client_module.form_to_vcard(
                self._uid or "", data, photo_bytes)
            connection = service_client_module.gui_connection()
            if self._uid is None:
                saved_uid = service_client_module.create_sync(
                    connection, card)
            else:
                service_client_module.update_sync(
                    connection, self._uid, card)
                saved_uid = self._uid
        except (ValueError, NotFoundError, RuntimeError,
                GLib.Error) as exc:
            GLib.idle_add(self._after_save_error, str(exc))
            return
        except Exception as exc:
            GLib.idle_add(self._after_save_error, str(exc))
            return
        GLib.idle_add(self._after_save, saved_uid)

    def _after_save_error(self, text: str) -> bool:
        if not self._alive:
            return False
        self._saving = False
        self._save_button.set_sensitive(True)
        self._show_error(text or "Could not save this contact.")
        return False

    def _after_save(self, uid: str) -> bool:
        if not self._alive:
            return False
        self._saving = False
        self._uid = uid
        self._saved_snapshot = self._read_form()
        if self._on_saved is not None:
            try:
                self._on_saved(uid)
            except Exception:
                pass
        self._alive = False
        self._navigation.pop()
        return False

    # ------------------------------------------------------------------ cancel

    def _on_cancel_clicked(self, _button: Gtk.Button) -> None:
        if not self._alive:
            return
        if editor_model.is_dirty(self._saved_snapshot, self._read_form()
                                 ) or self._pending_photo is not None \
                or self._remove_photo:
            dialog = Adw.AlertDialog.new(
                "Discard changes?",
                "Unsaved edits to this contact will be lost.")
            dialog.add_response("keep", "Keep editing")
            dialog.add_response("discard", "Discard")
            dialog.set_response_appearance(
                "discard", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("keep")
            dialog.set_close_response("keep")
            dialog.choose(self._parent, None, self._on_discard_chosen)
            return
        self._alive = False
        self._navigation.pop()

    def _on_discard_chosen(self, dialog: Adw.AlertDialog,
                           result: Gio.AsyncResult) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response != "discard":
            return
        self._alive = False
        self._navigation.pop()

    # --------------------------------------------------------------------- misc

    def _on_map_clear_focus(self, _page: Adw.NavigationPage) -> None:
        toplevel = self.get_root()
        if toplevel is not None:
            try:
                toplevel.set_focus(None)
            except Exception:
                pass

    def _on_unrealize(self, _page: Adw.NavigationPage) -> None:
        self._alive = False


class _PhoneData:
    """Tiny adapter so edit-load reuses the typed-row constructor."""

    def __init__(self, number_raw: str, type_text: str, label: str,
                 primary: bool) -> None:
        self.number_raw = number_raw
        self.type = type_text
        self.label = label
        self.is_primary = primary


class _EmailData:
    """Tiny adapter so edit-load reuses the typed-row constructor."""

    def __init__(self, address: str, type_text: str, label: str,
                 primary: bool) -> None:
        self.address = address
        self.type = type_text
        self.label = label
        self.is_primary = primary
