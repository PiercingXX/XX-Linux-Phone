"""
Month-grid math for the shade's inline calendar — pure logic, no GTK.

Weeks start Monday (Mo–Su columns, design.md "Notification shade & quick
settings"). A grid cell is a day number or None for padding.
"""
from __future__ import annotations

import calendar

_CAL = calendar.Calendar(firstweekday=0)

WEEKDAY_HEADERS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']


def month_grid(year: int, month: int) -> list[list[int | None]]:
    """Weeks of the month, Monday-first; padding days are None."""
    return [
        [day if day != 0 else None for day in week]
        for week in _CAL.monthdayscalendar(year, month)
    ]


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month_title(year: int, month: int) -> str:
    return f'{calendar.month_name[month]} {year}'
