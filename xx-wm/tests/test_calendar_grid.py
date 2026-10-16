"""Tests for calendar_grid.py — month grid math."""
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from calendar_grid import WEEKDAY_HEADERS, add_months, month_grid, month_title


class TestMonthGrid:
    def test_weeks_start_monday(self):
        # July 2026 starts on a Wednesday
        grid = month_grid(2026, 7)
        assert grid[0] == [None, None, 1, 2, 3, 4, 5]
        assert len(WEEKDAY_HEADERS) == 7
        assert WEEKDAY_HEADERS[0] == 'Mo'

    def test_all_days_present_once(self):
        grid = month_grid(2026, 7)
        days = [d for week in grid for d in week if d]
        assert days == list(range(1, 32))

    def test_leap_year_february(self):
        days = [d for week in month_grid(2024, 2) for d in week if d]
        assert days[-1] == 29

    def test_non_leap_february(self):
        days = [d for week in month_grid(2026, 2) for d in week if d]
        assert days[-1] == 28

    def test_rows_are_full_weeks(self):
        for year, month in [(2024, 2), (2026, 7), (2025, 12)]:
            for week in month_grid(year, month):
                assert len(week) == 7


class TestAddMonths:
    def test_forward_wrap(self):
        assert add_months(2026, 12, 1) == (2027, 1)

    def test_backward_wrap(self):
        assert add_months(2026, 1, -1) == (2025, 12)

    def test_same_year(self):
        assert add_months(2026, 7, 2) == (2026, 9)

    def test_multi_year_jump(self):
        assert add_months(2026, 7, -19) == (2024, 12)


def test_month_title():
    assert month_title(2026, 7) == 'July 2026'
