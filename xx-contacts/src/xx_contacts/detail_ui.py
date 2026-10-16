"""L3 contact detail page pushed on the list's ``Adw.NavigationView``.

Phone-first 360x720 (min 320x640): a scrolled vertical stack of sections
from ``detail`` with the theme CSS classes, call/message/email/map
actions through ``Gtk.UriLauncher``, star/business/blocked switches that
write through the store (list + detail refresh off the ``Changed``
watcher), portal vCard share, and delete with confirmation. Call history
stays hidden until xx-dialer exposes it (see ``detail``'s seam).

Import-safe without a display: constructing the page needs a display, but
importing this module must not. The page holds no text entries and never
grabs focus, so opening it never pops the on-screen keyboard.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

import os  # noqa: E402
import threading  # noqa: E402

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from xx_contacts import avatar as avatar_module  # noqa: E402
from xx_contacts import detail as detail_model  # noqa: E402
from xx_contacts import mecard as mecard_module  # noqa: E402
from xx_contacts import photos as photos_module  # noqa: E402
from xx_contacts import privacy  # noqa: E402
from xx_contacts import vcard as vcard_module  # noqa: E402
from xx_contacts.list_ui import ChangeWatcher, launch_uri  # noqa: E402
from xx_contacts.store import NotFoundError, default_exports_dir  # noqa: E402

DETAIL_PHOTO_PX = 64

ACTION_LABELS = {
    "call": "Call",
    "message": "Message",
    "email": "Email",
    "map": "Map",
    "open": "Open",
}


class ContactDetailPage(Adw.NavigationPage):
    """Detail for one contact ``uid``; pops itself after delete."""

    def __init__(self, navigation: Adw.NavigationView,
                 parent: Gtk.Window, store, uid: str, db_path: str,
                 on_deleted=None, default_region: str = "US", config=None,
                 config_path: str = "", photos_dir: str = "") -> None:
        super().__init__()
        self._navigation = navigation
        self._parent = parent
        self._store = store
        self._uid = uid
        self._db_path = db_path
        self._on_deleted = on_deleted
        self._config = config
        self._config_path = config_path
        self._photos_dir = photos_dir
        self._region = ((getattr(config, "default_region", None)
                         or default_region or "US").upper())
        self._alive = True
        self._switches: dict[str, Gtk.Switch] = {}
        self._updating_switches = False
        self._watcher = ChangeWatcher(db_path, self._on_external_changed)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
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

        ident = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=12)
        avatar_box, avatar_image, avatar_fallback = (
            avatar_module.build_avatar(DETAIL_PHOTO_PX))
        ident.append(avatar_box)
        self._avatar_box = avatar_box
        self._avatar_image = avatar_image
        self._avatar_fallback = avatar_fallback
        self._name = Gtk.Label()
        self._name.set_xalign(0.0)
        self._name.set_hexpand(True)
        self._name.set_wrap(True)
        self._name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._name.add_css_class("xx-title")
        ident.append(self._name)
        body.append(ident)

        self._toggles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=4)
        body.append(self._toggles)

        me_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        me_label = Gtk.Label(label="Me card")
        me_label.set_hexpand(True)
        me_label.set_xalign(0.0)
        me_row.append(me_label)
        self._me_button = Gtk.Button(label="Set as Me")
        self._me_button.connect("clicked", self._on_me_clicked)
        me_row.append(self._me_button)
        body.append(me_row)

        self._sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=12)
        body.append(self._sections)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._share_button = Gtk.Button(label="Share vCard")
        self._share_button.connect("clicked", self._on_share_clicked)
        actions.append(self._share_button)
        self._edit_button = Gtk.Button(label="Edit")
        self._edit_button.connect("clicked", self._on_edit_clicked)
        actions.append(self._edit_button)
        self._delete_button = Gtk.Button(label="Delete")
        self._delete_button.add_css_class("destructive-action")
        self._delete_button.connect("clicked", self._on_delete_clicked)
        actions.append(self._delete_button)
        body.append(actions)

        self.set_child(toolbar)
        self.set_title("Details")
        self.connect("unrealize", self._on_unrealize)
        self._watcher.start()
        self._schedule_load()

    # ----------------------------------------------------------------- load

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
            self._close_missing()
            return False
        title = detail_model.detail_title(record)
        self.set_title(title)
        self._name.set_text(title)
        avatar_module.set_avatar(
            self._avatar_image, self._avatar_fallback, title,
            record.photo_path or None,
            bool(getattr(self._config, "show_photos", False)),
            DETAIL_PHOTO_PX)
        self._render_toggles(record)
        self._render_sections(record)
        self._refresh_me_button()
        return False

    def _refresh_me_button(self) -> None:
        if self._config is None:
            self._me_button.set_sensitive(False)
            return
        mine = mecard_module.is_me(self._config, self._uid)
        self._me_button.set_label("Me ✓" if mine else "Set as Me")

    def _on_me_clicked(self, _button: Gtk.Button) -> None:
        if self._config is None:
            return
        if mecard_module.is_me(self._config, self._uid):
            mecard_module.clear_me_uid(self._config, self._config_path)
        else:
            mecard_module.set_me_uid(self._config, self._config_path,
                                     self._uid)
        self._refresh_me_button()

    def _on_edit_clicked(self, _button: Gtk.Button) -> None:
        from xx_contacts.editor_ui import ContactEditorPage

        page = ContactEditorPage(
            self._navigation, self._parent, self._store, self._uid,
            self._db_path, self._photos_dir, config=self._config,
            config_path=self._config_path, default_region=self._region)
        self._navigation.push(page)

    # -------------------------------------------------------------- toggles

    def _render_toggles(self, record) -> None:
        child = self._toggles.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._toggles.remove(child)
            child = following
        self._switches = {}
        self._updating_switches = True
        try:
            for key, label in (("starred", "Starred"),
                               ("business", "Business"),
                               ("blocked", "Blocked")):
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                              spacing=8)
                text = Gtk.Label(label=label)
                text.set_hexpand(True)
                text.set_xalign(0.0)
                switch = Gtk.Switch()
                switch.set_active(bool(getattr(record, key)))
                switch.connect("notify::active", self._on_toggle_flipped,
                               key)
                row.append(text)
                row.append(switch)
                self._toggles.append(row)
                self._switches[key] = switch
        finally:
            self._updating_switches = False

    def _on_toggle_flipped(self, switch: Gtk.Switch, _pspec, key: str) -> None:
        if self._updating_switches or not self._alive:
            return
        worker = threading.Thread(target=self._toggle_in_worker,
                                  args=(key, switch.get_active(),),
                                  daemon=True)
        worker.start()

    def _toggle_in_worker(self, key: str, active: bool) -> None:
        try:
            from xx_contacts import service_client as service_client_module

            connection = service_client_module.gui_connection()
            if active:
                service_client_module.update_tags_sync(
                    connection, self._uid, add=(key,))
            else:
                service_client_module.update_tags_sync(
                    connection, self._uid, remove=(key,))
        except (NotFoundError, RuntimeError, GLib.Error):
            pass
        GLib.idle_add(self._reload_from_idle)

    def _reload_from_idle(self) -> bool:
        if self._alive:
            self._schedule_load()
        return False

    def _on_external_changed(self, revision: int | None = None,
                             uids: list[str] | None = None) -> None:
        if not self._alive:
            return
        if uids is not None and self._uid not in uids:
            return
        GLib.idle_add(self._reload_from_idle)

    # ------------------------------------------------------------- sections

    def _render_sections(self, record) -> None:
        child = self._sections.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._sections.remove(child)
            child = following
        for section in detail_model.build_detail_sections(record,
                                                          self._region):
            header = Gtk.Label(label=section.title)
            header.set_xalign(0.0)
            header.add_css_class("xx-section-header")
            self._sections.append(header)
            for item in section.fields:
                self._sections.append(self._field_row(item))
        if detail_model.CALL_HISTORY_AVAILABLE:
            history = detail_model.get_call_history(self._uid)
            if history:
                header = Gtk.Label(label="Recent calls")
                header.set_xalign(0.0)
                header.add_css_class("xx-section-header")
                self._sections.append(header)
                for line in history:
                    row = Gtk.Label(label=str(line))
                    row.set_xalign(0.0)
                    row.set_wrap(True)
                    row.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    self._sections.append(row)

    def _field_row(self, item: detail_model.DetailField) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        if item.label:
            kind = Gtk.Label(label=item.label)
            kind.set_xalign(0.0)
            kind.add_css_class("xx-muted")
            box.append(kind)
        value = Gtk.Label(label=item.display)
        value.set_xalign(0.0)
        value.set_hexpand(True)
        value.set_wrap(True)
        value.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        value.set_selectable(True)
        box.append(value)
        if item.actions:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            for action in item.actions:
                label = ACTION_LABELS.get(action.kind, action.kind)
                button = Gtk.Button(label=label)
                button.connect("clicked", self._on_field_action, action.uri)
                row.append(button)
            box.append(row)
        return box

    def _on_field_action(self, _button: Gtk.Button, uri: str) -> None:
        if uri:
            launch_uri(self._parent, uri)

    # ---------------------------------------------------------------- share

    def _on_share_clicked(self, _button: Gtk.Button) -> None:
        worker = threading.Thread(target=self._share_in_worker, daemon=True)
        worker.start()

    def _share_in_worker(self) -> None:
        try:
            record = self._store.get(self._uid)
            photo = photos_module.read_photo_bytes(record.photo_path or "")
            card = vcard_module.contact_to_vcard(record, photo_bytes=photo)
            name = detail_model.detail_title(record)
        except NotFoundError:
            card, name = "", ""
        GLib.idle_add(self._portal_save, card, name)

    def _portal_save(self, card: str, name: str) -> bool:
        if not self._alive:
            return False
        if not card:
            self._message("Share failed",
                          "Could not build a vCard for this contact.")
            return False
        dialog = Gtk.FileDialog()
        dialog.set_initial_name(f"{name or 'contact'}.vcf")
        dialog.save(self._parent, None, self._on_save_finished, card)
        return False

    def _on_save_finished(self, dialog: Gtk.FileDialog,
                          result: Gio.AsyncResult, card: str) -> None:
        try:
            target = dialog.save_finish(result)
        except GLib.Error:
            return
        path = target.get_path() if target is not None else None
        if path:
            worker = threading.Thread(target=self._write_saved_vcard,
                                      args=(path, card,), daemon=True)
            worker.start()
        else:
            self._fallback_save(card)

    def _write_saved_vcard(self, path: str, card: str) -> None:
        try:
            privacy.secure_write_text(path, card)
        except OSError:
            path = ""
        GLib.idle_add(self._announce_saved, path)

    def _fallback_save(self, card: str) -> None:
        try:
            exports = privacy.ensure_private_dir(
                default_exports_dir(os.path.expanduser("~")))
            path = os.path.join(exports, f"{self._uid}.vcf")
            privacy.secure_write_text(path, card)
        except OSError:
            path = ""
        self._announce_saved(path)

    def _announce_saved(self, path: str) -> bool:
        if not self._alive:
            return False
        if not path:
            self._message("Share failed", "Could not write the vCard file.")
            return False
        dialog = Adw.AlertDialog.new("vCard saved", path)
        dialog.add_response("close", "Close")
        dialog.add_response("open", "Open")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.choose(self._parent, None,
                      lambda dlg, res: self._on_saved_opened(dlg, res, path))
        return False

    def _on_saved_opened(self, dialog: Adw.AlertDialog,
                         result: Gio.AsyncResult, path: str) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response == "open":
            launch_uri(self._parent,
                       Gio.File.new_for_path(path).get_uri())

    # --------------------------------------------------------------- delete

    def _on_delete_clicked(self, _button: Gtk.Button) -> None:
        dialog = Adw.AlertDialog.new(
            "Delete contact?",
            f"Delete {self._name.get_text()}? This cannot be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self._parent, None, self._on_delete_chosen)

    def _on_delete_chosen(self, dialog: Adw.AlertDialog,
                          result: Gio.AsyncResult) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response != "delete":
            return
        worker = threading.Thread(target=self._delete_in_worker, daemon=True)
        worker.start()

    def _delete_in_worker(self) -> None:
        try:
            from xx_contacts import service_client as service_client_module

            connection = service_client_module.gui_connection()
            service_client_module.delete_sync(connection, self._uid)
            missing = False
        except NotFoundError:
            missing = True
        except (RuntimeError, GLib.Error):
            return
        GLib.idle_add(self._after_delete, missing)

    def _after_delete(self, missing: bool) -> bool:
        if not self._alive:
            return False
        if missing:
            self._close_missing()
            return False
        self._alive = False
        try:
            self._watcher.stop()
        except Exception:
            pass
        if self._on_deleted is not None:
            self._on_deleted()
        else:
            self._navigation.pop()
        return False

    # ------------------------------------------------------------------ misc

    def _close_missing(self) -> None:
        self._message("Contact gone",
                      "This contact no longer exists.")
        self._alive = False
        try:
            self._watcher.stop()
        except Exception:
            pass
        if self._on_deleted is not None:
            self._on_deleted()
        else:
            self._navigation.pop()

    def _message(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog.new(heading, body)
        dialog.add_response("ok", "OK")
        dialog.choose(self._parent, None, self._on_dialog_dismissed)

    def _on_dialog_dismissed(self, dialog: Adw.AlertDialog,
                             result: Gio.AsyncResult) -> None:
        try:
            dialog.choose_finish(result)
        except GLib.Error:
            pass

    def _on_unrealize(self, _page: Adw.NavigationPage) -> None:
        self._alive = False
        try:
            self._watcher.stop()
        except Exception:
            pass
