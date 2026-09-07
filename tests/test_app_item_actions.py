"""Tests for app_item_actions.py mutation helpers — no widgets shown."""
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import ShellConfig

gi = pytest.importorskip('gi')
gi.require_version('Gtk', '4.0')

from app_index import AppEntry
from app_item_actions import AppItemActions


def _entry(app_id: str, name: str) -> AppEntry:
    return AppEntry(app_id=app_id, name=name, description='', executable='',
                    search_text=name.casefold(), desktop_app=None)


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    cfg = ShellConfig()
    cfg.set_home_slots([
        {'type': 'app', 'label': 'Notes', 'app_id': 'notes.desktop'},
        {'type': 'folder', 'label': 'Tools', 'folder': [
            {'label': 'Calc', 'app_id': 'calc.desktop'},
            {'label': 'Camera', 'app_id': 'camera.desktop'},
        ]},
    ])
    return cfg


@pytest.fixture
def actions(config):
    events = []
    act = AppItemActions(config, on_changed=lambda: events.append('changed'),
                         on_status=lambda msg: events.append(msg))
    act.events = events
    return act


class TestPasswordDialog:
    def test_wifi_title_is_a_password_field(self):
        assert AppItemActions._title_is_password('Password for HomeNet') is True

    def test_rename_title_is_not_a_password_field(self):
        assert AppItemActions._title_is_password('Rename') is False

    def test_entry_dialog_source_raises_osk(self):
        src = Path(__file__).parent.parent.joinpath(
            'launcher', 'src', 'app_item_actions.py').read_text(encoding='utf-8')
        assert 'on_keyboard' in src
        assert 'InputPurpose.PASSWORD' in src
        assert 'self._on_keyboard(True)' in src


class TestRenameInSlots:
    def test_updates_app_slot_and_folder_member(self, actions, config):
        actions._rename_in_slots('calc.desktop', 'Sums')
        members = config.home_slots[1]['folder']
        assert members[0]['label'] == 'Sums'

        actions._rename_in_slots('notes.desktop', 'Journal')
        assert config.home_slots[0]['label'] == 'Journal'

    def test_no_write_when_id_absent(self, actions, config):
        before = config.home_slots
        actions._rename_in_slots('missing.desktop', 'Nope')
        assert config.home_slots == before


class TestFolderMembership:
    def test_add_member(self, actions, config):
        actions._add_member(1, _entry('photos.desktop', 'Photos'), 'Photos')
        members = config.home_slots[1]['folder']
        assert members[-1] == {'label': 'Photos', 'app_id': 'photos.desktop'}
        assert 'changed' in actions.events

    def test_add_duplicate_is_noop(self, actions, config):
        actions._add_member(1, _entry('calc.desktop', 'Calc'), 'Calc')
        assert len(config.home_slots[1]['folder']) == 2
        assert 'changed' not in actions.events

    def test_move_member_down_and_clamp(self, actions, config):
        actions._move_member(1, 0, +1)
        members = config.home_slots[1]['folder']
        assert [m['label'] for m in members] == ['Camera', 'Calc']

        actions.events.clear()
        actions._move_member(1, 1, +1)
        assert [m['label'] for m in config.home_slots[1]['folder']] == ['Camera', 'Calc']
        assert 'changed' not in actions.events


class TestPinned:
    def test_pin_unpin_and_reorder(self, actions, config):
        a, b = _entry('a.desktop', 'A'), _entry('b.desktop', 'B')
        actions._set_pinned(a, True)
        actions._set_pinned(b, True)
        assert config.pinned == ['a.desktop', 'b.desktop']

        actions._move_pinned(b, -1)
        assert config.pinned == ['b.desktop', 'a.desktop']

        actions._set_pinned(a, False)
        assert config.pinned == ['b.desktop']


class TestHidden:
    def test_hide_and_show(self, actions, config):
        e = _entry('x.desktop', 'X')
        actions._set_hidden(e, True)
        assert 'x.desktop' in config.hidden_apps
        actions._set_hidden(e, False)
        assert 'x.desktop' not in config.hidden_apps


class TestMuteNormalization:
    def test_menu_written_mute_matches_desktop_entry_hint(self, config):
        config.set_app_muted('org.gnome.Calculator.desktop', 9e12)
        assert config.is_app_muted('org.gnome.Calculator')
        assert config.is_app_muted('org.gnome.Calculator.desktop')
        assert not config.is_app_muted('org.gnome.Clocks')
