"""Settings dialog: every Android setting with a real effect here.

Ports ui/settings/SettingsScreen.kt over an Adw.PreferencesDialog:
accounts (add via the loopback OAuth flow, remove with the
snooze-restore warning, revoke), gestures, sending, sync,
notifications, appearance, privacy, logs, and JSON backup. Thin
view only — persistence and worker ops live behind hooks (main.py),
config saves are tiny atomic writes, and account/backup work runs
off the main loop.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk

from xx_email import config as config_mod
from xx_email import snooze as snooze_mod
from xx_email.ui import common as common_mod

SWIPE_OPTIONS = ("ARCHIVE", "DELETE", "MARK_READ", "STAR", "SNOOZE")
THEME_OPTIONS = ("follow", "light", "dark")
UNDO_OPTIONS = ("5", "10", "20", "30")
SYNC_OPTIONS = ("15", "30", "60")


def swipe_label(action: str) -> str:
    return {"ARCHIVE": "Archive", "DELETE": "Delete",
            "MARK_READ": "Mark read", "STAR": "Star",
            "SNOOZE": "Snooze"}.get(action or "", action or "")


def account_subtitle(snoozed: int, queued: int) -> str:
    parts = []
    if snoozed:
        parts.append(f"{snoozed:d} snoozed")
    if queued:
        parts.append(f"{queued:d} queued")
    return ", ".join(parts) or "Signed in"


class SettingsDialog(Adw.PreferencesDialog):
    """Preferences dialog; hooks own every worker-backed op."""

    def __init__(self, hooks, toast) -> None:
        super().__init__()
        self.set_title("Settings")
        self._hooks = hooks
        self._toast = toast
        self._stored = config_mod.load()
        self._accounts_box = None
        self._accounts_empty = None

        self.add(self._accounts_page())
        self.add(self._prefs_page())
        self.add(self._privacy_page())
        hooks.load_state(self._on_state)

    # ------------------------------------------------------------------ pages

    def _accounts_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        page.set_title("Accounts")
        group = Adw.PreferencesGroup()
        group.set_title("Accounts")
        page.add(group)
        self._accounts_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4)
        group.add(self._accounts_box)
        self._accounts_empty = common_mod.wrapped(
            Gtk.Label(label="Loading accounts…"))
        self._accounts_box.append(self._accounts_empty)
        add = Adw.ActionRow()
        add.set_title("Add account")
        add.set_subtitle("Sign in with Google in your browser")
        button = common_mod.text_button("Add")
        button.connect("clicked", lambda _b: self._hooks.add_account())
        button.set_valign(Gtk.Align.CENTER)
        add.add_suffix(button)
        group.add(add)
        return page

    def _prefs_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        page.set_title("Preferences")
        gestures = Adw.PreferencesGroup()
        gestures.set_title("Gestures")
        page.add(gestures)
        gestures.add(self._combo_row(
            "Swipe right", self._stored.swipe_right, SWIPE_OPTIONS,
            swipe_label, "swipe_right"))
        gestures.add(self._combo_row(
            "Swipe left", self._stored.swipe_left, SWIPE_OPTIONS,
            swipe_label, "swipe_left"))
        sending = Adw.PreferencesGroup()
        sending.set_title("Sending")
        page.add(sending)
        sending.add(self._combo_row(
            "Undo window", str(self._stored.undo_seconds), UNDO_OPTIONS,
            lambda value: value + " s", "undo_seconds"))
        send_archive = Adw.SwitchRow()
        send_archive.set_title("Send and archive")
        send_archive.set_active(bool(self._stored.send_and_archive))
        send_archive.connect("notify::active",
                             lambda row, _p: self._save_flag(
                                 "send_and_archive", row.get_active()))
        sending.add(send_archive)
        syncing = Adw.PreferencesGroup()
        syncing.set_title("Sync and notifications")
        page.add(syncing)
        syncing.add(self._combo_row(
            "Sync interval", str(self._stored.sync_minutes),
            SYNC_OPTIONS, lambda value: value + " min",
            "sync_minutes"))
        notify_switch = Adw.SwitchRow()
        notify_switch.set_title("New-mail notifications")
        notify_switch.set_active(
            bool(self._stored.notifications_enabled))
        notify_switch.connect("notify::active",
                              lambda row, _p: self._save_flag(
                                  "notifications_enabled",
                                  row.get_active()))
        syncing.add(notify_switch)
        remote = Adw.SwitchRow()
        remote.set_title("Load remote images")
        remote.set_subtitle("Off keeps tracking pixels dead")
        remote.set_active(bool(self._stored.remote_images))
        remote.connect("notify::active",
                       lambda row, _p: self._save_flag(
                           "remote_images", row.get_active()))
        syncing.add(remote)
        appearance = Adw.PreferencesGroup()
        appearance.set_title("Appearance")
        page.add(appearance)
        appearance.add(self._combo_row(
            "Theme", self._stored.theme_mode, THEME_OPTIONS,
            str.capitalize, "theme_mode"))
        client = Adw.EntryRow()
        client.set_title("OAuth client ID")
        client.set_text(self._stored.client_id or "")
        client.connect("changed",
                       lambda row: self._save_text(
                           "client_id", row.get_text().strip()))
        appearance.add(client)
        last = Adw.ActionRow()
        last.set_title("Last-used account")
        last.set_subtitle(self._stored.last_account or "None yet")
        appearance.add(last)
        return page

    def _privacy_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        page.set_title("Privacy and data")
        privacy = Adw.PreferencesGroup()
        privacy.set_title("Privacy")
        page.add(privacy)
        note = Adw.ActionRow()
        note.set_title("On this device only")
        note.set_subtitle(
            "No analytics or trackers. Tokens stay in the keyring; "
            "mail cache and credentials never leave in a backup.")
        privacy.add(note)
        revoke = Adw.ActionRow()
        revoke.set_title("Revoke Google access")
        revoke.set_subtitle("Open your Google permissions page")
        web = common_mod.text_button("Open")
        web.connect("clicked",
                    lambda _b: self._hooks.open_permissions())
        web.set_valign(Gtk.Align.CENTER)
        revoke.add_suffix(web)
        privacy.add(revoke)
        data = Adw.PreferencesGroup()
        data.set_title("Logs and backup")
        page.add(data)
        logs = Adw.ActionRow()
        logs.set_title("Logs")
        logs.set_subtitle("On-device log, scrubbed before display")
        logs_button = common_mod.text_button("View")
        logs_button.connect("clicked",
                            lambda _b: self._hooks.open_logs())
        logs_button.set_valign(Gtk.Align.CENTER)
        logs.add_suffix(logs_button)
        data.add(logs)
        export = Adw.ActionRow()
        export.set_title("Export backup")
        export.set_subtitle("Settings, snoozes and queued sends as JSON")
        export_button = common_mod.text_button("Save")
        export_button.connect("clicked",
                              lambda _b: self._export_backup())
        export_button.set_valign(Gtk.Align.CENTER)
        export.add_suffix(export_button)
        data.add(export)
        restore = Adw.ActionRow()
        restore.set_title("Import backup")
        restore.set_subtitle("Validated before anything is written")
        restore_button = common_mod.text_button("Open")
        restore_button.connect("clicked",
                               lambda _b: self._import_backup())
        restore_button.set_valign(Gtk.Align.CENTER)
        restore.add_suffix(restore_button)
        data.add(restore)
        return page

    # -------------------------------------------------------------------- rows

    def _combo_row(self, title: str, current: str, options: tuple,
                   label_of, key: str) -> Adw.ComboRow:
        row = Adw.ComboRow()
        row.set_title(title)
        row.set_model(Gtk.StringList.new(
            [label_of(option) for option in options]))
        try:
            row.set_selected(max(0, list(options).index(current)))
        except ValueError:
            row.set_selected(0)
        row.connect("notify::selected",
                    lambda combo, _p: self._save_option(
                        key, options[combo.get_selected()], options))
        return row

    def _save_option(self, key: str, value: str,
                     options: tuple) -> None:
        if value not in options:
            return
        if key == "undo_seconds":
            self._stored.undo_seconds = int(value)
        elif key == "sync_minutes":
            self._stored.sync_minutes = int(value)
        elif key == "swipe_right":
            self._stored.swipe_right = value
        elif key == "swipe_left":
            self._stored.swipe_left = value
        elif key == "theme_mode":
            self._stored.theme_mode = value
        else:
            return
        self._persist()

    def _save_flag(self, key: str, value: bool) -> None:
        setattr(self._stored, key, bool(value))
        self._persist()

    def _save_text(self, key: str, value: str) -> None:
        setattr(self._stored, key, value)
        self._persist()

    def _persist(self) -> None:
        try:
            config_mod.save(self._stored)
        except OSError:
            self._toast("Couldn't save settings", None)

    # ---------------------------------------------------------------- accounts

    def _on_state(self, result) -> None:
        if isinstance(result, Exception):
            self._accounts_empty.set_label("Accounts: unavailable")
            return
        _config, accounts = result
        for child in list(self._row_children()):
            self._accounts_box.remove(child)
        if not accounts:
            self._accounts_box.append(common_mod.wrapped(Gtk.Label(
                label="No accounts yet — add one below.")))
            return
        for entry in accounts:
            self._accounts_box.append(self._account_row(entry))

    def _row_children(self) -> list:
        children = []
        child = self._accounts_box.get_first_child()
        while child is not None:
            children.append(child)
            child = child.get_next_sibling()
        return children

    def _account_row(self, entry: dict) -> Gtk.Box:
        email = entry.get("email", "")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                      spacing=4)
        name = common_mod.ellipsized(Gtk.Label(label=email))
        box.append(name)
        sub = common_mod.wrapped(Gtk.Label(
            label=account_subtitle(int(entry.get("snoozed", 0)),
                                   int(entry.get("queued", 0)))))
        sub.add_css_class("email-muted")
        box.append(sub)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=8)
        buttons.set_halign(Gtk.Align.END)
        revoke = common_mod.text_button("Revoke")
        revoke.set_tooltip_text("Revoke this account's Google grant")
        revoke.connect("clicked",
                       lambda _b, address=email:
                       self._confirm_revoke(address))
        buttons.append(revoke)
        remove = common_mod.text_button("Remove")
        remove.connect("clicked",
                       lambda _b, address=email:
                       self._confirm_remove(address, entry))
        buttons.append(remove)
        box.append(buttons)
        return box

    def _confirm_remove(self, email: str, entry: dict) -> None:
        snoozed = int(entry.get("snoozed", 0))
        queued = int(entry.get("queued", 0))
        detail = (f"Remove {email} from this phone? Cached mail and "
                  "queued sends go with it; your Gmail data is "
                  "untouched.")
        if snoozed:
            detail += (f" {snoozed:d} snoozed "
                       f"{'thread is' if snoozed == 1 else 'threads are'} "
                       "moved back to the inbox on the server first.")
        if queued:
            detail += (f" {queued:d} queued "
                       f"{'send is' if queued == 1 else 'sends are'} "
                       "cancelled.")
        detail += " " + snooze_mod.UNINSTALL_WARNING
        dialog = Adw.AlertDialog()
        dialog.set_heading(f"Remove {email}?")
        dialog.set_body(detail)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance(
            "remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response",
                       lambda _d, answer: self._on_remove_answer(
                           email, answer))
        dialog.present(self)

    def _on_remove_answer(self, email: str, answer: str) -> None:
        if answer == "remove":
            self._hooks.remove_account(email)

    def _confirm_revoke(self, email: str) -> None:
        dialog = Adw.AlertDialog()
        dialog.set_heading(f"Revoke {email}?")
        dialog.set_body(
            "Google forgets this phone's access; the stored grant is "
            "deleted here too. Cached mail stays, and the next sync "
            "asks you to sign in again. Continue?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("revoke", "Revoke")
        dialog.set_response_appearance(
            "revoke", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response",
                       lambda _d, answer: self._on_revoke_answer(
                           email, answer))
        dialog.present(self)

    def _on_revoke_answer(self, email: str, answer: str) -> None:
        if answer != "revoke":
            return
        self._toast("Revoking…", None)
        self._hooks.revoke_account(email, self._on_revoked)

    def _on_revoked(self, result) -> None:
        if isinstance(result, Exception):
            self._toast("Couldn't revoke access", None)
        else:
            self._toast(str(result) or "Access revoked", None)
        self._hooks.load_state(self._on_state)

    # ----------------------------------------------------------------- backup

    def _export_backup(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Save backup")
        dialog.set_initial_name("xx-email-backup.json")
        backup_filter = Gtk.FileFilter()
        backup_filter.set_name("JSON backup")
        backup_filter.add_suffix("json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(backup_filter)
        dialog.set_filters(filters)
        try:
            dialog.save(self.get_root(), None, self._on_export_picked)
        except Exception:
            self._toast("Couldn't open the file picker", None)

    def _on_export_picked(self, dialog, result) -> None:
        try:
            target = dialog.save_finish(result)
        except Exception:
            return
        path = target.get_path() if isinstance(target, Gio.File) \
            else None
        if not path:
            self._toast("Couldn't use that file", None)
            return
        self._hooks.export_backup(path, self._on_exported)

    def _on_exported(self, result) -> None:
        if isinstance(result, Exception):
            self._toast(f"Backup failed: {result}", None)
        else:
            self._toast("Backup saved", None)

    def _import_backup(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Open backup")
        backup_filter = Gtk.FileFilter()
        backup_filter.set_name("JSON backup")
        backup_filter.add_suffix("json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(backup_filter)
        dialog.set_filters(filters)
        try:
            dialog.open(self.get_root(), None, self._on_import_picked)
        except Exception:
            self._toast("Couldn't open the file picker", None)

    def _on_import_picked(self, dialog, result) -> None:
        try:
            target = dialog.open_finish(result)
        except Exception:
            return
        path = target.get_path() if isinstance(target, Gio.File) \
            else None
        if not path:
            self._toast("Couldn't use that file", None)
            return
        self._hooks.import_backup(path, self._on_imported)

    def _on_imported(self, result) -> None:
        if isinstance(result, Exception):
            self._toast(f"Import failed: {result}", None)
            return
        restored = (result or {}).get("restored", {})
        skipped = (result or {}).get("skipped", [])
        summary = (f"Restored {restored.get('wakes', 0):d} snoozes, "
                   f"{restored.get('pages', 0):d} cursors, "
                   f"{restored.get('outbox', 0):d} queued sends")
        if skipped:
            summary += f" ({len(skipped):d} skipped)"
        self._toast(summary, None)
        self._stored = config_mod.load()
        self._hooks.load_state(self._on_state)
