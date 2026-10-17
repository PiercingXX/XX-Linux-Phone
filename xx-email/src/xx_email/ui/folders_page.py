"""Folders page: accounts plus every mailbox folder (thin view).

Replaces the old folder bottom-sheet dialog with a full page so it
fits the phone, screenshots cleanly and matches the design system:
one tap switches account or folder, rarely used account actions live
here instead of on the list. Folder contents still render through
hooks on worker threads — this page only shows state.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from xx_email import mailbox as mailbox_mod
from xx_ui import widgets


def _account_subtitle(entry: dict) -> str:
    parts = []
    if entry.get("snoozed"):
        parts.append(f"{int(entry['snoozed']):d} snoozed")
    if entry.get("queued"):
        parts.append(f"{int(entry['queued']):d} queued")
    return ", ".join(parts) or "Signed in"


class FoldersPage(Gtk.Box):
    """Account switcher and folder list for one account."""

    def __init__(self, account: str, hooks, toast) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.account = account
        self._hooks = hooks
        self._toast = toast

        header = widgets.compact_header(
            lambda: hooks.go_back(), "Folders", account)
        self.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.add_css_class("xx-page-body")
        body.set_vexpand(True)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER,
                            Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_vexpand(True)
        scrolled.set_child(inner)
        body.append(scrolled)
        self.append(body)

        inner.append(widgets.section_label("Accounts"))
        self._accounts_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.append(self._accounts_box)
        loading = widgets.label("Loading accounts…", "xx-secondary")
        self._accounts_box.append(loading)

        inner.append(widgets.section_label("Folders"))
        folders = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.append(folders)
        inbox_row = widgets.list_row(
            "Inbox", "Primary, Social, Promotions, Updates, Forums",
            on_activate=lambda: hooks.show_folder(None))
        folders.append(inbox_row)
        others = list(mailbox_mod.OTHER_FOLDERS)
        for index, folder in enumerate(others):
            row = widgets.list_row(
                folder.title, "",
                on_activate=lambda f=folder: hooks.show_folder(f),
                last=index == len(others) - 1)
            folders.append(row)

        inner.append(widgets.section_label("This account"))
        account_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              spacing=0)
        inner.append(account_box)
        account_box.append(widgets.list_row(
            "Add account", "Sign in with Google in your browser",
            on_activate=lambda: hooks.add_account()))
        account_box.append(widgets.list_row(
            f"Sign out {account}", "Mail stays on Google's servers",
            on_activate=lambda: self._confirm_sign_out()))
        account_box.append(widgets.list_row(
            "Revoke Google access", "Open your Google permissions",
            on_activate=lambda: hooks.open_permissions(), last=True))

        hooks.load_state(self._on_state)

    # ------------------------------------------------------------------ state

    def _on_state(self, result) -> None:
        if isinstance(result, Exception):
            return
        _config, accounts = result
        child = self._accounts_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._accounts_box.remove(child)
            child = next_child
        if not accounts:
            self._accounts_box.append(widgets.label(
                "No accounts yet.", "xx-secondary"))
            return
        for index, entry in enumerate(accounts):
            email = entry.get("email", "")
            marker = " (current)" if email == self.account else ""
            row = widgets.list_row(
                email + marker, _account_subtitle(entry),
                on_activate=lambda e=email: self._maybe_switch(e),
                last=index == len(accounts) - 1)
            self._accounts_box.append(row)

    def _maybe_switch(self, email: str) -> None:
        if email != self.account:
            self._hooks.switch_account(email)

    def _confirm_sign_out(self) -> None:
        email = self.account
        dialog = Adw.Dialog()
        dialog.set_title("Sign out")
        content = widgets.dialog_box("Sign out")
        content.append(widgets.label(
            f"Remove {email} from this phone? Mail stays on "
            "Google's servers. Sends still waiting in the "
            "Outbox are discarded.", "xx-body"))
        cancel = widgets.outline_button("Cancel")
        cancel.connect("clicked", lambda _b: dialog.close())
        content.append(cancel)
        out = widgets.outline_button("Sign out")
        out.connect("clicked", lambda _b: (
            dialog.close(), self._hooks.sign_out(email, False)))
        content.append(out)
        revoke = widgets.danger_button("Sign out and revoke")
        revoke.connect("clicked", lambda _b: (
            dialog.close(), self._hooks.sign_out(email, True)))
        content.append(revoke)
        dialog.set_child(content)
        dialog.present(self.get_root())


def folders_navigation_page(page: FoldersPage) -> Adw.NavigationPage:
    navigation = Adw.NavigationPage(title="Folders")
    navigation.set_child(page)
    return navigation
