"""In-shell restore flow: no file chooser (a toplevel would open UNDER the
layer-shell TOP surface). "Restore from backup" lists xx-wm-backup-*.json
from ~ and ~/Documents (newest first, date labels, max 10) plus Cancel in
the themed action menu; picking one confirms, then restores from the path.

Headless: window.py is imported against a minimal fake gi stack and driven
on bare ShellWindow instances; backup discovery runs against a tmp HOME."""
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


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


def _write_backup(directory: Path, stamp: str) -> Path:
    payload = {'version': 1, 'home_slots': [], 'theme': 'amoled',
               'font': 'jetbrains-mono-nerd'}
    path = directory / f'xx-wm-backup-{stamp}.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


@pytest.fixture
def home_with_backups(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    docs = home / 'Documents'
    docs.mkdir(parents=True)
    _write_backup(home, '20240102_080000')
    _write_backup(docs, '20240304_120000')
    _write_backup(docs, '20240203_090000')
    (home / 'notes.txt').write_text('not a backup', encoding='utf-8')
    monkeypatch.setenv('HOME', str(home))
    return home


class _Menus:
    def __init__(self):
        self.calls: list[tuple] = []

    def _show_action_menu(self, anchor, title, actions):
        self.calls.append((anchor, title, list(actions)))


def _restore_session(window_module, tmp_path, monkeypatch):
    from config import ShellConfig
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    (tmp_path / 'home').mkdir(parents=True, exist_ok=True)
    menus = _Menus()
    statuses: list[str] = []
    applied: list[str] = []
    shell = types.SimpleNamespace(
        config=ShellConfig(),
        item_actions=menus,
        _restore_btn=object(),
        _show_status=statuses.append,
        _apply_config_change=lambda: applied.append('applied'),
    )
    _write_backup(tmp_path / 'home', '20240102_080000')
    shell._restore_from_path = (
        window_module.ShellWindow._restore_from_path.__get__(shell))
    shell._confirm_restore_backup = (
        window_module.ShellWindow._confirm_restore_backup.__get__(shell))
    window_module.ShellWindow._on_restore_backup(shell, object())
    return menus, statuses, applied


def _by_label(actions):
    return {label: callback for label, callback in actions}


class TestRestoreMenu:
    def test_lists_backups_newest_first_with_date_labels_and_cancel(
            self, window_module, home_with_backups):
        shell = types.SimpleNamespace(
            item_actions=_Menus(), _restore_btn=object(),
            _show_status=lambda message: None,
        )
        window_module.ShellWindow._on_restore_backup(shell, object())
        menus = shell.item_actions
        assert len(menus.calls) == 1
        _anchor, title, actions = menus.calls[0]
        assert title == 'Restore from backup'
        assert [label for label, _cb in actions] == [
            '2024-03-04 12:00', '2024-02-03 09:00', '2024-01-02 08:00',
            'Cancel',
        ]

    def test_empty_shows_status_and_no_menu(
            self, window_module, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        (tmp_path / 'home').mkdir(parents=True, exist_ok=True)
        statuses: list[str] = []
        menus = _Menus()
        shell = types.SimpleNamespace(
            item_actions=menus, _restore_btn=object(),
            _show_status=statuses.append,
        )
        window_module.ShellWindow._on_restore_backup(shell, object())
        assert statuses == ['No backups found in ~ or ~/Documents']
        assert menus.calls == []

    def test_no_file_chooser_left(self, window_module):
        import inspect

        import settings_page
        src = inspect.getsource(settings_page.SettingsPage._on_restore_backup)
        assert 'FileChooser' not in src
        assert 'get_filename' not in src
        assert '_show_action_menu' in src


class TestRestoreConfirm:
    def test_pick_confirms_then_restores(
            self, window_module, tmp_path, monkeypatch):
        menus, statuses, applied = _restore_session(
            window_module, tmp_path, monkeypatch)
        _anchor, _title, actions = menus.calls[0]
        _by_label(actions)['2024-01-02 08:00']()
        assert len(menus.calls) == 2
        _anchor, title, confirm_actions = menus.calls[1]
        assert title == ('Restore backup from 2024-01-02 08:00? '
                        'This replaces your home layout and settings.')
        _by_label(confirm_actions)['Restore']()
        assert applied == ['applied']
        assert statuses == ['Backup restored successfully']

    def test_confirm_cancel_restores_nothing(
            self, window_module, tmp_path, monkeypatch):
        menus, statuses, applied = _restore_session(
            window_module, tmp_path, monkeypatch)
        _anchor, _title, actions = menus.calls[0]
        _by_label(actions)['2024-01-02 08:00']()
        _anchor, _title, confirm_actions = menus.calls[1]
        _by_label(confirm_actions)['Cancel']()
        assert applied == []
        assert statuses == []

    def test_list_cancel_restores_nothing(
            self, window_module, tmp_path, monkeypatch):
        menus, statuses, applied = _restore_session(
            window_module, tmp_path, monkeypatch)
        _anchor, _title, actions = menus.calls[0]
        _by_label(actions)['Cancel']()
        assert applied == []
        assert statuses == []
        assert len(menus.calls) == 1

    def test_invalid_payload_reports_without_applying(
            self, window_module, tmp_path, monkeypatch):
        from config import ShellConfig
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        home = tmp_path / 'home'
        home.mkdir(parents=True, exist_ok=True)
        bad = home / 'xx-wm-backup-20240102_080000.json'
        bad.write_text('{"version": 99}', encoding='utf-8')
        statuses: list[str] = []
        applied: list[str] = []
        shell = types.SimpleNamespace(
            config=ShellConfig(),
            _show_status=statuses.append,
            _apply_config_change=lambda: applied.append('applied'),
        )
        shell._restore_from_path = (
            window_module.ShellWindow._restore_from_path.__get__(shell))
        shell._restore_from_path(str(bad))
        assert applied == []
        assert len(statuses) == 1
        assert statuses[0].startswith('Invalid backup:')


class TestExportDestination:
    def _export_shell(self, window_module):
        from config import ShellConfig
        statuses: list[str] = []
        shell = types.SimpleNamespace(
            config=ShellConfig(), _show_status=statuses.append)
        window_module.ShellWindow._on_export_backup(shell)
        return statuses

    def test_export_prefers_documents(
            self, window_module, tmp_path, monkeypatch):
        home = tmp_path / 'home'
        (home / 'Documents').mkdir(parents=True)
        monkeypatch.setenv('HOME', str(home))
        statuses = self._export_shell(window_module)
        exported = list((home / 'Documents').glob('xx-wm-backup-*.json'))
        assert len(exported) == 1
        assert list(home.glob('xx-wm-backup-*.json')) == []
        assert statuses == [f'Backup exported to {exported[0]}']

    def test_export_falls_back_to_home(
            self, window_module, tmp_path, monkeypatch):
        home = tmp_path / 'home'
        home.mkdir(parents=True)
        monkeypatch.setenv('HOME', str(home))
        statuses = self._export_shell(window_module)
        exported = list(home.glob('xx-wm-backup-*.json'))
        assert len(exported) == 1
        assert statuses == [f'Backup exported to {exported[0]}']
