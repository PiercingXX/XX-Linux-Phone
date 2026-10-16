"""Quarantine page: review held messages from unknown senders.

Ports ``ui/QuarantineActivity``: threads held by the unknown-sender rule
(``is_quarantined``) are hidden from the main list and reviewed here —
Deliver releases the thread to the inbox (and overrides the sender so
future messages land there), Block adds the sender to the blocklist and
deletes the thread, Delete drops the thread. Contact display names
resolve through the shared resolver; bare numbers never render blank.
"""

from __future__ import annotations

import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from txxt import blocking as blocking_mod  # noqa: E402


def resolve_quarantine_id(picked) -> int | None:
    """Held-thread id for the widget under a pointer gesture (pick + walk up)."""
    widget = picked
    while widget is not None:
        try:
            value = getattr(widget, '_txxt_cid', None)
        except Exception:
            value = None
        if isinstance(value, int) and value > 0:
            return value
        get_parent = getattr(widget, 'get_parent', None)
        if not callable(get_parent):
            return None
        try:
            widget = get_parent()
        except Exception:
            return None
    return None


class QuarantineItem(GObject.Object):
    """One ``Gio.ListStore`` entry for a held thread."""

    __gtype_name__ = 'TxxtQuarantineItem'
    conversation_id = GObject.Property(type=GObject.TYPE_INT64, default=0)
    title = GObject.Property(type=str, default='')
    snippet = GObject.Property(type=str, default='')
    address = GObject.Property(type=str, default='')


