"""Tests for the battery-saver ("Saver") quick-settings tile.

Sorts after the other tile tests on purpose: quick_actions is shared via
sys.modules, so this module must not become its first importer.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

gi = pytest.importorskip('gi')

import quick_actions  # noqa: E402


class TestSaverTile:
    def _tile(self):
        return next(t for t in quick_actions._TILES if t.key == 'saver')

    def test_present_in_expanded_tier(self):
        tile = self._tile()
        assert tile.label == 'Saver'
        assert tile.tier == 2

    def test_position_keeps_existing_tail_order(self):
        keys = [t.key for t in quick_actions._TILES]
        assert keys.index('focus') < keys.index('saver') < \
            keys.index('auto_br')
        assert keys[-5:] == ['auto_br', 'location', 'hotspot', 'night',
                             'tailscale']

    def test_no_settings_section(self):
        assert quick_actions.tile_settings_section('saver') is None

    def test_get_set_round_trip(self):
        import app_standby
        saved = app_standby._default_controller
        try:
            app_standby._default_controller = None
            tile = self._tile()
            tile.set_state(False)
            assert tile.get_state() is False
            tile.set_state(True)
            assert tile.get_state() is True
            tile.set_state(False)
            assert tile.get_state() is False
        finally:
            app_standby._default_controller = saved
