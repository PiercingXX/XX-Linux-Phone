"""TxxT phone UI: conversation list + thread on an ``Adw.NavigationView``.

The UI reads the SQLite store for display and talks to the daemon
over ``io.piercingxx.TxxT1`` (``TxxtClient``: Send / MarkRead /
OpenThread / MessagesChanged) — never the modem stack, never message
rows behind the daemon. Refreshes ride the ``MessagesChanged``
signal; nothing polls.

Launches: ``txxt --open-thread ID`` and the
``io.piercingxx.TxxT://thread/ID`` URI the daemon's notification
action fires (the .desktop ``Exec`` carries ``%U`` so the URI lands
in argv; ``do_open`` covers bus-activated opens).

Phone rules: CSS providers attach to ``Gdk.Display.get_default()``
(None-guarded; ``Adw.Application`` has no ``get_display``), header
bars hide desktop window buttons, and no entry autofocuses — list
search and compose stay unfocusable until tapped.
"""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')

from gi.repository import Adw, Gio, Gtk, Gdk  # noqa: E402

from txxt import APP_ID  # noqa: E402
from txxt import list_presenter as presenter  # noqa: E402
from txxt import theme as txxt_theme  # noqa: E402
from txxt.conversations import ConversationListPage  # noqa: E402
from txxt.settings import load as load_settings  # noqa: E402
from txxt.store import Store  # noqa: E402
from txxt.thread_page import ThreadPage, sweep_staged_photos  # noqa: E402
from txxt.txxt_client import TxxtClient  # noqa: E402


def data_dir() -> Path:
    root = os.environ.get('XDG_DATA_HOME') or str(Path.home() / '.local' / 'share')
    return Path(root) / 'txxt'


def ensure_data_dir() -> Path:
    """Create the XDG data dir (mode 0700) before SQLite opens in it."""
    target = data_dir()
    target.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target, 0o700)
    except OSError:
        pass
    try:
        sweep_staged_photos()
    except Exception:
        pass
    return target


class TxxtWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, open_thread_id: int | None = None) -> None:
        super().__init__(application=app)
        self.set_title('TxxT')
        self.set_default_size(360, 720)
        self.set_size_request(320, 640)
        self._css = Gtk.CssProvider()
        self.apply_theme()
        self._store = Store(str(ensure_data_dir() / 'txxt.db'))
        self._client: TxxtClient | None = None
        self._thread_pages: list[ThreadPage] = []

        from txxt.contacts import session_resolver  # noqa: E402
        self._resolver = session_resolver(None)

        self._navigation = Adw.NavigationView()
        self._navigation.connect('popped', self._on_page_popped)
        self._list_page = ConversationListPage(
            self._navigation, self, self._store, self._resolver,
            None, self.open_thread,
        )
        self._navigation.push(self._list_page)
        self.set_content(self._navigation)

        Gio.bus_get(Gio.BusType.SESSION, None, self._on_bus_ready)
        self._theme_monitor = txxt_theme.watch_xxwm_config(
            self._on_shell_config_changed,
        )
        self._blocking_page = None
        self._quarantine_page = None
        if open_thread_id is not None:
            self.open_thread(open_thread_id)

    def _on_shell_config_changed(self, _shell_config) -> None:
        """Shell theme edits re-apply live; a manual pick still wins."""
        try:
            self.apply_theme()
        except Exception:
            pass

    def apply_theme(self) -> None:
        settings = load_settings()
        shell_config = txxt_theme.load_xxwm_config()
        if settings.auto_sync_theme:
            active = txxt_theme.resolve_theme(shell_config)
            font = txxt_theme.resolve_font(shell_config)
        else:
            active = txxt_theme.resolve_theme({}, manual_preset=settings.theme_preset)
            font = txxt_theme.manual_font_family(settings.font_mode)
        try:
            scale = float(shell_config.get('text_size_scale', 1.0))
        except (TypeError, ValueError):
            scale = 1.0
        css = txxt_theme.css_for_theme(active, font, scale)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            self._css.load_from_string(css)
        try:
            scheme = (
                Adw.ColorScheme.PREFER_DARK if active.get('dark')
                else Adw.ColorScheme.PREFER_LIGHT
            )
            Adw.StyleManager.get_default().set_color_scheme(scheme)
        except Exception:
            pass

    def _on_bus_ready(self, _source, result) -> None:
        try:
            connection = Gio.bus_get_finish(result)
        except Exception:
            return
        from txxt.contacts import GioContactsBackend, session_resolver  # noqa: E402
        self._resolver = session_resolver(connection)
        self._client = TxxtClient(connection)
        try:
            self._list_page.set_resolver(self._resolver)
        except Exception:
            pass
        self._list_page.set_client(self._client)
        for page in list(self._thread_pages):
            try:
                page.set_resolver(self._resolver)
            except Exception:
                pass
            page.set_client(self._client)
        for page in (self._blocking_page, self._quarantine_page):
            if page is not None:
                try:
                    page.set_resolver(self._resolver)
                except Exception:
                    pass
        try:
            backend = GioContactsBackend(connection)
            backend.subscribe_changed(self._list_page.handle_contacts_changed)
            for page in list(self._thread_pages):
                try:
                    backend.subscribe_changed(page.handle_contacts_changed)
                except Exception:
                    pass
            for page in (self._blocking_page, self._quarantine_page):
                if page is not None:
                    try:
                        backend.subscribe_changed(page.handle_contacts_changed)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            self._list_page.refresh()
        except Exception:
            pass
        self._client.subscribe_messages_changed(self._on_messages_changed)

    def _on_messages_changed(self, conversation_ids) -> None:
        self._list_page.refresh_for_signal(conversation_ids)
        for page in list(self._thread_pages):
            try:
                page.refresh_for_signal(conversation_ids)
            except Exception:
                pass

    def _on_page_popped(self, _navigation, _page) -> bool:
        self._thread_pages = [
            page for page in self._thread_pages if page.get_visible()
        ]
        return False

    def open_settings(self) -> None:
        from txxt.settings_page import SettingsDialog  # noqa: E402

        dialog = SettingsDialog(
            self._store,
            on_changed=self._on_settings_changed,
            on_open_blocking=lambda: self.open_blocking(),
            on_open_quarantine=lambda: self.open_quarantine(),
        )
        dialog.present(self)
        return dialog

    def _on_settings_changed(self) -> None:
        self.apply_theme()
        try:
            self._list_page.refresh()
        except Exception:
            pass

    def open_blocking(self) -> None:
        from txxt.blocking_page import BlockingPage  # noqa: E402

        if self._blocking_page is None or not self._blocking_page.get_visible():
            self._blocking_page = BlockingPage(
                self._navigation, self, self._store, self._resolver,
                on_changed=self._on_filter_pages_changed,
            )
            self._navigation.push(self._blocking_page)
        else:
            self._navigation.pop_to_page(self._blocking_page)

    def open_quarantine(self) -> None:
        from txxt.quarantine_page import QuarantinePage  # noqa: E402

        if self._quarantine_page is None or not self._quarantine_page.get_visible():
            self._quarantine_page = QuarantinePage(
                self._navigation, self, self._store, self._resolver,
                on_changed=self._on_filter_pages_changed,
            )
            self._navigation.push(self._quarantine_page)
        else:
            self._navigation.pop_to_page(self._quarantine_page)
        try:
            self._quarantine_page.refresh()
        except Exception:
            pass

    def _on_filter_pages_changed(self) -> None:
        try:
            self._list_page.refresh()
        except Exception:
            pass

    def open_thread(self, conversation_id: int) -> None:
        try:
            cid = int(conversation_id)
        except (TypeError, ValueError):
            return
        if cid <= 0:
            return
        for page in self._thread_pages:
            if page.conversation_id == cid and page.get_visible():
                self._navigation.pop_to_page(page)
                return
        page = ThreadPage(
            self._navigation, self, self._store, self._resolver,
            self._client, cid,
        )
        self._thread_pages.append(page)
        self._navigation.push(page)


class TxxtApplication(Adw.Application):
    def __init__(self, open_thread_id: int | None = None) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self._pending_thread = open_thread_id

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = TxxtWindow(self, self._pending_thread)
            self._pending_thread = None
        window.present()

    def do_open(self, files, _hint: str) -> None:
        for item in files or []:
            try:
                uri = item.get_uri()
            except Exception:
                continue
            found = presenter.parse_thread_uri(uri)
            if found is not None:
                self._pending_thread = found
        window = self.props.active_window
        if window is None:
            window = TxxtWindow(self, self._pending_thread)
            self._pending_thread = None
            window.present()
        elif self._pending_thread is not None:
            window.open_thread(self._pending_thread)
            self._pending_thread = None
            window.present()


def main(argv: list[str] | None = None) -> None:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if '--daemon' in args:
        from txxt.daemon import main as daemon_main

        raise SystemExit(daemon_main(args))
    pending = presenter.parse_open_thread_argv(args)
    app = TxxtApplication(pending)
    app.run([])
