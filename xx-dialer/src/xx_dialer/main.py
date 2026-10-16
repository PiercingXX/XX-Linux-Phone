"""XX-Dialer entry point: Adw.Application GUI, or the headless policy service.

The ``--service`` path serves the Policy1 endpoint the shell consults
before its first ring. It must stay light enough to answer inside the
shell's 2000 ms consult timeout on a cold D-Bus-activation start, so
nothing on that path imports Gtk/Adw — those imports live inside the
GUI functions below. ``--service --check-imports`` verifies that
without starting the service (used by the test suite).

``build_dialer_view`` assembles the real main window content (Recents +
Keypad) over explicit deps without needing an application instance, so
the Broadway smoke test can build it against a seeded store.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

APP_ID = "io.piercingxx.XXDialer"


@dataclass
class DialerDeps:
    db_file: Path
    home: Path | None = None
    voice: object = None
    contacts: object = None


def apply_shell_theme() -> None:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")

    from gi.repository import Gdk, Gtk  # noqa: E402

    from xx_dialer import theme as xx_theme

    display = Gdk.Display.get_default()
    if display is None:
        return
    shell_theme = xx_theme.load_shell_theme()
    provider = Gtk.CssProvider()
    provider.load_from_data(xx_theme.theme_css(shell_theme)
                            + _dialer_css(shell_theme))
    Gtk.StyleContext.add_provider_for_display(
        display,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def _dialer_css(shell_theme) -> str:
    """List and keypad paint from the preset: no white lists in dark."""
    background = shell_theme.background
    foreground = shell_theme.foreground
    muted = shell_theme.muted
    family = shell_theme.font_family
    return (
        "listview, listview row, listbox, listbox row"
        " { background-color: %s; color: %s; }\n"
        ".xx-number { color: %s; font-family: %s;"
        " font-size: 28pt; }\n"
        ".xx-key { padding: 10px 4px; }\n"
        ".xx-chip { padding: 4px 10px; }\n"
        ".xx-muted { color: %s; font-family: %s; }\n"
        % (background, foreground, foreground, family, muted, family)
    )


def build_dialer_view(deps: DialerDeps):
    """Assemble the main window content over explicit deps.

    Returns a ``DialerView`` with the root widget, both page controllers
    and refresh/prefill entry points. Gtk/Adw imports stay inside this
    function so importing this module never touches the toolkit.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")

    from gi.repository import Adw, Gtk  # noqa: E402

    from xx_dialer import (  # noqa: E402
        contacts_client,
        modem,
        ui_backup,
        ui_keypad,
        ui_logs,
        ui_people,
        ui_recents,
        ui_rules,
        ui_setup,
    )
    from xx_dialer.service import ensure_dirs  # noqa: E402
    from xx_dialer.store import Store  # noqa: E402

    home = deps.home if deps.home is not None else Path.home()
    try:
        ensure_dirs(home)
    except Exception:
        pass
    db_file = deps.db_file
    try:
        from xx_dialer import config as app_config

        hidden = set(app_config.load(home).get("hidden_tabs") or [])
    except Exception:
        hidden = set()

    toast = Adw.ToastOverlay()

    def _notice(text: str) -> None:
        try:
            toast.add_toast(Adw.Toast.new(text))
        except Exception:
            pass

    voice = deps.voice
    if voice is None:
        try:
            voice = modem.VoiceClient()
            if not voice.start():
                voice = None
        except Exception:
            voice = None
    client = deps.contacts
    if client is None:
        try:
            with Store(db_file):
                pass
            candidate = contacts_client.ContactsClient(Store(db_file))
            if candidate.start():
                client = candidate
        except Exception:
            client = None
    recents_deps = ui_recents.RecentsDeps(
        db_file=db_file, home=home, voice=voice, contacts=client,
        notice=_notice)
    recents_controller = ui_recents.RecentsController(recents_deps)
    recents_view = ui_recents.build_recents(recents_controller)
    keypad_view = ui_keypad.build_keypad(
        ui_keypad.KeypadDeps(db_file=db_file, home=home, voice=voice,
                             contacts=client, notice=_notice))

    header = Adw.HeaderBar()
    header.set_show_start_title_buttons(False)
    header.set_show_end_title_buttons(False)
    header.set_title_widget(Gtk.Label(label="XX Dialer"))
    stack = Adw.ViewStack()
    recents_page = stack.add_titled(recents_view.widget, "recents",
                                   "Recents")
    if "keypad" not in hidden:
        stack.add_titled(keypad_view.widget, "keypad", "Keypad")
    people_view = ui_people.build_people(
        ui_people.PeopleDeps(db_file=db_file, home=home, voice=voice,
                             contacts=client, notice=_notice))
    if "people" not in hidden:
        stack.add_titled(people_view.widget, "people", "People")
    rules_view = ui_rules.build_rules(
        ui_rules.RulesDeps(db_file=db_file, home=home, notice=_notice))
    stack.add_titled(rules_view.widget, "rules", "Rules")
    setup_view = ui_setup.build_setup(
        ui_setup.SetupDeps(db_file=db_file, home=home, notice=_notice))
    stack.add_titled(setup_view.widget, "setup", "Setup")
    backup_view = ui_backup.build_backup(
        ui_backup.BackupDeps(db_file=db_file, home=home, notice=_notice))
    stack.add_titled(backup_view.widget, "backup", "Backup")
    logs_view = ui_logs.build_logs(
        ui_logs.LogsDeps(db_file=db_file, home=home, notice=_notice))
    stack.add_titled(logs_view.widget, "logs", "Logs")
    switcher = Adw.ViewSwitcherBar()
    switcher.set_stack(stack)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(header)
    toolbar.set_content(stack)
    toolbar.add_bottom_bar(switcher)
    toast.set_child(toolbar)

    view = DialerView(toast, stack, recents_page, recents_controller,
                      recents_view, keypad_view, voice, db_file,
                      people_view, rules_view, setup_view,
                      backup_view, logs_view)
    stack.connect("notify::visible-child", view._on_page_shown)
    view.refresh_all()
    if voice is not None:
        _watch_calls(voice, view)
    return view


