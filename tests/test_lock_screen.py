"""Tests for the lock screen's notification line filtering (pure logic)."""
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

gi = pytest.importorskip('gi')
gi.require_version('Gtk', '4.0')

from lock_screen import lock_screen_lines

FEED = [('Chat', 'New message from Sam'), ('Email', '2 unread'), ('', 'orphan summary')]


class TestLockScreenLines:
    def test_summary_mode(self):
        lines = lock_screen_lines(FEED, 'summary', dnd_active=False)
        assert lines == [
            'Chat — New message from Sam',
            'Email — 2 unread',
            'orphan summary',
        ]

    def test_count_mode(self):
        assert lock_screen_lines(FEED, 'count', dnd_active=False) == ['3 notifications']
        assert lock_screen_lines(FEED[:1], 'count', dnd_active=False) == ['1 notification']

    def test_off_mode(self):
        assert lock_screen_lines(FEED, 'off', dnd_active=False) == []

    def test_dnd_hides_everything(self):
        assert lock_screen_lines(FEED, 'summary', dnd_active=True) == []
        assert lock_screen_lines(FEED, 'count', dnd_active=True) == []

    def test_empty_feed(self):
        assert lock_screen_lines([], 'summary', dnd_active=False) == []
        assert lock_screen_lines([], 'count', dnd_active=False) == []
