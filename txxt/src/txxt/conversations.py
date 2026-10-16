"""Conversation list page: rows, list search, long-press actions.

The UI reads the SQLite store for display, sends through the daemon's
``io.piercingxx.TxxT1`` API (``TxxtClient``), and refreshes off the
``MessagesChanged`` signal — it never touches the modem stack and never
creates message rows behind the daemon. Conversation-flag edits
(pin/mute/archive/star/delete, mark unread) have no daemon method, so
the page applies them to its own store copy and re-reads; the
daemon's retry queue keys on message ids, where a removed row is a
harmless no-op.

Phone rules: ``Gtk.ListView`` with single-click activate over
``Gtk.NoSelection`` (an autoselecting selection model would pick row 0);
long-press resolves the row under the pointer (pick + walk up), never
the selection; header bars hide desktop window buttons; the search
entry stays unfocusable until tapped so the on-screen keyboard never
pops on its own. Import-safe without a display: building the page
needs one, importing this module must not.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("Gdk", "4.0")

import threading  # noqa: E402
import time  # noqa: E402

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from txxt import list_presenter as presenter  # noqa: E402


class ConversationItem(GObject.Object):
    """One ``Gio.ListStore`` entry for the conversation list."""

    __gtype_name__ = "TxxtConversationItem"
    conversation_id = GObject.Property(type=GObject.TYPE_INT64, default=0)
    title = GObject.Property(type=str, default="")
    snippet = GObject.Property(type=str, default="")
    stamp = GObject.Property(type=str, default="")
    unread = GObject.Property(type=int, default=0)
    address = GObject.Property(type=str, default="")
    marks = GObject.Property(type=str, default="")


def resolve_conversation_id(picked) -> int | None:
    """Conversation id for the widget under a pointer gesture.

    Selection, focus and pointer position are independent in GTK4, so
    a long-press must resolve its own target: walk up ``get_parent()``
    from the picked widget to the row box, which carries the id set at
    bind time.
    """
    widget = picked
    while widget is not None:
        for attr in ("_txxt_cid", "conversation_id", "txxt_cid", "cid"):
            try:
                value = getattr(widget, attr, None)
            except Exception:
                value = None
            if isinstance(value, int) and value > 0:
                return value
            if isinstance(value, bool):
                continue
        get_parent = getattr(widget, "get_parent", None)
        if not callable(get_parent):
            return None
        try:
            widget = get_parent()
        except Exception:
            return None
    return None


def tap_to_focus(entry: Gtk.Editable) -> None:
    """Focusable only on an explicit tap (never at startup or on pop).

    The entry starts unfocusable so window activation and
    ``Adw.NavigationView`` focus restore cannot grab it (which would
    pop the on-screen keyboard). A CAPTURE-phase click arms it and
    grabs focus; leaving focus disarms it again.
    """
    entry.set_can_focus(False)
    tap = Gtk.GestureClick.new()
    tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def _on_tapped(_gesture, _n_press, _x, _y) -> None:
        entry.set_can_focus(True)
        entry.grab_focus()

    tap.connect("pressed", _on_tapped)
    entry.add_controller(tap)
    focus = Gtk.EventControllerFocus.new()

    def _on_leave(_controller) -> None:
        entry.set_can_focus(False)

    focus.connect("leave", _on_leave)
    entry.add_controller(focus)


def launch_uri(parent: Gtk.Window | None, uri: str) -> None:
    """Open a tel:/sms: URI with whichever handler owns it."""
    launcher = Gtk.UriLauncher.new(uri)
    launcher.launch(parent, None, _on_uri_launched)


def _on_uri_launched(
    launcher: Gtk.UriLauncher, result: Gio.AsyncResult,
) -> None:
    try:
        launcher.launch_finish(result)
    except GLib.Error:
        pass


class ConversationListPage(Adw.NavigationPage):
    """Root page: searchable conversation list over the store."""

    def __init__(
        self, navigation: Adw.NavigationView, parent_window: Gtk.Window,
        store, resolver=None, client=None, on_open_thread=None,
    ) -> None:
        super().__init__()
        self._navigation = navigation
        self._window = parent_window
        self._store = store
        self._resolver = resolver
        self._client = client
        self._on_open_thread = on_open_thread
        self._views: list = []
        self._query_seq = 0
        try:
            from txxt.contacts import DisplayNameCache

            self._names = DisplayNameCache(resolver)
        except Exception:
            self._names = None

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Messages"))
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        settings_button = Gtk.Button(label="Settings")
        settings_button.connect("clicked", self._on_settings_clicked)
        header.pack_end(settings_button)
        toolbar.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.set_hexpand(True)
        body.set_margin_start(12)
        body.set_margin_end(12)
        body.set_margin_top(6)
        body.set_margin_bottom(6)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Search conversations")
        self._search.set_hexpand(True)
        tap_to_focus(self._search)
        body.append(self._search)

        self._quarantine_banner = Gtk.Button(label="Quarantine")
        self._quarantine_banner.set_visible(False)
        self._quarantine_banner.connect("clicked", self._on_quarantine_clicked)
        body.append(self._quarantine_banner)

        self._model = Gio.ListStore.new(ConversationItem)
        self._selection = Gtk.NoSelection.new(self._model)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_item_setup)
        factory.connect("bind", self._on_item_bind)
        self._list = Gtk.ListView.new(self._selection, factory)
        self._list.set_single_click_activate(True)
        self._list.set_vexpand(True)
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_child(self._list)
        body.append(self._scrolled)

        self._empty = Adw.StatusPage()
        self._empty.set_vexpand(True)
        self._empty.set_visible(False)
        body.append(self._empty)

        toolbar.set_content(body)
        self.set_child(toolbar)
        self.set_title("Messages")

        self._search.connect("search-changed", self._on_search_changed)
        self._list.connect("activate", self._on_row_activated)
        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)
        right_click.connect("pressed", self._on_row_right_clicked)
        self._list.add_controller(right_click)
        long_press = Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_row_long_pressed)
        self._list.add_controller(long_press)

        self.refresh()

    # ------------------------------------------------------------ loading

    def set_client(self, client) -> None:
        self._client = client

    def set_resolver(self, resolver) -> None:
        self._resolver = resolver
        try:
            self._names.set_resolver(resolver)
        except Exception:
            pass

    def refresh(self) -> None:
        """Re-read the store on a worker; results post back via idle."""
        self._query_seq += 1
        seq = self._query_seq
        query = self._search.get_text()
        worker = threading.Thread(
            target=self._load_in_worker, args=(seq, query), daemon=True,
        )
        worker.start()

    def refresh_for_signal(self, conversation_ids) -> None:
        _ = conversation_ids
        self.refresh()

    def handle_contacts_changed(
        self, _conn, _sender, _path, _iface, _signal, params, _user_data,
    ) -> None:
        try:
            self._names.handle_contacts_changed(
                _conn, _sender, _path, _iface, _signal, params, _user_data,
            )
        except Exception:
            pass
        GLib.idle_add(self._refresh_from_idle)

    def _refresh_from_idle(self) -> bool:
        try:
            self.refresh()
        except Exception:
            pass
        return False

    def _display_name(self, number: str) -> str:
        try:
            return self._names.fast(number)
        except Exception:
            pass
        if self._resolver is not None:
            try:
                return self._resolver.display_name(number)
            except Exception:
                pass
        try:
            from txxt import phone_numbers as _phones

            return _phones.format_display_configured(number) or number
        except Exception:
            return number

    def _blocking_display_name(self, number: str) -> str:
        try:
            return self._names.resolve_blocking(number)
        except Exception:
            pass
        return self._display_name(number)

    def _load_in_worker(self, seq: int, query: str) -> None:
        try:
            rows = self._store.get_all_conversations()
            by_thread = {
                row.id: self._store.get_messages_for_conversation(row.id)
                for row in rows
            }
            now = int(time.time() * 1000)
            views = presenter.present_conversations(
                rows, by_thread.get, self._display_name, now,
            )
            held = sum(1 for row in rows if row.is_quarantined)
        except Exception:
            views = None
            held = 0
        GLib.idle_add(self._apply_views, seq, views, query, held)
        if views is None:
            return
        try:
            numbers: list[str] = []
            seen: set[str] = set()
            for view in views:
                address = getattr(view, "address", "") or ""
                if address.strip() and address not in seen:
                    seen.add(address)
                    numbers.append(address)
        except Exception:
            numbers = []
        if not numbers or self._resolver is None:
            return
        try:
            for number in numbers:
                self._names.resolve_blocking(number)
        except Exception:
            return
        try:
            rows = self._store.get_all_conversations()
            by_thread = {
                row.id: self._store.get_messages_for_conversation(row.id)
                for row in rows
            }
            now = int(time.time() * 1000)
            resolved = presenter.present_conversations(
                rows, by_thread.get, self._display_name, now,
            )
            held_again = sum(1 for row in rows if row.is_quarantined)
        except Exception:
            return
        GLib.idle_add(self._apply_views, seq, resolved, query, held_again)

    def _apply_views(self, seq: int, views, query: str, held: int = 0) -> bool:
        if seq != self._query_seq:
            return False
        if views is None:
            return False
        self._views = views
        self._apply_filter(query)
        try:
            count = int(held)
        except (TypeError, ValueError):
            count = 0
        self._quarantine_banner.set_label(f"Quarantine ({count})")
        self._quarantine_banner.set_visible(count > 0)
        return False

    def _on_settings_clicked(self, _button) -> None:
        try:
            self._window.open_settings()
        except Exception:
            pass

    def _on_quarantine_clicked(self, _button) -> None:
        try:
            self._window.open_quarantine()
        except Exception:
            pass

    def _apply_filter(self, query: str) -> None:
        shown = presenter.filter_rows(self._views, query)
        self._model.remove_all()
        for view in shown:
            self._model.append(ConversationItem(
                conversation_id=view.conversation_id,
                title=view.title,
                snippet=view.snippet + view.marks,
                stamp=presenter.format_time(
                    view.timestamp_millis, int(time.time() * 1000),
                ),
                unread=view.unread_count,
                address=view.address,
            ))
        searching = bool((query or "").strip())
        self._empty.set_title("No matches" if searching else "No conversations")
        self._empty.set_description(
            "Try a different search." if searching
            else "New messages will appear here.")
        has_rows = len(shown) > 0
        self._scrolled.set_visible(has_rows)
        self._empty.set_visible(not has_rows)

    # -------------------------------------------------------------- factory

    def _on_item_setup(self, _factory, list_item: Gtk.ListItem) -> None:
        name = Gtk.Label()
        name.set_xalign(0.0)
        name.set_hexpand(True)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(24)
        stamp = Gtk.Label()
        stamp.add_css_class("txxt-timestamp")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.set_hexpand(True)
        top.append(name)
        top.append(stamp)
        snippet = Gtk.Label()
        snippet.set_xalign(0.0)
        snippet.set_hexpand(True)
        snippet.set_ellipsize(Pango.EllipsizeMode.END)
        snippet.set_max_width_chars(32)
        snippet.add_css_class("txxt-detail")
        badge = Gtk.Label()
        badge.add_css_class("txxt-badge")
        badge.set_margin_start(8)
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bottom.set_hexpand(True)
        bottom.append(snippet)
        bottom.append(badge)
        row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row_box.set_hexpand(True)
        row_box.append(top)
        row_box.append(bottom)
        list_item.name = name
        list_item.stamp = stamp
        list_item.snippet = snippet
        list_item.badge = badge
        list_item.row_box = row_box
        list_item.set_child(row_box)

    def _on_item_bind(self, _factory, list_item: Gtk.ListItem) -> None:
        item = list_item.get_item()
        list_item.set_activatable(True)
        list_item.name.set_text(item.title)
        list_item.stamp.set_text(item.stamp)
        list_item.snippet.set_text(item.snippet)
        if item.unread > 0:
            list_item.badge.set_text(str(item.unread))
            list_item.badge.set_visible(True)
        else:
            list_item.badge.set_visible(False)
        list_item.row_box._txxt_cid = int(item.conversation_id)

    # ---------------------------------------------------------------- input

    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        self._apply_filter(self._search.get_text())

    def _on_row_activated(self, _view: Gtk.ListView, position: int) -> None:
        item = self._model.get_item(position)
        if item is None or int(item.conversation_id) <= 0:
            return
        if self._on_open_thread is not None:
            self._on_open_thread(int(item.conversation_id))

    def _cid_at_position(self, x: float, y: float) -> int | None:
        try:
            picked = self._list.pick(x, y, Gtk.PickFlags.DEFAULT)
        except Exception:
            return None
        return resolve_conversation_id(picked)

    def _view_for(self, conversation_id: int):
        for view in self._views:
            if view.conversation_id == conversation_id:
                return view
        return None

    def _on_row_right_clicked(self, _gesture, _n_press, x, y) -> None:
        cid = self._cid_at_position(x, y)
        if cid:
            self._show_row_menu(cid)

    def _on_row_long_pressed(self, _gesture, x, y) -> None:
        cid = self._cid_at_position(x, y)
        if cid:
            self._show_row_menu(cid)

    # ------------------------------------------------------------ row menu

    def _show_row_menu(self, conversation_id: int) -> None:
        view = self._view_for(conversation_id)
        title = view.title if view is not None else "Conversation"
        address = view.address if view is not None else ""
        dialog = Adw.AlertDialog.new(title, address or title)
        dialog.add_response("call", "Call")
        dialog.add_response("copy", "Copy number")
        if view is not None and view.unread_count > 0:
            dialog.add_response("read", "Mark read")
        else:
            dialog.add_response("unread", "Mark unread")
        dialog.add_response("pin", "Unpin" if self._is_pinned(conversation_id) else "Pin")
        dialog.add_response("mute", "Unmute" if self._is_muted(conversation_id) else "Mute")
        dialog.add_response("archive", "Archive")
        if view is not None and "Starred" in (view.marks or ""):
            dialog.add_response("unstar", "Remove star")
        else:
            dialog.add_response("star", "Add star")
        dialog.add_response("block", "Block")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.add_response("cancel", "Cancel")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(
            self._window, None,
            lambda dlg, res: self._on_menu_chosen(
                dlg, res, conversation_id, address),
        )

    def _on_menu_chosen(self, dialog, result, conversation_id, address) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response == "cancel":
            return
        if response == "call" and address:
            launch_uri(self._window, f"tel:{address}")
        elif response == "copy" and address:
            self.get_clipboard().set(address)
        elif response == "read":
            self._mark_read(conversation_id)
        elif response == "unread":
            self._run_store(lambda: self._store.mark_conversation_unread(conversation_id))
        elif response == "pin":
            self._run_store(lambda: self._store.toggle_pin(conversation_id))
        elif response == "mute":
            self._toggle_mute(conversation_id)
        elif response == "archive":
            self._run_store(lambda: self._set_archived(conversation_id, True))
        elif response in ("star", "unstar"):
            self._run_store(
                lambda: self._set_starred(address, response == "star"))
        elif response == "block":
            self._block_sender(address)
        elif response == "delete":
            self._confirm_delete(conversation_id)

    def _is_pinned(self, conversation_id: int) -> bool:
        view = self._view_for(conversation_id)
        return bool(view is not None and view.pinned)

    def _is_muted(self, conversation_id: int) -> bool:
        view = self._view_for(conversation_id)
        return bool(view is not None and view.muted)

    def _set_archived(self, conversation_id: int, archived: bool) -> None:
        row = self._store.get_conversation(conversation_id)
        if row is None:
            return
        flags = row.flags()
        flags = flags.archive() if archived else flags.unarchive()
        self._store.set_flags(conversation_id, flags)

    def _set_starred(self, address: str, starred: bool) -> None:
        if not address:
            return
        if starred:
            self._store.star(address)
        else:
            self._store.unstar(address)

    def _toggle_mute(self, conversation_id: int) -> None:
        row = self._store.get_conversation(conversation_id)
        if row is None:
            return
        flags = row.flags()
        flags = flags.unmute() if flags.is_muted else flags.mute()
        self._store.set_flags(conversation_id, flags)

    def _run_store(self, fn) -> None:
        worker = threading.Thread(target=self._store_in_worker, args=(fn,), daemon=True)
        worker.start()

    def _store_in_worker(self, fn) -> None:
        try:
            fn()
        except Exception:
            pass
        GLib.idle_add(self._after_store_write)

    def _after_store_write(self) -> bool:
        self.refresh()
        return False

    def _mark_read(self, conversation_id: int) -> None:
        if self._client is None:
            self._run_store(
                lambda: self._store.mark_conversation_read(conversation_id))
            return

        def _done(ok, _error) -> None:
            self.refresh()

        try:
            self._client.mark_read(conversation_id, _done)
        except Exception:
            self._run_store(
                lambda: self._store.mark_conversation_read(conversation_id))

    def _block_sender(self, address: str) -> None:
        if not address:
            return
        worker = threading.Thread(
            target=self._block_in_worker, args=(address,), daemon=True)
        worker.start()

    def _block_in_worker(self, address: str) -> None:
        if self._resolver is None:
            GLib.idle_add(self._announce_block, address, False)
            return
        try:
            done = self._resolver.set_blocked(address, True)
        except Exception:
            done = False
        GLib.idle_add(self._announce_block, address, done)

    def _announce_block(self, address: str, done: bool) -> bool:
        heading = "Blocked" if done else "Could not block"
        body = (
            f"New messages from {address} are tagged."
            if done else f"No contact holds {address}; nothing changed."
        )
        dialog = Adw.AlertDialog.new(heading, body)
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.choose(self._window, None, self._on_dialog_dismissed)
        if done:
            self.refresh()
        return False

    def _confirm_delete(self, conversation_id: int) -> None:
        dialog = Adw.AlertDialog.new(
            "Delete conversation?",
            "Delete this thread and every message in it? This cannot be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(
            self._window, None,
            lambda dlg, res: self._on_delete_chosen(dlg, res, conversation_id),
        )

    def _on_delete_chosen(self, dialog, result, conversation_id: int) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response != "delete":
            return
        self._run_store(lambda: self._store.delete_conversation(conversation_id))

    def _on_dialog_dismissed(self, dialog: Adw.AlertDialog, result) -> None:
        try:
            dialog.choose_finish(result)
        except GLib.Error:
            pass
