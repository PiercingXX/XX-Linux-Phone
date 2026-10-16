"""xx-share GTK4/libadwaita UI: devices, files, progress, settings.

Text-first, phone-narrow (360px): no icons, every color from the active
ThemePreset, long labels ellipsize. Network work runs on worker threads
with results posted back via GLib.idle_add; the main loop never blocks.
Long lists (nearby devices, received history) use Gtk.ListView; the
short chosen-files list stays a ListBox.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

from config import VISIBILITY_ALWAYS, ShareConfig
from favorites import auto_accept_enabled, is_favorite, set_auto_accept, set_favorite
from notify import format_size


def theme_css(background: str, foreground: str, muted: str, accent: str,
              surface: str, border: str) -> bytes:
    return (
        f"window {{ background-color: {background}; color: {foreground}; }} "
        f"scrolledwindow, viewport, stack {{ background-color: {background}; }} "
        f"listview, listview > row, list, list > row {{ "
        f"background-color: {surface}; color: {foreground}; }} "
        f"listview > row:selected, list > row:selected {{ "
        f"background-color: {border}; color: {foreground}; }} "
        f".xx-muted {{ color: {muted}; }} "
        f".xx-accent {{ color: {accent}; }} "
        f"entry {{ background-color: {surface}; color: {foreground}; "
        f"border-color: {border}; }}"
    ).encode("utf-8")


def apply_theme(window: object, config: ShareConfig) -> None:
    from gi.repository import Gdk, Gtk

    preset = config.effective_theme()
    provider = Gtk.CssProvider()
    provider.load_from_data(theme_css(
        preset.background, preset.foreground, preset.muted,
        preset.accent, preset.surface, preset.border))
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def ellipsize(label: object) -> None:
    from gi.repository import Pango

    label.set_ellipsize(Pango.EllipsizeMode.END)  # type: ignore[union-attr]
    label.set_max_width_chars(32)  # type: ignore[union-attr]


def peer_label(peer: dict) -> str:
    alias = str(peer.get("alias", ""))
    addr = str(peer.get("addr", ""))
    fp = str(peer.get("fingerprint", ""))
    short = f" ·{fp[:8]}" if fp else ""
    return f"{alias} ({addr}){short}"


def history_label(entry: dict) -> str:
    name = str(entry.get("file_name", "file"))
    sender = str(entry.get("sender_alias", ""))
    size = entry.get("size", 0)
    try:
        text = f"{name} ({format_size(int(size))})"
    except (TypeError, ValueError):
        text = name
    return f"{text} — {sender}" if sender else text


class ShareWindow:
    def __init__(self, app: object, service: object, initial_files: list[str]) -> None:
        from gi.repository import Adw, Gtk

        self.service = service
        self.config: ShareConfig = service.config  # type: ignore[union-attr]
        self.files: list[str] = [item for item in initial_files
                                 if Path(item).is_file()]
        self.window = Adw.ApplicationWindow(application=app, title="xx-share")
        self.window.set_default_size(360, 640)
        apply_theme(self.window, self.config)

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        header.set_decoration_layout(":close")

        self.peer_strings = Gtk.StringList()
        peer_factory = Gtk.SignalListItemFactory()
        peer_factory.connect("setup", self._on_peer_setup)
        peer_factory.connect("bind", self._on_peer_bind)
        self.peer_selection = Gtk.SingleSelection(model=self.peer_strings)
        self.device_list = Gtk.ListView(model=self.peer_selection,
                                        factory=peer_factory)
        self.device_list.set_show_separators(True)
        self._peers: list[dict] = []

        self.history_strings = Gtk.StringList()
        history_factory = Gtk.SignalListItemFactory()
        history_factory.connect("setup", self._on_peer_setup)
        history_factory.connect("bind", self._on_history_bind)
        self.history_selection = Gtk.SingleSelection(model=self.history_strings)
        self.history_selection.set_can_unselect(True)
        self.history_list = Gtk.ListView(model=self.history_selection,
                                         factory=history_factory)
        self.history_list.set_show_separators(True)
        self._history: list[dict] = []

        self.file_box = Gtk.ListBox()
        self.file_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.progress_label = Gtk.Label(label="No files chosen")
        ellipsize(self.progress_label)

        add_button = Gtk.Button(label="Add files")
        add_button.connect("clicked", self._on_add_files)
        self.send_button = Gtk.Button(label="Send")
        self.send_button.connect("clicked", self._on_send)
        self.cancel_button = Gtk.Button(label="Cancel")
        self.cancel_button.connect("clicked", self._on_cancel)
        self.cancel_button.set_sensitive(False)
        self.current_transfer: str | None = None

        self.visible_switch = Gtk.Switch()
        self.visible_switch.set_active(bool(self.config.data.get("visible")))
        self.visible_switch.connect("notify::active", self._on_visible_toggled)
        self.visible_note = Gtk.Label(label=self._visibility_note())
        self.visible_note.add_css_class("xx-muted")
        ellipsize(self.visible_note)

        self.always_switch = Gtk.Switch()
        self.always_switch.set_active(
            self.config.data.get("visibility_mode") == VISIBILITY_ALWAYS)
        self.always_switch.connect("notify::active", self._on_always_toggled)

        self.auto_accept_switch = Gtk.Switch()
        self.auto_accept_switch.set_active(auto_accept_enabled(self.config))
        self.auto_accept_switch.connect("notify::active",
                                        self._on_auto_accept_toggled)
        self.favorite_button = Gtk.Button(label="Favorite selected device")
        self.favorite_button.connect("clicked", self._on_favorite)

        visible_row = Adw.ActionRow(title="Visible to nearby devices")
        visible_row.add_suffix(self.visible_switch)
        always_row = Adw.ActionRow(title="Keep visible (no auto-off)")
        always_row.add_suffix(self.always_switch)
        auto_row = Adw.ActionRow(title="Auto-accept favorites")
        auto_row.add_suffix(self.auto_accept_switch)

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        layout.set_margin_start(12)
        layout.set_margin_end(12)
        layout.set_margin_top(12)
        layout.set_margin_bottom(12)
        layout.append(visible_row)
        layout.append(self.visible_note)
        layout.append(always_row)
        layout.append(Gtk.Label(label="Nearby devices"))
        layout.append(self.device_list)
        layout.append(self.favorite_button)
        layout.append(Gtk.Label(label="Files"))
        layout.append(self.file_box)
        layout.append(self.progress_label)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.append(add_button)
        buttons.append(self.send_button)
        buttons.append(self.cancel_button)
        layout.append(buttons)
        layout.append(self._settings_group())
        layout.append(auto_row)
        layout.append(Gtk.Label(label="Received"))
        layout.append(self.history_list)
        clear_button = Gtk.Button(label="Clear history")
        clear_button.connect("clicked", self._on_clear_history)
        layout.append(clear_button)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(layout)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(scroll)
        self.window.set_content(view)
        previous = getattr(service, "on_signal", None)
        service.on_signal = self._chain_signal(previous)  # type: ignore[union-attr]
        self.refresh_files()
        self.refresh_devices()
        self.refresh_history()
        self.send_button.grab_focus()

    def _chain_signal(self, previous: object) -> object:
        def chained(name: str, args: tuple) -> None:
            try:
                if callable(previous):
                    previous(name, args)  # type: ignore[operator]
            except Exception as error:
                logger.warning("chained signal failed: %s", error)
            self._on_bus_signal(name, args)

        return chained

    def _visibility_note(self) -> str:
        if self.config.data.get("visibility_mode") == VISIBILITY_ALWAYS:
            return "Visible to nearby devices"
        return "Visible (auto-off after 10 min)"

    def _settings_group(self) -> object:
        from gi.repository import Adw, Gtk

        alias = Gtk.Entry(text=str(self.config.data.get("alias", "")))
        alias.set_placeholder_text("Device name")
        alias.connect("changed", self._on_alias_changed)
        pin = Gtk.Entry(text=str(self.config.data.get("require_pin", "")))
        pin.set_placeholder_text("Receive PIN (empty = none)")
        pin.connect("changed", self._on_pin_changed)
        self.send_pin = Gtk.Entry()
        self.send_pin.set_placeholder_text("Receiver PIN (if they show one)")
        group = Adw.PreferencesGroup(title="Settings")
        group.add(Adw.ActionRow(title="Device name"))
        group.add(alias)
        group.add(Adw.ActionRow(title="Receive PIN"))
        group.add(pin)
        group.add(Adw.ActionRow(title="Send PIN"))
        group.add(self.send_pin)
        return group

    def present(self) -> None:
        self.window.present()  # type: ignore[union-attr]

    def refresh_files(self) -> None:
        from gi.repository import Gtk

        while True:
            row = self.file_box.get_first_child()  # type: ignore[union-attr]
            if row is None:
                break
            self.file_box.remove(row)  # type: ignore[union-attr]
        for path in self.files:
            try:
                size = Path(path).stat().st_size
            except OSError:
                continue
            label = Gtk.Label(label=f"{Path(path).name} ({format_size(size)})")
            ellipsize(label)
            label.set_xalign(0.0)
            self.file_box.append(label)  # type: ignore[union-attr]
        self.progress_label.set_text(  # type: ignore[union-attr]
            f"{len(self.files)} file(s) chosen" if self.files else "No files chosen")

    def _peer_dicts(self) -> list[dict]:
        peers = self.service.list_peers()  # type: ignore[union-attr]
        return [dict(item) for item in peers]

    def set_backend(self, service: object) -> None:
        previous = getattr(service, "on_signal", None)
        service.on_signal = self._chain_signal(previous)  # type: ignore[union-attr]
        self.service = service
        self.config = service.config  # type: ignore[union-attr]
        self.refresh_devices()
        self.refresh_history()

    def refresh_devices(self) -> None:
        list_async = getattr(self.service, "list_peers_async", None)
        if callable(list_async):
            list_async(self._got_peers)
            return
        try:
            self._peers = self._peer_dicts()
        except Exception as error:
            logger.warning("refresh devices failed: %s", error)
            self._peers = []
        self._render_peers()

    def _got_peers(self, ok: bool, peers: object) -> None:
        from gi.repository import GLib

        def render() -> bool:
            try:
                self._peers = [dict(item) for item in peers] if ok else []  # type: ignore[union-attr]
            except Exception as error:
                logger.warning("render peers failed: %s", error)
                self._peers = []
            self._render_peers()
            return False

        GLib.idle_add(render)

    def _render_peers(self) -> None:
        while self.peer_strings.get_n_items():
            self.peer_strings.remove(0)
        if not self._peers:
            self.peer_strings.append("No devices nearby yet")
            return
        for peer in self._peers:
            mark = " *" if is_favorite(self.config, peer.get("fingerprint", "")) else ""
            self.peer_strings.append(peer_label(peer) + mark)

    def refresh_history(self) -> None:
        try:
            self._history = list(self.service.history.list())  # type: ignore[union-attr]
        except Exception as error:
            logger.warning("refresh history failed: %s", error)
            self._history = []
        while self.history_strings.get_n_items():
            self.history_strings.remove(0)
        for entry in self._history:
            self.history_strings.append(history_label(entry))

    def _on_peer_setup(self, _factory: object, list_item: object) -> None:
        from gi.repository import Gtk

        label = Gtk.Label(xalign=0.0)
        ellipsize(label)
        list_item.set_child(label)  # type: ignore[union-attr]

    def _on_peer_bind(self, _factory: object, list_item: object) -> None:
        item = list_item.get_item()  # type: ignore[union-attr]
        label = list_item.get_child()  # type: ignore[union-attr]
        text = item.get_string() if item is not None else ""
        label.set_text(text)  # type: ignore[union-attr]

    def _on_history_bind(self, _factory: object, list_item: object) -> None:
        item = list_item.get_item()  # type: ignore[union-attr]
        label = list_item.get_child()  # type: ignore[union-attr]
        text = item.get_string() if item is not None else ""
        label.set_text(text)  # type: ignore[union-attr]

    def _selected_peer(self) -> dict | None:
        if not self._peers:
            return None
        try:
            index = int(self.peer_selection.get_selected())  # type: ignore[union-attr]
        except (TypeError, ValueError):
            return None
        if 0 <= index < len(self._peers):
            return self._peers[index]
        return None

    def _on_alias_changed(self, entry: object) -> None:
        text = entry.get_text()  # type: ignore[union-attr]
        self.config.data["alias"] = text.strip()
        self.config.save()

    def _on_pin_changed(self, entry: object) -> None:
        text = entry.get_text()  # type: ignore[union-attr]
        self.config.data["require_pin"] = text.strip()
        self.config.save()

    def _on_auto_accept_toggled(self, switch: object, _param: object) -> None:
        set_auto_accept(self.config, bool(switch.get_active()))  # type: ignore[union-attr]

    def _on_always_toggled(self, switch: object, _param: object) -> None:
        from gi.repository import GLib

        active = bool(switch.get_active())  # type: ignore[union-attr]
        self.config.data["visibility_mode"] = (
            VISIBILITY_ALWAYS if active else "auto-10min")
        self.config.save()
        GLib.idle_add(self._idle_visibility_note)

    def _idle_visibility_note(self) -> bool:
        self.visible_note.set_text(self._visibility_note())  # type: ignore[union-attr]
        return False

    def _on_favorite(self, _button: object) -> None:
        from gi.repository import GLib

        peer = self._selected_peer()
        if peer is None:
            return
        fingerprint = str(peer.get("fingerprint", ""))
        set_favorite(self.config, fingerprint,
                     not is_favorite(self.config, fingerprint))
        GLib.idle_add(self._idle_refresh_devices)

    def _idle_refresh_devices(self) -> bool:
        self.refresh_devices()
        return False

    def _on_clear_history(self, _button: object) -> None:
        from gi.repository import GLib

        try:
            self.service.history.clear()  # type: ignore[union-attr]
        except Exception as error:
            logger.warning("clear history failed: %s", error)
        GLib.idle_add(self._idle_refresh_history)

    def _idle_refresh_history(self) -> bool:
        self.refresh_history()
        return False

    def _on_bus_signal(self, name: str, args: tuple) -> None:
        from gi.repository import GLib

        if name == "TransferProgress":
            transfer_id = str(args[0]) if args else ""
            sent = int(args[2]) if len(args) > 2 else 0
            total = int(args[3]) if len(args) > 3 else 0
            GLib.idle_add(self._idle_bus_progress, transfer_id, sent, total)
        elif name == "TransferDone":
            transfer_id = str(args[0]) if args else ""
            ok = bool(args[1]) if len(args) > 1 else False
            message = str(args[2]) if len(args) > 2 else ""
            if not self.current_transfer or transfer_id == self.current_transfer:
                GLib.idle_add(self._idle_done, message or "Send finished", ok)
        elif name == "IncomingRequest":
            GLib.idle_add(self._idle_refresh_history)

    def _idle_bus_progress(self, transfer_id: str, sent: int, total: int) -> bool:
        if self.current_transfer and transfer_id != self.current_transfer:
            return False
        self.progress_label.set_text(  # type: ignore[union-attr]
            f"{format_size(sent)} / {format_size(total)}")
        return False

    def _on_visible_toggled(self, switch: object, _param: object) -> None:
        from gi.repository import GLib

        active = switch.get_active()  # type: ignore[union-attr]

        def work() -> None:
            from netif import allowed_bind_addrs

            addrs = allowed_bind_addrs()
            GLib.idle_add(self._apply_visibility, active, addrs)

        threading.Thread(target=work, daemon=True).start()

    def _apply_visibility(self, active: bool, addrs: list[str]) -> bool:
        self.service.set_visible(active, addrs)  # type: ignore[union-attr]
        self.visible_note.set_text(self._visibility_note())  # type: ignore[union-attr]
        self.refresh_devices()
        return False

    def _on_add_files(self, _button: object) -> None:
        from gi.repository import Gtk

        dialog = Gtk.FileDialog(title="Choose files to send")
        dialog.open_multiple(self.window, None, self._on_files_chosen)

    def _on_files_chosen(self, dialog: object, result: object) -> None:
        from gi.repository import GLib

        try:
            chosen = dialog.open_multiple_finish(result)  # type: ignore[union-attr]
        except GLib.Error:
            return
        paths = [item.get_path() for item in chosen]
        self.files.extend(item for item in paths if item)
        GLib.idle_add(self._idle_refresh_files)

    def _idle_refresh_files(self) -> bool:
        self.refresh_files()
        return False

    def _on_send(self, _button: object) -> None:
        from gi.repository import GLib

        peer = self._selected_peer()
        if peer is None:
            self.progress_label.set_text("Choose a device first")  # type: ignore[union-attr]
            return
        if not self.files:
            self.progress_label.set_text("Add files first")  # type: ignore[union-attr]
            return
        files = list(self.files)
        pin_text = self.send_pin.get_text().strip()
        self.progress_label.set_text(f"Sending to {peer.get('alias', '')}…")  # type: ignore[union-attr]
        self.cancel_button.set_sensitive(True)  # type: ignore[union-attr]

        def on_done(ok: bool, message: str) -> None:
            GLib.idle_add(self._idle_done, message, ok)

        start_async = getattr(self.service, "start_send_async", None)
        if callable(start_async):
            def on_started(ok: bool, result: str) -> None:
                if ok:
                    self.current_transfer = result
                else:
                    GLib.idle_add(self._idle_done, result, False)

            try:
                start_async(str(peer.get("fingerprint", "")), files,  # type: ignore[operator]
                            pin_text, on_started, on_done)
            except Exception as error:
                GLib.idle_add(self._idle_done, f"Send failed: {error}", False)
            return
        try:
            self.current_transfer = self.service.start_send(  # type: ignore[union-attr]
                str(peer.get("fingerprint", "")), files, pin_text, on_done)
        except ValueError as error:
            GLib.idle_add(self._idle_done, str(error), False)
        except Exception as error:
            GLib.idle_add(self._idle_done, f"Send failed: {error}", False)

    def _on_cancel(self, _button: object) -> None:
        transfer_id = self.current_transfer
        if not transfer_id:
            return
        try:
            self.service.cancel_send(transfer_id)  # type: ignore[union-attr]
        except Exception as error:
            logger.warning("cancel send failed: %s", error)

    def _idle_done(self, message: str, _ok: bool) -> bool:
        self.progress_label.set_text(message)  # type: ignore[union-attr]
        self.cancel_button.set_sensitive(False)  # type: ignore[union-attr]
        self.current_transfer = None
        return False
