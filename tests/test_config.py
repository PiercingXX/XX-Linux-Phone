"""Tests for config.py - pure logic, no GTK imports."""
from pathlib import Path

import pytest

# Add launcher/src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import DEFAULT_CONFIG, ShellConfig


class TestShellConfig:
    """Tests for ShellConfig class."""

    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        # ShellConfig() reads ~/.config/xx-wm/config.json in its
        # constructor; point HOME at a temp dir so the dev box's real shell
        # config never leaks into tests.
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))

    def test_default_config_has_home_slots(self):
        """DEFAULT_CONFIG must include home_slots key."""
        assert 'home_slots' in DEFAULT_CONFIG
        assert isinstance(DEFAULT_CONFIG['home_slots'], list)

    def test_default_config_has_default_layout_applied(self):
        """DEFAULT_CONFIG must include default_layout_applied key."""
        assert 'default_layout_applied' in DEFAULT_CONFIG
        assert isinstance(DEFAULT_CONFIG['default_layout_applied'], bool)

    def test_home_slots_property_empty_by_default(self, tmp_path):
        """home_slots returns empty list when not set."""
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        config.load()
        assert config.home_slots == []

    def test_home_slots_validation(self, tmp_path):
        """home_slots validates slot types and caps at 8."""
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        
        # Create slots with various valid/invalid entries
        slots = [
            {'type': 'app', 'label': 'Test App', 'app_id': 'test.desktop'},
            {'type': 'folder', 'label': 'Test Folder', 'folder': []},
            {'type': 'invalid', 'label': 'Bad'},  # Invalid type
            'not a dict',  # Not a dict
            {'type': 'app', 'label': 'App2', 'cmd': ['echo', 'hello']},
        ]
        config.set_home_slots(slots)
        
        result = config.home_slots
        assert len(result) == 3  # Only valid entries
        
        # Check first slot
        assert result[0]['type'] == 'app'
        assert result[0]['label'] == 'Test App'
        assert result[0]['app_id'] == 'test.desktop'
        
        # Check second slot (folder)
        assert result[1]['type'] == 'folder'
        assert result[1]['label'] == 'Test Folder'
        
        # Check third slot (cmd-based app)
        assert result[2]['type'] == 'app'
        assert result[2]['cmd'] == ['echo', 'hello']

    def test_home_slots_cap_at_8(self, tmp_path):
        """home_slots caps at 8 entries."""
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        
        slots = [{'type': 'app', 'label': f'Slot {i}', 'app_id': f'slot{i}.desktop'} for i in range(10)]
        config.set_home_slots(slots)
        
        result = config.home_slots
        assert len(result) == 8

    def test_set_default_layout_applied(self, tmp_path):
        """set_default_layout_applied persists correctly."""
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        
        config.set_default_layout_applied(True)
        config.load()  # Reload from file
        
        assert config.default_layout_applied is True

    def test_new_defaults_match_android_overhaul(self):
        """Defaults carried over from the launcher overhaul (ea84fbf)."""
        assert DEFAULT_CONFIG['font'] == 'jetbrains-mono-nerd'
        assert DEFAULT_CONFIG['home_alignment'] == 'center'
        widgets = DEFAULT_CONFIG['widgets']
        ordered = sorted(widgets, key=lambda k: widgets[k]['order'])
        assert ordered == ['time', 'date', 'weather', 'battery']
        assert widgets['weather']['enabled'] is True

    def test_app_labels_roundtrip(self, tmp_path):
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'

        config.set_app_label('org.gnome.Calendar', 'Calendar')
        assert config.label_for('org.gnome.Calendar', 'GNOME Calendar') == 'Calendar'
        assert config.label_for('unknown.app', 'Fallback') == 'Fallback'

        config.set_app_label('org.gnome.Calendar', None)
        assert config.label_for('org.gnome.Calendar', 'GNOME Calendar') == 'GNOME Calendar'

    def test_muted_apps_window(self, tmp_path):
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'

        now = 1_000_000.0
        config.set_app_muted('org.example.Chat', now + 3600, now=now)
        assert config.is_app_muted('org.example.Chat', now=now) is True
        assert config.is_app_muted('org.example.Chat', now=now + 3599) is True
        assert config.is_app_muted('org.example.Chat', now=now + 3601) is False
        assert config.is_app_muted('org.other.App', now=now) is False
        assert config.is_app_muted('', now=now) is False

    def test_muted_apps_expired_entries_pruned(self, tmp_path):
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'

        now = 1_000_000.0
        config.set_app_muted('a.app', now + 10, now=now)
        config.set_app_muted('b.app', now + 3600, now=now + 20)  # a.app expired by now+20
        assert 'a.app' not in config.muted_apps
        assert 'b.app' in config.muted_apps

    def test_migration_from_old_config(self, tmp_path):
        """Old configs without home_slots should work."""
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        
        # Write an old-style config
        config.data = {'theme': 'amoled', 'font': 'space-mono'}
        config.save()
        
        # Load it back
        config.load()
        
        assert config.theme.key == 'amoled'
        assert config.home_slots == []
        assert config.default_layout_applied is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
