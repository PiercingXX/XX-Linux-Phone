"""L6 launch tests: vCard MIME entry, ``--new --tel`` routing, ``.vcf`` open.

Headless: command-line parsing drives ``ContactsApp.do_command_line``
against a fake command line (real ``GLib.VariantDict`` options, real
``Gio.File`` targets), with ``activate()`` stubbed so no window or
store is touched. The pushed pages themselves are covered by the L6
Broadway smoke test.
"""

from pathlib import Path

from gi.repository import Gio, GLib

from xx_contacts import main as main_module
from xx_contacts.list_model import call_uri

REPO = Path(__file__).parent.parent


class FakeCommandLine:
    def __init__(self, flags=(), tel=None, args=()):
        box = GLib.VariantDict.new(None)
        for flag in flags:
            box.insert_value(flag, GLib.Variant.new_boolean(True))
        if tel is not None:
            box.insert_value("tel", GLib.Variant.new_string(tel))
        self._box = box
        self._args = list(args)

    def get_options_dict(self):
        return self._box

    def get_arguments(self):
        return self._args

    def create_file_for_arg(self, arg):
        return Gio.File.new_for_path(arg)


def _app_with_stubbed_activate(monkeypatch):
    app = main_module.ContactsApp()
    activated = []
    monkeypatch.setattr(app, "activate", lambda: activated.append(True))
    return app, activated


def test_desktop_entry_handles_vcard_and_takes_files():
    text = (REPO / "data" / "io.piercingxx.XXContacts.desktop.in"
            ).read_text(encoding="utf-8")
    assert "MimeType=text/vcard;text/x-vcard;" in text
    exec_line = next(line for line in text.splitlines()
                     if line.startswith("Exec="))
    assert "%F" in exec_line


def test_app_registers_open_flags_and_consumer_options():
    src = (REPO / "src" / "xx_contacts" / "main.py"
           ).read_text(encoding="utf-8")
    assert "HANDLES_OPEN" in src
    assert "HANDLES_COMMAND_LINE" in src
    assert '"new"' in src and '"tel"' in src
    assert "def do_command_line" in src
    assert "create_file_for_arg" in src
    assert "open_new_editor" in src and "open_vcf_file" in src
    editor = (REPO / "src" / "xx_contacts" / "editor_ui.py"
              ).read_text(encoding="utf-8")
    assert "prefill_phone" in editor
    page = (REPO / "src" / "xx_contacts" / "import_export_ui.py"
            ).read_text(encoding="utf-8")
    assert "def import_file" in page


def test_is_vcf_path_matches_vcard_suffixes_only():
    assert main_module.is_vcf_path("/tmp/card.vcf")
    assert main_module.is_vcf_path("/tmp/card.VCF")
    assert main_module.is_vcf_path("card.vcard")
    assert not main_module.is_vcf_path("/tmp/card.txt")
    assert not main_module.is_vcf_path("")
    assert not main_module.is_vcf_path("/tmp/vcf-backup.json")


def test_new_with_tel_queues_prefilled_editor(monkeypatch):
    app, activated = _app_with_stubbed_activate(monkeypatch)
    command = FakeCommandLine(flags=("new",), tel="+1 555 0100",
                              args=["xx-contacts"])
    assert app.do_command_line(command) == 0
    assert app._pending_new == ["+1 555 0100"]
    assert app._pending_files == []
    assert activated == [True]


def test_tel_without_new_still_opens_the_editor(monkeypatch):
    app, activated = _app_with_stubbed_activate(monkeypatch)
    app.do_command_line(FakeCommandLine(tel="+1555", args=["xx-contacts"]))
    assert app._pending_new == ["+1555"]
    assert activated == [True]


def test_bare_launch_queues_nothing(monkeypatch):
    app, activated = _app_with_stubbed_activate(monkeypatch)
    app.do_command_line(FakeCommandLine(args=["xx-contacts"]))
    assert app._pending_new == [] and app._pending_files == []
    assert activated == [True]


def test_vcf_arguments_queue_import_pages_ignoring_other_files(
        monkeypatch, tmp_path):
    keep = tmp_path / "card.vcf"
    keep.write_text("BEGIN:VCARD\r\nEND:VCARD\r\n", encoding="utf-8")
    app, _activated = _app_with_stubbed_activate(monkeypatch)
    app.do_command_line(FakeCommandLine(
        args=["xx-contacts", str(keep), str(tmp_path / "note.txt"),
              str(keep)]))
    assert app._pending_files == [str(keep)]


def test_detail_call_action_uses_tel_uri():
    assert call_uri("+1 555 0100") == "tel:+15550100"
    assert call_uri("") == ""
