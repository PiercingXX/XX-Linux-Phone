"""XX-Audiobook entry point: Adw.Application with the five-tab shell.

Scaffold placeholder (PORT_SPEC.md M1-M5 build the real pages): every tab
renders its header plus a one-sentence empty state on the shared xx_ui
design system, installed once here with live theme switching.
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

import xx_ui  # noqa: E402
from xx_ui import widgets  # noqa: E402
from xx_audiobook import __app_id__  # noqa: E402
from xx_audiobook import applog as applog_module  # noqa: E402
from xx_audiobook import prefs as prefs_module  # noqa: E402
from xx_audiobook.paths import ensure_app_dirs  # noqa: E402

TABS: tuple[tuple[str, str, str], ...] = (
    ("library", "Library", "Sign in to sync your library."),
    ("playing", "Playing", "Nothing playing right now."),
    ("downloads", "Downloads", "No downloaded books yet."),
    ("zones", "Zones", "No speaker zones yet."),
    ("setup", "Setup", "Sign in to set up audiobooks."),
)


def build_tab_page(name: str, title: str, text: str):
    header = widgets.page_header(title)
    body = widgets.empty_state(text)
    return widgets.page(header, body)


def build_window(app: Adw.Application):
    import gi as _gi

    _gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    xx_ui.apply(app)
    window = Gtk.ApplicationWindow(application=app)
    window.set_title("XX-Audiobook")
    window.set_default_size(360, 720)
    window.set_decorated(False)

    stack = Gtk.Stack()
    stack.set_hexpand(True)
    stack.set_vexpand(True)
    for name, title, text in TABS:
        stack.add_named(build_tab_page(name, title, text), name)

    state: dict = {"current": "library", "bar": None}
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    root.set_hexpand(True)
    root.set_vexpand(True)
    root.append(stack)

    def show(name: str) -> None:
        state["current"] = name
        stack.set_visible_child_name(name)
        bar = widgets.bottom_tabs([
            ("", label, tab == name, lambda tab=tab: show(tab))
            for tab, label, _text in TABS
        ])
        if state["bar"] is not None:
            root.remove(state["bar"])
        state["bar"] = bar
        root.append(bar)

    show("library")
    window.set_child(root)
    return window


class AudiobookApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is not None:
            window.present()
            return
        ensure_app_dirs()
        prefs_module.load()
        applog_module.get_logger().info("audiobook started")
        window = build_window(self)
        window.present()


def main(argv: list[str] | None = None) -> int:
    app = AudiobookApplication()
    return app.run(sys.argv if argv is None else [sys.argv[0], *argv])


if __name__ == "__main__":
    raise SystemExit(main())
