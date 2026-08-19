"""Tests for backup.py — round-trip and malformed-payload no-write guarantee."""
import copy
import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from backup import export_backup, restore_backup, validate_backup
from config import ShellConfig


@pytest.fixture
def populated_config(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    cfg = ShellConfig()
    cfg.set_home_slots([
        {'type': 'app', 'label': 'Notes', 'app_id': 'notes.desktop'},
        {'type': 'folder', 'label': 'Tools', 'folder': [
            {'label': 'Calc', 'app_id': 'calc.desktop'}]},
    ])
    cfg.data.update({
        'app_labels': {'notes.desktop': 'Notes'},
        'pinned': ['notes.desktop'],
        'hidden_apps': ['junk.desktop'],
        'theme': 'forest',
        'prefer_dark': True,
        'sound_notifications': False,
        'dnd_schedules': [{'days': [0], 'start': '22:00', 'end': '06:30'}],
        'dnd_starred_numbers': ['+15550102222'],
        'focus_apps': ['chat.desktop'],
        'weather_lat': 52.52,
        'weather_lon': 13.4,
        'lock_screen_notifications': 'count',
        'pin_hash': 'SECRET',
        'launch_counts': {'notes.desktop': 9},
        'muted_apps': {'chat': 9e12},
        'focus_break_until': 9e12,
    })
    cfg.save()
    return cfg


class TestExport:
    def test_excludes_secrets_and_transient_state(self, populated_config):
        backup = export_backup(populated_config)
        assert 'pin_hash' not in json.dumps(backup)
        assert 'launch_counts' not in backup
        assert 'muted_apps' not in backup
        assert 'focus_break_until' not in backup

    def test_covers_prefs(self, populated_config):
        backup = export_backup(populated_config)
        assert backup['version'] == 1
        assert backup['theme'] == 'forest'
        assert backup['dnd_schedules'] == [{'days': [0], 'start': '22:00', 'end': '06:30'}]
        assert backup['focus_apps'] == ['chat.desktop']
        assert backup['weather_lat'] == 52.52
        assert backup['lock_screen_notifications'] == 'count'
        assert backup['pinned'] == ['notes.desktop']


class TestRoundTrip:
    def test_export_restore_onto_fresh_config(self, populated_config, tmp_path, monkeypatch):
        backup = export_backup(populated_config)

        monkeypatch.setenv('HOME', str(tmp_path / 'home2'))
        fresh = ShellConfig()
        assert restore_backup(fresh, copy.deepcopy(backup))

        assert fresh.home_slots == populated_config.home_slots
        assert fresh.data['app_labels'] == {'notes.desktop': 'Notes'}
        assert fresh.pinned == ['notes.desktop']
        assert fresh.hidden_apps == ['junk.desktop']
        assert fresh.data['theme'] == 'forest'
        assert fresh.data['dnd_starred_numbers'] == ['+15550102222']
        assert fresh.data['focus_apps'] == ['chat.desktop']
        assert fresh.data['weather_lat'] == 52.52
        assert fresh.lock_screen_notifications == 'count'
        assert fresh.data.get('pin_hash') is None
        # A second export matches the first — nothing drifted
        assert export_backup(fresh) == backup


class TestMalformedPayloads:
    @pytest.fixture
    def fresh(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        return ShellConfig()

    @pytest.mark.parametrize('mutate', [
        lambda p: p.update(version=2),
        lambda p: p.update(home_slots='not a list'),
        lambda p: p.update(home_slots=[{'type': 'app'}] * 9),
        lambda p: p.update(home_slots=[{'type': 'wallpaper'}]),
        lambda p: p.update(widgets='no'),
        lambda p: p.update(theme='custom', custom_background='red'),
        lambda p: p.update(text_size_scale=9.0),
        lambda p: p.update(auto_lock_timeout=-5),
        lambda p: p.update(home_alignment='justified'),
        lambda p: p.update(lock_screen_notifications='banners'),
        lambda p: p.update(dnd_enabled='yes'),
        lambda p: p.update(focus_apps='chat'),
        lambda p: p.update(weather_lat='north'),
        lambda p: p.update(gestures={'made_up_gesture': 'home'}),
        lambda p: p.update(gestures={'swipe_left_home': 'explode'}),
        lambda p: p.update(gestures={'swipe_left_home': 'launch:'}),
    ])
    def test_invalid_payload_writes_nothing(self, fresh, mutate):
        payload = {'version': 1, 'home_slots': [], 'theme': 'amoled',
                   'font': 'jetbrains-mono-nerd'}
        mutate(payload)
        before = copy.deepcopy(fresh.data)
        ok, error = validate_backup(payload)
        assert not ok and error
        assert not restore_backup(fresh, payload)
        assert fresh.data == before
        assert not fresh.config_path.exists()

    def test_truncated_json_is_callers_problem_but_never_a_dict(self, fresh):
        try:
            payload = json.loads('{"version": 1, "home_slo')
        except json.JSONDecodeError:
            payload = None
        assert payload is None
        assert not restore_backup(fresh, payload or [])
