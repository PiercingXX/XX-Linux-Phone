"""Tests for the lock screen's notification line filtering (pure logic)."""
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from lock_lines import _FAIL_THRESHOLD, _lockout_secs, lock_screen_lines

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

    def test_empty_app_name_filtered_from_join(self):
        # An empty app name must not leave a leading ' — ' in the line; the
        # summary alone is kept.
        assert lock_screen_lines([('', 'orphan summary')], 'summary', dnd_active=False) \
            == ['orphan summary']
        assert lock_screen_lines([('Chat', ''), ('', '')], 'summary', dnd_active=False) \
            == ['Chat']


class TestLockoutSecs:
    def test_below_threshold_no_lockout(self):
        assert _lockout_secs(_FAIL_THRESHOLD - 1) == 0
        assert _lockout_secs(0) == 0

    def test_at_threshold_starts_lockout(self):
        assert _lockout_secs(_FAIL_THRESHOLD) == 30

    def test_scales_with_failures(self):
        assert _lockout_secs(_FAIL_THRESHOLD + 1) == 60

    def test_capped_at_five_minutes(self):
        assert _lockout_secs(_FAIL_THRESHOLD + 100) == 300