def _watch_calls(voice, view) -> None:
    """Refresh Recents on call changes.

    The shell owns the incoming surface and its missed-call card, and
    the policy service process owns the silenced-record cards, so this
    watcher only keeps the store-driven UI honest: any call-list or
    state change refreshes Recents (rows and the unread badge). It
    never posts a notification.
    """
    import gi

    gi.require_version("Gtk", "4.0")

    from gi.repository import GLib  # noqa: E402

    def _on_calls(_calls) -> None:
        try:
            GLib.idle_add(view.refresh_all)
        except Exception:
            pass

    def _on_state(_path: str, _old: int, _new: int,
                  _reason: int) -> None:
        try:
            GLib.idle_add(view.refresh_all)
        except Exception:
            pass

    try:
        voice._on_calls = _on_calls
        voice._on_state = _on_state
    except Exception:
        pass


class DialerView:
    """Live handles over the assembled main window content."""

    def __init__(self, widget, stack, recents_page,
                 recents_controller, recents_view, keypad_view,
                 voice, db_file, people_view=None, rules_view=None,
                 setup_view=None, backup_view=None, logs_view=None) -> None:
        self.widget = widget
        self.stack = stack
        self._recents_page = recents_page
        self.recents = recents_controller
        self.recents_view = recents_view
        self.keypad = keypad_view
        self.voice = voice
        self._db_file = db_file
        self.people = people_view
        self.rules = rules_view
        self.setup = setup_view
        self.backup = backup_view
        self.logs = logs_view

    def _on_page_shown(self, _stack, _spec) -> None:
        if self.stack.get_visible_child_name() == "recents":
            self.recents.mark_viewed()
            self.refresh_all()

    def refresh_all(self) -> None:
        try:
            self.recents_view.refresh()
        except Exception:
            pass
        try:
            self.keypad.refresh()
        except Exception:
            pass
        for page in (self.people, self.rules, self.setup, self.backup,
                     self.logs):
            if page is None:
                continue
            try:
                page.refresh()
            except Exception:
                pass
        try:
            self._recents_page.set_badge_number(self.recents.unread)
        except Exception:
            pass

    def prefill(self, number: str) -> None:
        try:
            self.keypad.set_digits(number)
        except Exception:
            pass
        try:
            self.stack.set_visible_child_name("keypad")
        except Exception:
            pass

def run_gui(args: list[str]) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")

    from gi.repository import Adw, Gio  # noqa: E402

    from xx_dialer.service import ensure_dirs  # noqa: E402
    from xx_dialer.store import db_path  # noqa: E402

    class DialerApp(Adw.Application):
        def __init__(self) -> None:
            super().__init__(
                application_id=APP_ID,
                flags=Gio.ApplicationFlags.HANDLES_OPEN)
            self._view: DialerView | None = None

        def do_activate(self) -> None:
            window = Adw.ApplicationWindow(application=self)
            window.set_title("XX Dialer")
            window.set_default_size(360, 720)
            deps = DialerDeps(db_file=db_path(), home=Path.home())
            self._view = build_dialer_view(deps)
            window.set_content(self._view.widget)
            apply_shell_theme()
            window.present()

        def do_open(self, files, _hint) -> None:
            self.do_activate()
            from urllib.parse import unquote

            for item in files:
                uri = item.get_uri() if hasattr(item, "get_uri") \
                    else str(item)
                number = uri
                if number.startswith("tel:"):
                    number = unquote(number[len("tel:"):])
                if self._view is not None and number.strip():
                    self._view.prefill(number.strip())
                break

    ensure_dirs()
    app = DialerApp()
    return app.run(args)


def run_service() -> int:
    """Policy-service entry point: serve Policy1 headless (no windows)."""
    from xx_dialer.service import run_service as serve

    return serve()


def check_service_imports() -> int:
    """Fail when the --service path pulls Gtk/Adw into the process."""
    import xx_dialer.service  # noqa: F401
    import xx_dialer.contacts_client  # noqa: F401

    heavy = sorted(
        name for name in ("gi.repository.Gtk", "gi.repository.Adw")
        if name in sys.modules)
    if heavy:
        print("service imports Gtk/Adw: %s" % ", ".join(heavy))
        return 1
    print("service imports: no Gtk/Adw")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--service" in args:
        if "--check-imports" in args:
            return check_service_imports()
        return run_service()
    return run_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
