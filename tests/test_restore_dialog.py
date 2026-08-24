"""GTK4 dialog conversion: _on_restore_backup must wire a 'response' signal
handler (dialog.run() no longer exists) that destroys the dialog and runs the
restore on the chosen path only for ResponseType.OK. Headless: window.py is
imported against a minimal fake gi stack; the dialog construction seam
(_build_restore_dialog) is stubbed so no GTK object is ever constructed."""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

_RESPONSE_OK = -5
_RESPONSE_CANCEL = -6


@pytest.fixture
def window_module(monkeypatch):
    """Import window.py with just enough gi.repository to satisfy its
    module-level imports (ShellWindow subclasses Adw.ApplicationWindow)."""

    def _require_version(name, _version):
        if name == 'Gtk4LayerShell':
            raise ValueError('not available headlessly')

    repo = types.ModuleType('gi.repository')
    for name in ('Gdk', 'GLib', 'Gtk', 'Pango', 'Gio'):
        setattr(repo, name, types.ModuleType(f'gi.repository.{name}'))
    repo.Gtk.ResponseType = types.SimpleNamespace(
        OK=_RESPONSE_OK, CANCEL=_RESPONSE_CANCEL)
    adw = types.ModuleType('gi.repository.Adw')
    adw.ApplicationWindow = type('ApplicationWindow', (), {})
    repo.Adw = adw

    gi = types.ModuleType('gi')
    gi.require_version = _require_version
    gi.repository = repo

    monkeypatch.setitem(sys.modules, 'gi', gi)
    monkeypatch.setitem(sys.modules, 'gi.repository', repo)
    monkeypatch.delitem(sys.modules, 'window', raising=False)

    import window
    return window


class _FakeDialog:
    def __init__(self):
        self.handler = None
        self.shown = False
        self.destroyed = False
        self.filename: str | None = '/home/user/backup.json'

    def connect(self, signal, callback):
        assert signal == 'response'
        self.handler = callback

    def show(self):
        self.shown = True

    def get_filename(self):
        return self.filename

    def destroy(self):
        self.destroyed = True


def _shell_harness(window_module, dialog):
    restored: list[str | None] = []
    shell = types.SimpleNamespace(
        _build_restore_dialog=lambda: dialog,
        _restore_from_path=restored.append,
        _show_status=lambda message: None,
    )
    window_module.ShellWindow._on_restore_backup(shell)
    return restored


class TestRestoreDialogWiring:
    def test_dialog_shown_with_response_handler(self, window_module):
        dialog = _FakeDialog()
        _shell_harness(window_module, dialog)
        assert dialog.shown
        assert callable(dialog.handler)
        assert not dialog.destroyed

    def test_ok_response_destroys_then_restores(self, window_module):
        dialog = _FakeDialog()
        restored = _shell_harness(window_module, dialog)
        dialog.handler(dialog, _RESPONSE_OK)
        assert dialog.destroyed
        assert restored == ['/home/user/backup.json']

    def test_cancel_response_destroys_without_restoring(self, window_module):
        dialog = _FakeDialog()
        restored = _shell_harness(window_module, dialog)
        dialog.handler(dialog, _RESPONSE_CANCEL)
        assert dialog.destroyed
        assert restored == []

    def test_filename_read_before_destroy(self, window_module):
        dialog = _FakeDialog()
        order: list[str] = []
        dialog.get_filename = lambda: order.append('read') or '/home/user/backup.json'
        dialog.destroy = lambda: order.append('destroy')
        restored = _shell_harness(window_module, dialog)
        dialog.handler(dialog, _RESPONSE_OK)
        assert order == ['read', 'destroy']
        assert restored == ['/home/user/backup.json']
