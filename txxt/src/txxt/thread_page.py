"""Conversation thread page: messages, in-thread search, compose.

The page reads its thread from the SQLite store for display and sends
through the daemon's ``io.piercingxx.TxxT1`` API (``TxxtClient``) —
never the modem stack, never message rows behind the daemon. Opening the
page marks the thread read via ``MarkRead``; refreshes arrive off the
``MessagesChanged`` signal. Rows are text-first lines (never bubbles):
inbound left, outbound right, timestamps and delivery state under each
outbound row, a Retry action on held-unsent SMS rows.

Phone rules mirror the list page: ``Gtk.ListView`` with
single-click activate over ``Gtk.NoSelection``, header bars without
desktop window buttons, and a compose entry that stays unfocusable
until tapped. Import-safe without a display.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("Gdk", "4.0")

import os  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from txxt import compose as compose_mod  # noqa: E402
from txxt import list_presenter as presenter  # noqa: E402
from txxt.conversations import launch_uri, tap_to_focus  # noqa: E402

#: Staged-photo cache lifetime (mirrors Android ``PhotoStaging`` TTL).
STAGED_TTL_MILLIS = 24 * 60 * 60 * 1000
#: Row thumbnail long side (never full-res into a 360 px row).
THUMBNAIL_PX = 320


class MessageItem(GObject.Object):
    """One ``Gio.ListStore`` entry for the thread."""

    __gtype_name__ = "TxxtMessageItem"
    message_id = GObject.Property(type=GObject.TYPE_INT64, default=0)
    body = GObject.Property(type=str, default="")
    stamp = GObject.Property(type=str, default="")
    outgoing = GObject.Property(type=bool, default=False)
    state = GObject.Property(type=str, default="")
    show_retry = GObject.Property(type=bool, default=False)
    address = GObject.Property(type=str, default="")
    media_path = GObject.Property(type=str, default="")
    is_photo = GObject.Property(type=bool, default=False)


def staging_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(root) / "txxt" / "attachments"


def sweep_staged_photos(ttl_millis: int = STAGED_TTL_MILLIS) -> int:
    """Delete orphaned staged photos older than the TTL; returns count."""
    target = staging_dir()
    try:
        entries = list(target.iterdir())
    except OSError:
        return 0
    now = int(time.time() * 1000)
    removed = 0
    for entry in entries:
        try:
            age = now - int(entry.stat().st_mtime * 1000)
        except OSError:
            continue
        if age > ttl_millis:
            try:
                entry.unlink()
                removed += 1
            except OSError:
                continue
    return removed


def thumbnail_pixbuf(path: str, long_side: int = THUMBNAIL_PX):
    """Scaled pixbuf for ``path`` (None when undecodable); never full-res."""
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
    except Exception:
        return None
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(
            path, long_side, long_side, True,
        )
    except Exception:
        return None


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes // 1024} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def stamp_text(timestamp_millis: int) -> str:
    moment = datetime.fromtimestamp(timestamp_millis / 1000).astimezone()
    now = datetime.now().astimezone()
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    return moment.strftime("%Y-%m-%d %H:%M")


class ThreadPage(Adw.NavigationPage):
    """One conversation thread with compose."""

    def __init__(
        self, navigation: Adw.NavigationView, parent_window: Gtk.Window,
        store, resolver=None, client=None, conversation_id: int = 0,
    ) -> None:
        super().__init__()
        self._navigation = navigation
        self._window = parent_window
        self._store = store
        self._resolver = resolver
        self._client = client
        self._conversation_id = int(conversation_id)
        self._gate = compose_mod.SendGate()
        try:
            from txxt.contacts import DisplayNameCache

            self._names = DisplayNameCache(resolver)
        except Exception:
            self._names = None
        self._address = ""
        self._messages: list = []
        self._matches: list = []
        self._cursor = -1
        self._query_seq = 0

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        self._title = Gtk.Label(label="Thread")
        self._title.set_ellipsize(Pango.EllipsizeMode.END)
        self._title.set_max_width_chars(20)
        header.set_title_widget(self._title)
        self._call_button = Gtk.Button(label="Call")
        self._call_button.connect("clicked", self._on_call_clicked)
        header.pack_end(self._call_button)
        self._search_toggle = Gtk.Button(label="Search")
        self._search_toggle.connect("clicked", self._on_search_toggled)
        header.pack_end(self._search_toggle)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.set_hexpand(True)

        self._search_bar = Gtk.SearchBar()
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_box.set_hexpand(True)
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_hexpand(True)
        self._search_entry.set_placeholder_text("Search in thread")
        tap_to_focus(self._search_entry)
        self._prev_button = Gtk.Button(label="Up")
        self._prev_button.connect("clicked", self._on_prev_match)
        self._next_button = Gtk.Button(label="Down")
        self._next_button.connect("clicked", self._on_next_match)
        self._count = Gtk.Label(label="")
        self._count.add_css_class("txxt-timestamp")
        search_box.append(self._search_entry)
        search_box.append(self._prev_button)
        search_box.append(self._next_button)
        search_box.append(self._count)
        self._search_bar.set_child(search_box)
        content.append(self._search_bar)

        self._model = Gio.ListStore.new(MessageItem)
        self._selection = Gtk.NoSelection.new(self._model)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_item_setup)
        factory.connect("bind", self._on_item_bind)
        self._list = Gtk.ListView.new(self._selection, factory)
        self._list.set_single_click_activate(True)
        self._list.set_vexpand(True)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(self._list)
        content.append(scrolled)

        self._staged_path: str | None = None
        self._staged_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._staged_row.set_margin_start(12)
        self._staged_row.set_margin_end(12)
        self._staged_thumb = Gtk.Picture()
        self._staged_thumb.set_can_shrink(True)
        self._staged_thumb.set_size_request(96, 72)
        self._staged_label = Gtk.Label(label="")
        self._staged_label.set_hexpand(True)
        self._staged_label.set_xalign(0.0)
        self._staged_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._staged_clear = Gtk.Button(label="✕")
        self._staged_clear.connect("clicked", self._on_staged_clear)
        self._staged_row.append(self._staged_thumb)
        self._staged_row.append(self._staged_label)
        self._staged_row.append(self._staged_clear)
        self._staged_row.set_visible(False)
        content.append(self._staged_row)

        compose = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        compose.add_css_class("txxt-compose")
        compose.set_margin_start(12)
        compose.set_margin_end(12)
        compose.set_margin_top(8)
        compose.set_margin_bottom(8)
        self._attach_button = Gtk.Button(label="⊕")
        self._attach_button.connect("clicked", self._on_attach_clicked)
        self._entry = Gtk.Entry()
        self._entry.set_hexpand(True)
        self._entry.set_placeholder_text("Message")
        tap_to_focus(self._entry)
        self._send_button = Gtk.Button(label="Send")
        self._send_button.add_css_class("suggested-action")
        self._send_button.connect("clicked", self._on_send_clicked)
        compose.append(self._attach_button)
        compose.append(self._entry)
        compose.append(self._send_button)
        content.append(compose)

        toolbar.set_content(content)
        self.set_child(toolbar)
        self.set_title("Thread")

        self._search_entry.connect("search-changed", self._on_thread_search)
        self._list.connect("activate", self._on_row_activated)

        try:
            sweep_staged_photos()
        except Exception:
            pass
        self.refresh()
        self._mark_read()

    # ------------------------------------------------------------ loading

    def set_client(self, client) -> None:
        self._client = client

    def set_resolver(self, resolver) -> None:
        self._resolver = resolver
        try:
            self._names.set_resolver(resolver)
        except Exception:
            pass

    @property
    def conversation_id(self) -> int:
        return self._conversation_id

    def refresh(self) -> None:
        self._query_seq += 1
        seq = self._query_seq
        worker = threading.Thread(
            target=self._load_in_worker, args=(seq,), daemon=True,
        )
        worker.start()

    def refresh_for_signal(self, conversation_ids) -> None:
        if str(self._conversation_id) in [str(cid) for cid in conversation_ids]:
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

    def _dialables_for(self, row) -> list[str]:
        try:
            dialables = row.dialable_list()  # type: ignore[attr-defined]
            cleaned = [p.strip() for p in dialables if p.strip()]
            if cleaned:
                return cleaned
        except Exception:
            pass
        try:
            primary = row.primary_dialable()  # type: ignore[attr-defined]
            if primary and primary.strip():
                return [primary.strip()]
        except Exception:
            pass
        try:
            return [p for p in row.addresses() if p.strip()]
        except Exception:
            return []

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

    def _load_in_worker(self, seq: int) -> None:
        try:
            row = self._store.get_conversation(self._conversation_id)
            if row is None:
                GLib.idle_add(self._apply_missing, seq)
                return
            messages = self._store.get_messages_for_conversation(
                self._conversation_id)
            addresses = self._dialables_for(row)
            name = presenter.row_title(addresses, self._display_name)
            address = addresses[0] if addresses else ""
        except Exception:
            GLib.idle_add(self._apply_missing, seq)
            return
        GLib.idle_add(self._apply_loaded, seq, name, address, messages)
        if not addresses or self._resolver is None:
            return
        try:
            for number in addresses:
                self._names.resolve_blocking(number)
        except Exception:
            return
        try:
            row = self._store.get_conversation(self._conversation_id)
            if row is None:
                return
            messages = self._store.get_messages_for_conversation(
                self._conversation_id)
            addresses = self._dialables_for(row)
            name = presenter.row_title(addresses, self._display_name)
            address = addresses[0] if addresses else ""
        except Exception:
            return
        GLib.idle_add(self._apply_loaded, seq, name, address, messages)

    def _apply_missing(self, seq: int) -> bool:
        if seq != self._query_seq:
            return False
        self._title.set_text("Thread gone")
        return False

    def _apply_loaded(self, seq, name, address, messages) -> bool:
        if seq != self._query_seq:
            return False
        self._title.set_text(name)
        self.set_title(name)
        self._address = address
        self._messages = list(messages or [])
        self._rebuild_model()
        return False

    def _rebuild_model(self) -> None:
        query = self._search_entry.get_text()
        self._matches = presenter.filter_message_rows(self._messages, query)
        shown = self._matches if (query or "").strip() else self._messages
        now = int(time.time() * 1000)
        self._model.remove_all()
        for row in shown:
            outgoing = (row.direction or "").upper() == "OUTGOING"
            state, retry = presenter.message_state(
                row.direction, row.sent, now, row.timestamp_millis)
            if (row.transport or "").upper() != "SMS":
                retry = False
            is_mms = (row.transport or "").upper() == "MMS"
            media_path = getattr(row, "media_path", None) or ""
            has_file = False
            if media_path:
                try:
                    has_file = Path(media_path).is_file()
                except Exception:
                    has_file = False
            self._model.append(MessageItem(
                message_id=row.id,
                body=row.body or "",
                stamp=stamp_text(row.timestamp_millis),
                outgoing=outgoing,
                state=state,
                show_retry=retry,
                address=self._address,
                media_path=media_path if has_file else "",
                is_photo=bool(is_mms),
            ))
        if (query or "").strip():
            self._cursor = 0 if self._matches else -1
            total = len(self._matches)
            self._count.set_text(
                f"1 of {total}" if total else "No matches")
        else:
            self._cursor = -1
            self._count.set_text("")
        if len(shown) > 0:
            self._list.scroll_to(
                len(shown) - 1, Gtk.ListScrollFlags.NONE, None)

    # -------------------------------------------------------------- factory

    def _on_item_setup(self, _factory, list_item: Gtk.ListItem) -> None:
        body = Gtk.Label()
        body.set_wrap(True)
        body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        body.set_hexpand(True)
        body.set_selectable(True)
        body.add_css_class("txxt-body")
        photo = Gtk.Picture()
        photo.set_can_shrink(True)
        photo.set_size_request(288, 160)
        photo_button = Gtk.Button(label="Fetch photo")
        photo_button.connect(
            "clicked", lambda btn: self._on_fetch_clicked(list_item))
        open_button = Gtk.Button(label="Open photo")
        open_button.connect(
            "clicked", lambda btn: self._on_open_photo_clicked(list_item))
        meta = Gtk.Label()
        meta.add_css_class("txxt-timestamp")
        links = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        links.set_hexpand(True)
        retry = Gtk.Button(label="Retry")
        retry.connect(
            "clicked", lambda btn: self._on_retry_clicked(list_item))
        row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row_box.set_hexpand(True)
        row_box.append(body)
        row_box.append(photo)
        row_box.append(photo_button)
        row_box.append(open_button)
        row_box.append(meta)
        row_box.append(links)
        row_box.append(retry)
        list_item.body = body
        list_item.photo = photo
        list_item.photo_button = photo_button
        list_item.open_button = open_button
        list_item.meta = meta
        list_item.links = links
        list_item.retry = retry
        list_item.row_box = row_box
        list_item.set_child(row_box)

    def _on_item_bind(self, _factory, list_item: Gtk.ListItem) -> None:
        item = list_item.get_item()
        list_item.set_activatable(True)
        list_item.body.set_text(item.body)
        list_item.body.set_xalign(1.0 if item.outgoing else 0.0)
        meta = item.stamp + (f" · {item.state}" if item.state else "")
        list_item.meta.set_text(meta)
        list_item.meta.set_xalign(1.0 if item.outgoing else 0.0)
        show_photo = bool(item.is_photo and item.media_path)
        show_fetch = bool(item.is_photo and not item.media_path)
        if show_photo:
            pixbuf = thumbnail_pixbuf(item.media_path)
            if pixbuf is not None:
                try:
                    list_item.photo.set_pixbuf(pixbuf)
                    list_item.photo.set_visible(True)
                except Exception:
                    list_item.photo.set_visible(False)
            else:
                list_item.photo.set_visible(False)
        else:
            list_item.photo.set_visible(False)
        list_item.open_button.set_visible(show_photo)
        list_item.open_button.set_halign(
            Gtk.Align.END if item.outgoing else Gtk.Align.START)
        list_item.open_button._txxt_mid = int(item.message_id)
        list_item.open_button._txxt_path = item.media_path or ""
        list_item.photo_button.set_visible(show_fetch)
        list_item.photo_button.set_halign(
            Gtk.Align.END if item.outgoing else Gtk.Align.START)
        list_item.photo_button._txxt_mid = int(item.message_id)
        box = list_item.links
        kid = box.get_first_child()
        while kid is not None:
            following = kid.get_next_sibling()
            box.remove(kid)
            kid = following
        for link in presenter.find_links(item.body):
            button = Gtk.Button(label=link.url)
            button.set_halign(
                Gtk.Align.END if item.outgoing else Gtk.Align.START)
            button.connect("clicked", self._on_link_clicked, link.url)
            box.append(button)
        list_item.retry.set_visible(bool(item.show_retry))
        list_item.retry.set_halign(
            Gtk.Align.END if item.outgoing else Gtk.Align.START)
        list_item.retry._txxt_mid = int(item.message_id)

    def _on_link_clicked(self, _button: Gtk.Button, url: str) -> None:
        if url:
            launch_uri(self._window, url)

    def _on_row_activated(self, _view: Gtk.ListView, position: int) -> None:
        item = self._model.get_item(position)
        if item is None:
            return
        if bool(getattr(item, "is_photo", False)) and not getattr(item, "media_path", ""):
            self._fetch_photo(int(item.message_id))
            return
        links = presenter.find_links(item.body or "")
        if links:
            launch_uri(self._window, links[0].url)

    def _on_fetch_clicked(self, list_item: Gtk.ListItem) -> None:
        item = list_item.get_item()
        if item is None:
            return
        self._fetch_photo(int(item.message_id))

    def _fetch_photo(self, message_id: int) -> None:
        if message_id <= 0:
            return
        if self._client is None:
            try:
                self._store.get_message(message_id)
            except Exception:
                pass
            self.refresh()
            return
        try:
            self._client.fetch_mms(
                message_id, lambda path, err: self._on_fetch_done(path, err),
            )
        except Exception:
            pass

    def _on_fetch_done(self, path, error) -> None:
        if error is not None or not path:
            if error is not None:
                self._complaint(
                    "Photo unavailable",
                    "The photo could not be downloaded; try again shortly.")
            return
        self.refresh()

    def _on_open_photo_clicked(self, list_item: Gtk.ListItem) -> None:
        item = list_item.get_item()
        path = ""
        try:
            path = item.media_path or ""
        except Exception:
            path = ""
        if not path:
            button_path = getattr(list_item.open_button, "_txxt_path", "")
            path = button_path or ""
        if path:
            self._open_photo(path)

    def _open_photo(self, path: str) -> None:
        try:
            uri = Path(path).as_uri()
        except Exception:
            return
        try:
            launcher = Gtk.UriLauncher.new(uri)
            launcher.launch(self._window, None, self._on_photo_opened)
        except Exception:
            pass

    def _on_photo_opened(self, launcher, result) -> None:
        try:
            launcher.launch_finish(result)
        except GLib.Error:
            pass

    def _on_retry_clicked(self, list_item: Gtk.ListItem) -> None:
        item = list_item.get_item()
        if item is None:
            return
        self._send_text(item.body or "", via_retry=True)

    # --------------------------------------------------------------- search

    def _on_search_toggled(self, _button: Gtk.Button) -> None:
        self._search_bar.set_search_mode(
            not self._search_bar.get_search_mode())

    def _on_thread_search(self, _entry: Gtk.SearchEntry) -> None:
        self._rebuild_model()
        if self._cursor >= 0:
            self._list.scroll_to(self._cursor, Gtk.ListScrollFlags.NONE, None)

    def _on_prev_match(self, _button: Gtk.Button) -> None:
        self._step_match(-1)

    def _on_next_match(self, _button: Gtk.Button) -> None:
        self._step_match(1)

    def _step_match(self, direction: int) -> None:
        total = len(self._matches)
        if not total:
            return
        self._cursor = presenter.step_match(self._cursor, direction, total)
        self._count.set_text(f"{self._cursor + 1} of {total}")
        self._list.scroll_to(self._cursor, Gtk.ListScrollFlags.NONE, None)

    # --------------------------------------------------------------- actions

    def _on_call_clicked(self, _button: Gtk.Button) -> None:
        if self._address:
            launch_uri(self._window, f"tel:{self._address}")

    def _mark_read(self) -> None:
        if self._client is None:
            worker = threading.Thread(
                target=self._mark_read_in_worker, daemon=True)
            worker.start()
            return

        def _done(ok, _error) -> None:
            if not ok:
                worker = threading.Thread(
                    target=self._mark_read_in_worker, daemon=True)
                worker.start()

        try:
            self._client.mark_read(self._conversation_id, _done)
        except Exception:
            worker = threading.Thread(
                target=self._mark_read_in_worker, daemon=True)
            worker.start()

    def _mark_read_in_worker(self) -> None:
        try:
            self._store.mark_conversation_read(self._conversation_id)
        except Exception:
            pass

    def _on_send_clicked(self, _button: Gtk.Button) -> None:
        self._send_text(self._entry.get_text(), via_retry=False)

    def _send_text(self, body: str, via_retry: bool) -> None:
        if not self._gate.begin():
            return
        try:
            staged = [self._staged_path] if self._staged_path else []
            if staged:
                problem = compose_mod.validate_mms_outgoing(
                    self._address, body, staged)
            else:
                problem = compose_mod.validate_outgoing(self._address, body)
            if problem is not None:
                self._complaint("Cannot send", problem)
                return
            if self._client is None:
                self._complaint(
                    "Daemon unavailable",
                    "The message service is not running; try again shortly.")
                return
            self._send_button.set_sensitive(False)
            if staged:
                self._client.send_mms(
                    [self._address], body.strip(), staged,
                    lambda mid, err: self._on_send_done(mid, err, body),
                )
            else:
                self._client.send(
                    self._address, body.strip(),
                    lambda mid, err: self._on_send_done(mid, err, body),
                )
        except Exception:
            self._gate.end()
            self._send_button.set_sensitive(True)
            if via_retry:
                pass

    def _on_send_done(self, message_id, error, _body: str) -> None:
        self._gate.end()
        self._send_button.set_sensitive(True)
        if error is not None or message_id is None:
            self._complaint(
                "Send failed",
                "The message stayed in the thread and will retry.")
            return
        self._entry.set_text("")
        self._clear_staged_file()
        self.refresh()

    # -------------------------------------------------- photo attach (L5)

    def _on_attach_clicked(self, _button: Gtk.Button) -> None:
        dialog = Adw.AlertDialog.new("Attach photo", "Choose a photo to send")
        dialog.add_response("files", "Files")
        dialog.add_response("roll", "Camera roll")
        dialog.add_response("cancel", "Cancel")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(
            self._window, None, self._on_attach_choice,
        )

    def _on_attach_choice(self, dialog, result) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response == "files":
            self._open_file_dialog(None)
        elif response == "roll":
            pictures = Path.home() / "Pictures"
            self._open_file_dialog(str(pictures) if pictures.is_dir() else None)

    def _open_file_dialog(self, folder: str | None) -> None:
        try:
            file_dialog = Gtk.FileDialog()
            image_filter = Gtk.FileFilter()
            image_filter.set_name("Images")
            image_filter.add_mime_type("image/jpeg")
            image_filter.add_mime_type("image/png")
            image_filter.add_pattern("*.jpg")
            image_filter.add_pattern("*.jpeg")
            image_filter.add_pattern("*.png")
            file_dialog.set_default_filter(image_filter)
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(image_filter)
            file_dialog.set_filters(filters)
            if folder:
                try:
                    file_dialog.set_initial_folder(Gio.File.new_for_path(folder))
                except Exception:
                    pass
            file_dialog.open(
                self._window, None, self._on_file_chosen,
            )
        except Exception:
            pass

    def _on_file_chosen(self, file_dialog, result) -> None:
        try:
            chosen = file_dialog.open_finish(result)
        except GLib.Error:
            return
        if chosen is None:
            return
        try:
            source = chosen.get_path()
        except Exception:
            return
        if not source:
            return
        self._stage_photo(source)

    def _stage_photo(self, source: str) -> None:
        try:
            with open(source, "rb") as handle:
                raw = handle.read(64)
        except OSError:
            self._complaint("Cannot read photo", "That file could not be read.")
            return
        try:
            from txxt import media as media_mod

            kind = media_mod.sniff(raw)
        except Exception:
            kind = None
        if kind not in ("jpeg", "png"):
            self._complaint(
                "Photo refused",
                "That file is not a JPEG or PNG photo, so it was not sent.")
            return
        try:
            target_dir = staging_dir()
            target_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(target_dir, 0o700)
            except OSError:
                pass
            dest = target_dir / f"staged-{int(time.time() * 1000)}.bin"
            import shutil

            shutil.copyfile(source, dest)
        except OSError:
            self._complaint("Cannot read photo", "That file could not be read.")
            return
        self._staged_path = str(dest)
        self._show_staged()

    def _show_staged(self) -> None:
        if not self._staged_path:
            self._staged_row.set_visible(False)
            return
        try:
            size = Path(self._staged_path).stat().st_size
        except OSError:
            size = 0
        pixbuf = thumbnail_pixbuf(self._staged_path, 96)
        if pixbuf is not None:
            try:
                self._staged_thumb.set_pixbuf(pixbuf)
            except Exception:
                pass
        self._staged_label.set_text(
            f"Photo staged · {format_size(size)}")
        self._staged_row.set_visible(True)

    def _on_staged_clear(self, _button: Gtk.Button) -> None:
        self._clear_staged_file()
        self._staged_row.set_visible(False)

    def _clear_staged_file(self) -> None:
        if self._staged_path:
            try:
                Path(self._staged_path).unlink(missing_ok=True)
            except OSError:
                pass
        self._staged_path = None
        try:
            self._staged_row.set_visible(False)
        except Exception:
            pass

    def _complaint(self, heading: str, body: str) -> None:
        self._gate.end()
        self._send_button.set_sensitive(True)
        dialog = Adw.AlertDialog.new(heading, body)
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.choose(self._window, None, self._on_dialog_dismissed)

    def _on_dialog_dismissed(self, dialog: Adw.AlertDialog, result) -> None:
        try:
            dialog.choose_finish(result)
        except GLib.Error:
            pass