class QuarantinePage(Adw.NavigationPage):
    """Held-thread review over the store's quarantined conversations."""

    def __init__(
        self, navigation: Adw.NavigationView, parent_window: Gtk.Window,
        store, resolver=None, on_changed=None,
    ) -> None:
        super().__init__()
        self._navigation = navigation
        self._window = parent_window
        self._store = store
        self._resolver = resolver
        self._on_changed = on_changed
        self.conversation_id = 0
        self.set_title('Quarantine')

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label='Quarantine'))
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        toolbar.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.set_hexpand(True)
        body.set_margin_start(12)
        body.set_margin_end(12)
        body.set_margin_top(6)
        body.set_margin_bottom(6)

        self._model = Gio.ListStore.new(QuarantineItem)
        selection = Gtk.NoSelection.new(self._model)
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', self._on_item_setup)
        factory.connect('bind', self._on_item_bind)
        self._list = Gtk.ListView.new(selection, factory)
        self._list.set_single_click_activate(True)
        self._list.set_vexpand(True)
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_child(self._list)
        body.append(self._scrolled)

        self._empty = Adw.StatusPage()
        self._empty.set_title('Nothing held')
        self._empty.set_description('Unknown senders will appear here.')
        self._empty.set_vexpand(True)
        self._empty.set_visible(False)
        body.append(self._empty)

        toolbar.set_content(body)
        self.set_child(toolbar)

        self._list.connect('activate', self._on_row_activated)
        long_press = Gtk.GestureLongPress.new()
        long_press.connect('pressed', self._on_row_long_pressed)
        self._list.add_controller(long_press)
        self.refresh()

    # ------------------------------------------------------------ loading

    def set_resolver(self, resolver) -> None:
        self._resolver = resolver

    def held_count(self) -> int:
        """Held threads right now (badge source for the root page)."""
        try:
            return sum(
                1 for row in self._store.get_all_conversations()
                if row.is_quarantined
            )
        except Exception:
            return 0

    def refresh(self) -> None:
        worker = threading.Thread(target=self._load_in_worker, daemon=True)
        worker.start()

    def refresh_for_signal(self, conversation_ids) -> None:
        _ = conversation_ids
        self.refresh()

    def handle_contacts_changed(
        self, _conn, _sender, _path, _iface, _signal, params, _user_data,
    ) -> None:
        try:
            params.unpack()
        except Exception:
            pass
        GLib.idle_add(self._refresh_from_idle)

    def _display_name(self, number: str) -> str:
        if self._resolver is not None:
            try:
                name = self._resolver.display_name(number)
                if (name or '').strip():
                    return name
            except Exception:
                pass
        try:
            from txxt import phone_numbers as _phones

            return _phones.format_display_configured(number) or number
        except Exception:
            return number

    def _load_in_worker(self) -> None:
        try:
            rows = [
                row for row in self._store.get_all_conversations()
                if row.is_quarantined
            ]
            items = []
            for row in rows:
                try:
                    messages = self._store.get_messages_for_conversation(row.id)
                except Exception:
                    messages = []
                snippet = messages[-1].body if messages else ''
                sender = self._store.quarantine_sender(row.id) or ''
                items.append((row.id, self._display_name(sender), snippet, sender))
        except Exception:
            items = None
        GLib.idle_add(self._apply_items, items)

    def _apply_items(self, items) -> bool:
        if items is None:
            return False
        self._model.remove_all()
        for conversation_id, title, snippet, address in items:
            self._model.append(QuarantineItem(
                conversation_id=conversation_id,
                title=title,
                snippet=snippet,
                address=address,
            ))
        has_rows = len(items) > 0
        self._scrolled.set_visible(has_rows)
        self._empty.set_visible(not has_rows)
        return False

    # ------------------------------------------------------------ factory

    def _on_item_setup(self, _factory, list_item: Gtk.ListItem) -> None:
        name = Gtk.Label()
        name.set_xalign(0.0)
        name.set_hexpand(True)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(24)
        snippet = Gtk.Label()
        snippet.set_xalign(0.0)
        snippet.set_hexpand(True)
        snippet.set_ellipsize(Pango.EllipsizeMode.END)
        snippet.set_max_width_chars(32)
        snippet.add_css_class('txxt-muted')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_hexpand(True)
        box.append(name)
        box.append(snippet)
        list_item.set_child(box)

    def _on_item_bind(self, _factory, list_item: Gtk.ListItem) -> None:
        item = list_item.get_item()
        box = list_item.get_child()
        name = box.get_first_child()
        snippet = name.get_next_sibling()
        name.set_text(item.title)
        snippet.set_text(item.snippet)
        box._txxt_cid = int(item.conversation_id)

    # ------------------------------------------------------------ actions

    def _resolve_id(self, list_item_position: int) -> int | None:
        try:
            item = self._model.get_item(list_item_position)
        except Exception:
            return None
        return int(item.conversation_id) if item is not None else None

    def _on_row_activated(self, view: Gtk.ListView, position: int) -> None:
        conversation_id = self._resolve_id(position)
        if conversation_id:
            self._prompt_disposition(conversation_id)

    def _on_row_long_pressed(self, _gesture, x: float, y: float) -> None:
        try:
            picked = self._list.pick(x, y, Gtk.PickFlags.DEFAULT)
        except Exception:
            return
        conversation_id = resolve_quarantine_id(picked)
        if conversation_id:
            self._prompt_disposition(conversation_id)

    def _prompt_disposition(self, conversation_id: int) -> None:
        try:
            sender = self._store.quarantine_sender(conversation_id) or ''
        except Exception:
            sender = ''
        title = self._display_name(sender) if sender else 'Held thread'
        dialog = Adw.AlertDialog.new(title, 'Release, block, or delete this thread?')
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('deliver', 'Deliver')
        dialog.add_response('block', 'Block')
        dialog.add_response('delete', 'Delete')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(
            self._window, None,
            lambda dlg, res: self._on_disposition_chosen(
                dlg, res, conversation_id, sender,
            ),
        )

    def _on_disposition_chosen(
        self, dialog, result, conversation_id: int, sender: str,
    ) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response == 'cancel':
            return
        if response == 'deliver':
            self._run_store(lambda: self._deliver(conversation_id, sender))
        elif response == 'block':
            self._run_store(lambda: self._block(conversation_id, sender))
        elif response == 'delete':
            self._run_store(lambda: self._store.quarantine_delete(conversation_id))

    # ------------------------------------------------------------ mutations

    def _run_store(self, fn) -> None:
        worker = threading.Thread(target=self._store_in_worker, args=(fn,), daemon=True)
        worker.start()

    def _store_in_worker(self, fn) -> None:
        try:
            fn()
        except Exception:
            pass
        try:
            blocking_mod.live_apply_from_store(self._store)
        except Exception:
            pass
        GLib.idle_add(self._refresh_from_idle)

    def _refresh_from_idle(self) -> bool:
        self.refresh()
        if self._on_changed is not None:
            try:
                self._on_changed()
            except Exception:
                pass
        return False

    def _deliver(self, conversation_id: int, sender: str) -> None:
        self._store.quarantine_deliver(conversation_id)
        if sender.strip():
            blocking_mod.add_persistent_override(self._store, sender)

    def _block(self, conversation_id: int, sender: str) -> None:
        if sender.strip():
            try:
                self._store.blocklist_add('sender', sender)
            except Exception:
                pass
            if self._resolver is not None:
                try:
                    self._resolver.set_blocked(sender, True)
                except Exception:
                    pass
        self._store.quarantine_delete(conversation_id)
