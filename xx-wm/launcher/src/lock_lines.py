"""Pure lock-screen notification-line formatting (GTK-free, unit-testable headlessly).

Kept in its own module so the lock screen's formatting logic can be tested
without PyGObject: lock_screen.py imports gi at module top for the GTK window,
which would otherwise force every pure-logic test to skip on a headless host.
"""

from __future__ import annotations

_FAIL_THRESHOLD = 5     # wrong attempts before first lockout


def lock_screen_lines(notifications: list[tuple[str, str]], mode: str,
                      dnd_active: bool) -> list[str]:
    """Lines shown on the lock surface (design.md "Lock screen"): app name +
    summary only, `count` collapses to one line, hidden entirely while DnD
    is active or the mode is off."""
    if dnd_active or mode == 'off' or not notifications:
        return []
    if mode == 'count':
        n = len(notifications)
        return [f'{n} notification{"s" if n != 1 else ""}']
    return [
        ' — '.join(part for part in (app.strip(), summary.strip()) if part)
        for app, summary in notifications
        if app.strip() or summary.strip()
    ]


def _lockout_secs(fail_count: int) -> int:
    """Seconds to lock the keypad after fail_count total wrong attempts."""
    if fail_count < _FAIL_THRESHOLD:
        return 0
    # 30s per attempt beyond the threshold, capped at 5 min
    return min((fail_count - _FAIL_THRESHOLD + 1) * 30, 300)