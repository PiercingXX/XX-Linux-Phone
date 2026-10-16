import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from xx_contacts import __appid__  # noqa: E402
from xx_contacts import shell_theme  # noqa: E402
from xx_contacts.app_config import AppConfig, default_config_path  # noqa: E402
from xx_contacts.list_ui import ContactsWindow  # noqa: E402
from xx_contacts.store import ContactStore, default_db_path  # noqa: E402

import os  # noqa: E402
import sys  # noqa: E402

#: vCard suffixes Files hands the app: each opens the import confirm page.
VCF_SUFFIXES = (".vcf", ".vcard")


def is_vcf_path(path: str) -> bool:
    """True for local ``.vcf``/``.vcard`` files the import page can read."""
    return (path or "").strip().lower().endswith(VCF_SUFFIXES)


class ContactsApp(Adw.Application):
    """Contacts app: list window plus consumer entry points.

    ``--new`` opens a blank editor over the list; ``--tel NUMBER``
    prefills its first phone row (and implies ``--new``). Bare ``.vcf``
    arguments open the import confirm page for each file. All three
    arrive through ``do_command_line``, so a second launch forwards them
    to the already-running instance instead of dropping them.
    """

    def __init__(self, pending_new=(), pending_files=()) -> None:
        super().__init__(
            application_id=__appid__,
            flags=(Gio.ApplicationFlags.HANDLES_OPEN
                   | Gio.ApplicationFlags.HANDLES_COMMAND_LINE),
        )
        self.add_main_option(
            "new", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Open a new-contact editor", None)
        self.add_main_option(
            "tel", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
            "Prefill the new-contact editor with this number", "NUMBER")
        self._pending_new = [str(tel or "") for tel in pending_new]
        self._pending_files = [str(path) for path in pending_files]

    def do_command_line(self,
                        command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict()
        tel_value = options.lookup_value("tel", None)
        tel = tel_value.get_string() if tel_value is not None else ""
        if options.contains("new") or tel:
            self._pending_new.append(tel)
        args = command_line.get_arguments() or ()
        for arg in args[1:]:
            try:
                target = command_line.create_file_for_arg(arg)
            except Exception:
                continue
            path = target.get_path()
            if path and is_vcf_path(path):
                if path not in self._pending_files:
                    self._pending_files.append(path)
        self.activate()
        return 0

    def do_activate(self) -> None:
        home = os.path.expanduser("~")
        shell_data = shell_theme.load_shell_config(
            os.path.join(home, ".config", "xx-wm", "config.json"))
        preset = shell_theme.resolve_preset(shell_data)
        css = shell_theme.to_css(
            preset,
            shell_theme.resolve_font_family(shell_data),
            shell_theme.resolve_text_scale(shell_data),
        )
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
        window = self.get_active_window()
        if window is None:
            config_path = default_config_path(home)
            config = AppConfig.load(config_path)
            store = ContactStore(default_db_path(home))
            window = ContactsWindow(self, store, config, config_path,
                                    store.path)
        window.present()
        self._drain_pending(window)

    def _drain_pending(self, window) -> None:
        for tel in self._pending_new:
            window.open_new_editor(prefill_phone=tel)
        del self._pending_new[:]
        for path in self._pending_files:
            window.open_vcf_file(path)
        del self._pending_files[:]


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    app = ContactsApp()
    app.run([sys.argv[0]] + args)


if __name__ == "__main__":
    main()
