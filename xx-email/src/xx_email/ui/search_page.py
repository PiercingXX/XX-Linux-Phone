"""Search page: operator hints plus offline FTS (thin view).

Ports SearchScreen: operator queries (anything with a colon) go to
the server as Gmail ``q``; plain terms hit the local FTS index first
and fall back to the server when empty. Results render without swipe
or star chrome. The entry sits at the bottom in thumb reach and never
takes focus on its own — the keyboard opens only on tap.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from xx_email import mailbox as mailbox_mod
from xx_email.ui import common as common_mod


class SearchPage(Gtk.Box):
    """Operator + offline search for one account."""

    def __init__(self, account: str, hooks, toast) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.account = account
        self._hooks = hooks
        self._toast = toast
        self._searched = False

        header = common_mod.phone_header("Search")
        back = common_mod.text_button("Back")
        back.connect("clicked", lambda _b: hooks.go_back())
        header.pack_start(back)
        self.append(header)

        self._store = common_mod.new_thread_store()
        view = common_mod.thread_list_view(
            self._store, lambda tid, _n: hooks.open_thread(tid),
            None, None, None, "", "")
        scrolled = Gtk.ScrolledWindow()
        scrolled.add_css_class("email-listview")
        scrolled.set_vexpand(True)
        scrolled.set_child(view)
        self.append(scrolled)

        self._status = Gtk.Label(label="")
        self._status.add_css_class("email-muted")
        self._status.set_xalign(0.0)
        self._status.set_margin_start(12)
        self._status.set_margin_end(12)
        self.append(self._status)

        self._hints = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              spacing=4)
        self._hints.set_margin_start(16)
        self._hints.set_margin_end(16)
        hint_title = Gtk.Label(label="Try:")
        hint_title.set_xalign(0.0)
        self._hints.append(hint_title)
        for hint in mailbox_mod.OPERATOR_HINTS:
            button = common_mod.text_button("")
            button.set_child(common_mod.ellipsized(
                Gtk.Label(label=hint)))
            button.connect("clicked",
                           lambda _b, h=hint: self._run(h))
            self._hints.append(button)
        self.append(self._hints)

        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=8)
        entry_row.set_margin_start(12)
        entry_row.set_margin_end(12)
        entry_row.set_margin_top(8)
        entry_row.set_margin_bottom(12)
        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text(
            "Search mail (from:, has:attachment …)")
        self._entry.set_hexpand(True)
        self._entry.set_can_focus(False)
        focus_tap = Gtk.GestureClick()
        focus_tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        focus_tap.connect("pressed", lambda *_a: self._entry.set_can_focus(True))
        self._entry.add_controller(focus_tap)
        self._entry.connect("activate", lambda _e: self._run(None))
        entry_row.append(self._entry)
        go = common_mod.text_button("Search")
        go.connect("clicked", lambda _b: self._run(None))
        entry_row.append(go)
        self.append(entry_row)

    def _run(self, preset: str | None) -> None:
        query = preset if preset is not None else \
            self._entry.get_text().strip()
        if not query:
            return
        if preset is not None:
            self._entry.set_text(preset)
        self._status.set_label("Searching…")

        def _done(result) -> None:
            if isinstance(result, Exception):
                self._status.set_label("")
                self._toast("Search failed", None)
                return
            threads, server, offline = result
            self._searched = True
            self._hints.set_visible(False)
            common_mod.replace_store(self._store, threads)
            self._status.set_label(
                mailbox_mod.search_status(bool(server), bool(offline),
                                          len(threads)))

        self._hooks.search(query, _done)


def search_dialog_page(account: str, hooks, toast) -> Adw.NavigationPage:
    page = SearchPage(account, hooks, toast)
    navigation = Adw.NavigationPage(title="Search")
    navigation.set_child(page)
    return navigation
