"""Tests for focus_mode.py — breaks, holding, paused matching, schedules."""
from datetime import datetime
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import ShellConfig
from focus_mode import FocusState, HeldNotifications

MON_NOON = datetime(2026, 7, 20, 12, 0)
MON_2300 = datetime(2026, 7, 20, 23, 0)


@pytest.fixture
def focus(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    return FocusState(ShellConfig())


class TestActive:
    def test_manual_toggle(self, focus):
        assert not focus.is_active(MON_NOON, now_ts=1000.0)
        focus.set_enabled(True)
        assert focus.is_active(MON_NOON, now_ts=1000.0)

    def test_schedule_reuses_dnd_matcher(self, focus):
        focus._config.data['focus_schedules'] = [
            {'days': [0], 'start': '22:00', 'end': '06:30'}]
        assert focus.is_active(MON_2300, now_ts=1000.0)
        assert not focus.is_active(MON_NOON, now_ts=1000.0)


class TestBreak:
    def test_break_suspends_focus(self, focus):
        focus.set_enabled(True)
        focus.start_break(5, now_ts=1000.0)
        assert focus.on_break(now_ts=1000.0 + 4 * 60)
        assert not focus.is_active(MON_NOON, now_ts=1000.0 + 4 * 60)

    def test_break_auto_resumes(self, focus):
        focus.set_enabled(True)
        focus.start_break(5, now_ts=1000.0)
        assert not focus.on_break(now_ts=1000.0 + 6 * 60)
        assert focus.is_active(MON_NOON, now_ts=1000.0 + 6 * 60)

    def test_turning_off_clears_break(self, focus):
        focus.start_break(15, now_ts=1000.0)
        focus.set_enabled(False)
        assert not focus.on_break(now_ts=1000.0)


class TestPausedApps:
    def test_paused_matching_normalizes_desktop_suffix(self, focus):
        focus.set_enabled(True)
        focus.set_app_focused('org.gnome.Fractal.desktop', True)
        assert focus.is_paused_app('org.gnome.Fractal', now=MON_NOON, now_ts=1000.0)
        assert focus.is_paused_app('org.gnome.Fractal.desktop', now=MON_NOON, now_ts=1000.0)
        assert not focus.is_paused_app('org.gnome.Calculator', now=MON_NOON, now_ts=1000.0)

    def test_not_paused_when_inactive(self, focus):
        focus.set_app_focused('a.desktop', True)
        assert not focus.is_paused_app('a.desktop', now=MON_NOON, now_ts=1000.0)

    def test_unpause_removes_either_form(self, focus):
        focus.set_app_focused('a.desktop', True)
        focus.set_app_focused('a', False)
        assert focus.focus_apps == []

    def test_empty_id_never_paused(self, focus):
        focus.set_enabled(True)
        assert not focus.is_paused_app('', now=MON_NOON, now_ts=1000.0)
        assert not focus.is_paused_app(None, now=MON_NOON, now_ts=1000.0)


class TestHeldNotifications:
    def test_release_preserves_arrival_order(self):
        held = HeldNotifications()
        held.hold(1, 'Chat', 'first', '', 'chat')
        held.hold(2, 'Chat', 'second', '', 'chat')
        held.hold(3, 'Mail', 'third', '', 'mail')
        assert len(held) == 3
        released = held.release_all()
        assert [r[2] for r in released] == ['first', 'second', 'third']
        assert len(held) == 0

    def test_release_empties_queue(self):
        held = HeldNotifications()
        held.hold(1, 'A', 's', '', 'a')
        held.release_all()
        assert held.release_all() == []
