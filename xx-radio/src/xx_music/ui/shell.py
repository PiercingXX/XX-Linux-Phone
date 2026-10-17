"""The window shell: core tabs, extra tabs from the AppSpec, bottom bar.

Scaffold stage: Home, Search and Library show their first-run sentence;
Settings lists the engine's components, read on a worker thread. Later
stages replace each page body (PORT_SPEC.md milestones) and add the mini
player above the tabs.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

from xx_ui import widgets  # noqa: E402
from xx_music.appspec import CORE_TAB_TITLES, AppSpec  # noqa: E402
from xx_music.engine import Engine, EngineStatus  # noqa: E402
from xx_music.tasks import Worker  # noqa: E402

FIRST_RUN_TEXT = {
    "home": "Search for a song to start listening.",
    "search": "Find songs, albums, artists and playlists.",
    "library": "Sign in to see your library. Downloads show here too.",
}
CHECKING_TEXT = "Checking…"


class TabContext:
    """What an extra tab's ``build`` receives."""

    def __init__(self, spec: AppSpec, engine: Engine, worker: Worker,
                 shell: Shell) -> None:
        self.spec = spec
        self.engine = engine
        self.worker = worker
        self.shell = shell


class Shell:
    def __init__(self, spec: AppSpec, engine: Engine,
                 worker: Worker | None = None) -> None:
        self.spec = spec
        self.engine = engine
        self.worker = worker or Worker()
        self.current = ""
        self.titles: dict[str, str] = {}
        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.root.set_hexpand(True)
        self.root.set_vexpand(True)
        self.root.append(self.stack)
        self._bar = None
        self._engine_rows = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        context = TabContext(spec, engine, self.worker, self)
        extras = {tab.key: tab for tab in spec.extra_tabs}
        for key in spec.tab_keys():
            if key in extras:
                self.titles[key] = extras[key].title
                page = extras[key].build(context)
            else:
                self.titles[key] = CORE_TAB_TITLES[key]
                page = self._core_page(key)
            self.stack.add_named(page, key)

    # -- pages ----------------------------------------------------------------
    def _core_page(self, key: str):
        title = CORE_TAB_TITLES[key]
        if key == "settings":
            return self._settings_page()
        caption = self.engine.name if key == "home" else None
        header = widgets.page_header(title, caption)
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 32)
        body.set_margin_top(24)
        if key == "search":
            body.append(widgets.search_bar("Search", boxed=True))
        body.append(widgets.empty_state(FIRST_RUN_TEXT[key]))
        return widgets.page(header, body)

    def _settings_page(self):
        header = widgets.page_header("Settings")
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        body.append(widgets.section_label("Playback engine"))
        self._engine_rows.append(
            widgets.setting_row(self.engine.name, CHECKING_TEXT, last=True))
        body.append(self._engine_rows)
        self.worker.run(self.engine.status, self._show_status,
                        self._show_status_error)
        return widgets.page(header, body)

    def _replace_engine_rows(self, rows: list) -> None:
        child = self._engine_rows.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._engine_rows.remove(child)
            child = following
        for index, (title, subtitle) in enumerate(rows):
            self._engine_rows.append(widgets.setting_row(
                title, subtitle, last=index == len(rows) - 1))

    def _show_status(self, status: EngineStatus) -> None:
        rows = []
        for component in status.components:
            parts = [component.state_text]
            if component.version:
                parts.append(component.version)
            if component.needed_for:
                parts.append(component.needed_for)
            rows.append((component.name, " · ".join(parts)))
        if not rows:
            rows.append((self.engine.name, "Ready" if status.can_play
                         else "Not set up"))
        self._replace_engine_rows(rows)

    def _show_status_error(self, error: BaseException) -> None:
        text = getattr(error, "user_text", "Something went wrong.")
        self._replace_engine_rows([(self.engine.name, text)])

    # -- navigation -------------------------------------------------------------
    def show(self, key: str) -> None:
        self.current = key
        self.stack.set_visible_child_name(key)
        bar = widgets.bottom_tabs([
            ("", self.titles[tab], tab == key, lambda tab=tab: self.show(tab))
            for tab in self.spec.tab_keys()
        ])
        if self._bar is not None:
            self.root.remove(self._bar)
        self._bar = bar
        self.root.append(bar)


def build_window(app, spec: AppSpec, engine: Engine | None = None):
    """The app window at phone size, themed by xx_ui with live switching."""
    import xx_ui

    xx_ui.apply(app)
    window = Gtk.ApplicationWindow(application=app)
    window.set_title(spec.name)
    window.set_default_size(360, 720)
    window.set_decorated(False)
    shell = Shell(spec, engine if engine is not None else spec.make_engine())
    shell.show("home")
    window.set_child(shell.root)
    window.xx_shell = shell
    window.connect("destroy", lambda _w: shell.worker.shutdown())
    return window
