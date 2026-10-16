"""Mailbox page: tabs, folder sheet, swipe, multi-select (thin view).

Ports MailboxScreen/MailboxViewModel over xx_email.mailbox (pure) and
the L1/L2 store: five inbox tabs in an Adw.ViewStack with a bottom
switcher, other folders in one bottom-sheet dialog, Gtk.ListView rows
with swipe + long-press multi-select, per-folder load-more, and undo
toasts. Store/network work enters through hooks and always runs off
the main loop; results land here for display only.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import time
from datetime import datetime

from gi.repository import Adw, Gtk

from xx_email import mailbox as mailbox_mod
from xx_email.domain import MailboxFolder, SnoozePresets
from xx_email.ui import common as common_mod


class MailboxPage(Gtk.Box):
    """Tabbed inbox + folder lists for one account."""

    def __init__(self, account: str, hooks, toast, swipe_left: str = "ARCHIVE",
                 swipe_right: str = "DELETE") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.account = account
        self._hooks = hooks
        self._toast = toast
        self._swipe_left = swipe_left
        self._swipe_right = swipe_right
        self._folder: MailboxFolder | None = None
        self._selection = mailbox_mod.Selection()
        self._tab_stores: dict = {}

        header = common_mod.phone_header("Inbox")
        folders_button = common_mod.text_button("Folders")
        folders_button.connect("clicked", lambda _b: self._open_sheets())
        header.pack_start(folders_button)
        compose_button = common_mod.text_button("Compose")
        compose_button.connect("clicked",
                               lambda _b: hooks.open_compose())
        header.pack_end(compose_button)
        self.append(header)

        banners = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._reauth_banner = self._banner(
            banners, "Sign in again", lambda _b: hooks.open_signin())
        self._failed_banner = self._banner(
            banners, "Retry", lambda _b: hooks.retry_failed())
        self.append(banners)

        self._sync_line = Gtk.Label(label="")
        self._sync_line.add_css_class("email-muted")
        self._sync_line.set_xalign(0.0)
        self._sync_line.set_margin_start(12)
        self._sync_line.set_margin_end(12)
        self.append(self._sync_line)

        self._stack = Adw.ViewStack()
        for tab in mailbox_mod.tabs():
            scrolled, store, _view = self._thread_list(
                lambda tid, n, _t=tab: self._row_activated(tid))
            self._tab_stores[tab] = (store, scrolled)
            self._stack.add_titled(scrolled, tab.name.lower(), tab.title)
        stack_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        stack_wrap.set_vexpand(True)
        self._tabs_box = stack_wrap
        stack_wrap.append(self._stack)
        self._switcher = Adw.ViewSwitcherBar()
        self._switcher.set_stack(self._stack)
        self._switcher.set_reveal(True)
        stack_wrap.append(self._switcher)
        self.append(stack_wrap)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=8)
        actions.set_margin_start(12)
        actions.set_margin_end(12)
        actions.set_margin_top(4)
        actions.set_margin_bottom(4)
        refresh_button = common_mod.text_button("Refresh")
        refresh_button.set_hexpand(True)
        refresh_button.connect("clicked", lambda _b: self.refresh())
        actions.append(refresh_button)
        search_button = common_mod.text_button("Search")
        search_button.set_hexpand(True)
        search_button.connect("clicked",
                              lambda _b: hooks.open_search())
        actions.append(search_button)
        self.append(actions)

        self._folder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._folder_box.set_vexpand(True)
        self._folder_box.set_visible(False)
        self._folder_scrolled, self._folder_store, _view = \
            self._thread_list(self._row_activated)
        self._load_more_button = common_mod.text_button("Load more")
        self._load_more_button.connect("clicked",
                                       lambda _b: self._on_load_more())
        self._folder_box.append(self._folder_scrolled)
        self._folder_box.append(self._load_more_button)
        self.append(self._folder_box)

        self._selection_bar = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._selection_bar.set_visible(False)
        self._selection_bar.set_margin_start(8)
        self._selection_bar.set_margin_end(8)
        self._selection_label = Gtk.Label(label="")
        self._selection_label.set_xalign(0.0)
        self._selection_bar.append(self._selection_label)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        for label, action in (("Archive", "ARCHIVE"),
                              ("Delete", "DELETE"),
                              ("Unread", "MARK_UNREAD"),
                              ("Star", "STAR"),
                              ("Snooze", "SNOOZE")):
            button = common_mod.text_button(label)
            button.connect("clicked",
                           lambda _b, a=action: self._on_bulk(a))
            flow.append(button)
        clear = common_mod.text_button("Clear")
        clear.connect("clicked", lambda _b: self._clear_selection())
        flow.append(clear)
        self._selection_bar.append(flow)
        self.append(self._selection_bar)

    # ------------------------------------------------------------------ lists

    def _thread_list(self, row_activated):
        store = common_mod.new_thread_store()
        view = common_mod.thread_list_view(
            store, row_activated, self._toggle_select, self._on_swipe,
            self._on_star, self._swipe_left, self._swipe_right)
        scrolled = Gtk.ScrolledWindow()
        scrolled.add_css_class("email-listview")
        scrolled.set_vexpand(True)
        scrolled.set_child(view)
        return (scrolled, store, view)

    def _row_activated(self, thread_id: str, _n_press: int = 1) -> None:
        if len(self._selection):
            self._toggle_select(thread_id)
        else:
            self._hooks.open_thread(thread_id)

    def _toggle_select(self, thread_id: str) -> None:
        self._selection.toggle(thread_id)
        self._paint_selection()

    def _clear_selection(self) -> None:
        self._selection.clear()
        self._paint_selection()

    def _paint_selection(self) -> None:
        count = len(self._selection)
        self._selection_bar.set_visible(count > 0)
        self._selection_label.set_label(f"{count:d} selected")
        for store, _ in list(self._tab_stores.values()) + \
                [(self._folder_store, None)]:
            for position in range(store.get_n_items()):
                item = store.get_item(position)
                item.selected = item.thread_id in self._selection

    # ------------------------------------------------------------------ data

    def load(self) -> None:
        for tab in mailbox_mod.tabs():
            self._reload_tab(tab)
        self._hooks.failed_count(self.set_failed)

    def _reload_tab(self, tab: MailboxFolder) -> None:
        store, _ = self._tab_stores[tab]

        def _done(result) -> None:
            if isinstance(result, Exception):
                self._toast(f"Couldn't load {tab.title}", None)
                return
            threads, _more = result
            common_mod.replace_store(store, threads)

        self._hooks.load_folder(tab, _done)

    def select_folder(self, folder: MailboxFolder | None) -> None:
        self._folder = folder
        self._selection.clear()
        showing_tabs = folder is None
        self._tabs_box.set_visible(showing_tabs)
        self._switcher.set_visible(showing_tabs)
        self._folder_box.set_visible(not showing_tabs)
        if folder is None:
            self.load()
            return

        def _done(result) -> None:
            if isinstance(result, Exception):
                self._toast(f"Couldn't load {folder.title}", None)
                return
            threads, more = result
            common_mod.replace_store(self._folder_store, threads)
            self._load_more_button.set_visible(bool(more))
            self._load_more_button.set_label("Load more")

        if folder == MailboxFolder.OUTBOX:
            self._hooks.load_outbox(self._done_outbox)
        else:
            self._hooks.load_folder(folder, _done)
        self._paint_selection()

    def _done_outbox(self, result) -> None:
        if isinstance(result, Exception):
            self._toast("Couldn't load Outbox", None)
            return
        entries, _more = result
        store = self._folder_store
        store.remove_all()
        for entry in entries:
            store.append(common_mod.ThreadItem.from_thread(
                _outbox_row(entry)))
        self._load_more_button.set_visible(False)

    def refresh(self) -> None:
        self._sync_line.set_label(
            mailbox_mod.sync_status_line(syncing=True))
        self._hooks.refresh(self._on_refreshed)

    def _on_refreshed(self, outcome) -> None:
        if isinstance(outcome, Exception):
            self._sync_line.set_label(
                mailbox_mod.sync_status_line(
                    {"ok": False, "error": "couldn't reach it"}))
            self._toast("Sync failed", None)
            return
        if not outcome.get("ok", False):
            self._toast(outcome.get("error") or "Sync failed", None)
        self._sync_line.set_label(
            mailbox_mod.sync_status_line(outcome))
        self.set_reauth(bool(outcome.get("reauth", False)))
        self.load()

    def set_reauth(self, needed: bool) -> None:
        self._reauth_banner.set_visible(needed)

    def set_failed(self, result) -> None:
        count = 0 if isinstance(result, Exception) else int(result or 0)
        self._failed_banner.set_visible(count > 0)
        if count > 0:
            self._failed_label.set_label(
                f"{count:d} send{'' if count == 1 else 's'} failed")

    # ---------------------------------------------------------------- actions

    def _on_swipe(self, action: str, thread_id: str) -> None:
        if action == "SNOOZE":
            self._open_snooze([thread_id])
        else:
            self._perform(action, [thread_id])

    def _on_star(self, thread_id: str) -> None:
        self._perform("STAR", [thread_id])

    def _on_bulk(self, action: str) -> None:
        ids = self._selection.ids()
        if not ids:
            return
        if action == "SNOOZE":
            self._open_snooze(ids)
        else:
            self._perform(action, ids)

    def _perform(self, action: str, ids: list[str], wake_at=None,
                 label_id=None) -> None:
        def _done(result) -> None:
            if isinstance(result, Exception):
                self._toast("Action failed — you stay put", None)
                self._reload_current()
                return
            self._toast(result.message,
                         lambda: self._revert(result))
            self._selection.clear()
            self._paint_selection()
            self._reload_current()

        self._hooks.perform(action, ids, wake_at, label_id, _done)

    def _revert(self, undo) -> None:
        def _done(result) -> None:
            if isinstance(result, Exception):
                self._toast("Undo failed", None)
                return
            self._reload_current()

        self._hooks.revert(undo, _done)

    def _reload_current(self) -> None:
        if self._folder is None:
            for tab in mailbox_mod.tabs():
                self._reload_tab(tab)
        else:
            folder = self._folder
            self.select_folder(None)
            self.select_folder(folder)

    def _on_load_more(self) -> None:
        if self._folder is None:
            return
        self._load_more_button.set_label("Loading…")

        def _done(result) -> None:
            if isinstance(result, Exception):
                self._load_more_button.set_label("Load more")
                self._toast("Couldn't load more", None)
                return
            threads, more = result
            for thread in threads:
                self._folder_store.append(
                    common_mod.ThreadItem.from_thread(thread))
            self._load_more_button.set_visible(bool(more))
            self._load_more_button.set_label("Load more")

        self._hooks.load_more(self._folder, _done)

    # ---------------------------------------------------------------- sheets

    def _banner(self, parent, action_label: str, on_action):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("email-banner")
        box.set_visible(False)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(4)
        label = common_mod.wrapped(Gtk.Label(label=""))
        box.append(label)
        action = common_mod.text_button(action_label)
        action.connect("clicked", on_action)
        box.append(action)
        parent.append(box)
        if action_label == "Retry":
            self._failed_label = label
        return box

    def _open_sheets(self) -> None:
        def _done(result) -> None:
            accounts = [] if isinstance(result, Exception) else result
            self._folder_dialog(accounts)

        self._hooks.load_accounts(_done)

    def _folder_dialog(self, accounts: list[str]) -> None:
        dialog = Adw.Dialog()
        dialog.set_title("Folders")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                          spacing=4)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(content)
        dialog.set_child(scrolled)

        def _row(label_text, callback) -> None:
            button = common_mod.text_button(label_text)
            button.connect("clicked",
                           lambda _b: (dialog.close(), callback()))
            content.append(button)

        for email in accounts:
            marker = " (current)" if email == self.account else ""
            _row(email + marker,
                 lambda e=email: self._hooks.switch_account(e)
                 if e != self.account else None)
        _row("Add account", lambda: self._hooks.add_account())
        _row("Sign out " + self.account,
             lambda: self._confirm_sign_out())
        _row("Revoke Google access",
             lambda: self._hooks.open_permissions())
        _row("Inbox (tabs)", lambda: self.select_folder(None))
        for folder in mailbox_mod.OTHER_FOLDERS:
            _row(folder.title,
                 lambda f=folder: self.select_folder(f))
        _row("Settings", lambda: self._hooks.open_settings())
        dialog.present(self.get_root())

    def _confirm_sign_out(self) -> None:
        email = self.account
        dialog = Adw.Dialog()
        dialog.set_title("Sign out")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                          spacing=8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        text = common_mod.wrapped(Gtk.Label(
            label=f"Remove {email} from this phone? Mail stays on "
                  "Google's servers. Sends still waiting in the "
                  "Outbox are discarded."))
        content.append(text)
        cancel = common_mod.text_button("Cancel")
        cancel.connect("clicked", lambda _b: dialog.close())
        content.append(cancel)
        out = common_mod.text_button("Sign out")
        out.connect("clicked", lambda _b: (
            dialog.close(), self._hooks.sign_out(email, False)))
        content.append(out)
        revoke = common_mod.text_button("Sign out and revoke")
        revoke.connect("clicked", lambda _b: (
            dialog.close(), self._hooks.sign_out(email, True)))
        content.append(revoke)
        dialog.set_child(content)
        dialog.present(self.get_root())

    def _open_snooze(self, ids: list[str]) -> None:
        now = datetime.now().astimezone()
        presets = (
            ("Later today", SnoozePresets.later_today(now)),
            ("Tomorrow morning", SnoozePresets.tomorrow_morning(now)),
            ("Weekend", SnoozePresets.weekend(now)),
            ("Next week", SnoozePresets.next_week(now)),
        )
        dialog = Adw.Dialog()
        dialog.set_title("Snooze until…")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                          spacing=4)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        dialog.set_child(content)
        for label_text, moment in presets:
            button = common_mod.text_button(label_text)
            wake_at = int(moment.timestamp() * 1000)

            def _pick(_b, undo_ids=list(ids), at=wake_at) -> None:
                dialog.close()
                self._perform("SNOOZE", undo_ids, wake_at=at)

            button.connect("clicked", _pick)
            content.append(button)
        dialog.present(self.get_root())


def _outbox_row(entry):
    """Project an outbox row onto a thread-shaped display row (pure)."""
    return mailbox_mod.outbox_display_row(entry,
                                          int(time.time() * 1000))
