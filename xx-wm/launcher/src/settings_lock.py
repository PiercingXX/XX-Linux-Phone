"""Lock settings: change PIN and change the device password.

Change PIN verifies the current PIN through config (which owns the
hash), respects the escalating lockout, and writes the new PIN twice
confirmation through config.set_pin. PIN digits are never logged —
this module logs nothing at all. Change device password opens `passwd`
in a terminal, the same terminal-table path as system updates.
"""
from __future__ import annotations

import time

import settings_page

_MAX_PIN = 64


def _lockout_until(config) -> float:
    try:
        _fails, until = config.get_lockout_state()
        return max(0.0, float(until))
    except Exception:
        return 0.0


def _note_wrong_pin(config, fail_count: int) -> None:
    try:
        from lock_lines import _lockout_secs
        secs = _lockout_secs(fail_count)
    except Exception:
        secs = 0
    try:
        if secs > 0:
            config.set_lockout_state(fail_count, time.time() + secs)
        else:
            config.set_lockout_state(fail_count, 0.0)
    except Exception:
        pass


def validate_new_pin(current: str, new: str, confirm: str) -> str | None:
    """Error string for a PIN change, None when the new PIN is usable."""
    if not new:
        return 'Enter a new PIN.'
    if new != confirm:
        return 'New PINs do not match.'
    if len(new) > _MAX_PIN:
        return 'New PIN is too long.'
    if new == current:
        return 'The new PIN must differ from the current one.'
    return None


def change_pin(config, current: str, new: str,
               confirm: str) -> tuple[bool, str]:
    """Verify `current`, then store `new` via config.set_pin.

    A live lockout refuses the change; a wrong current PIN escalates
    the lockout like the lock screen does; success clears it. Never
    logs — PIN digits must not reach any log.
    """
    if _lockout_until(config) > time.time():
        return False, 'Locked out — try again when the timer ends.'
    try:
        correct = config.verify_pin(current)
    except Exception:
        return False, 'Could not verify the current PIN.'
    if not correct:
        try:
            fails, _until = config.get_lockout_state()
        except Exception:
            fails = 0
        _note_wrong_pin(config, fails + 1)
        return False, 'Current PIN is wrong.'
    error = validate_new_pin(current, new, confirm)
    if error is not None:
        return False, error
    try:
        config.set_pin(new)
    except Exception:
        return False, 'Could not save the new PIN.'
    try:
        config.clear_lockout_state()
    except Exception:
        pass
    return True, 'PIN changed.'


def change_device_password() -> tuple[bool, str]:
    """Open `passwd` in a terminal (same terminal table as updates)."""
    from update_checker import run_terminal_command
    return run_terminal_command('passwd')


class LockSection(settings_page._Section):
    """Change PIN plus change device password."""

    key = 'lock'
    title = 'Lock'

    def __init__(self) -> None:
        self._status = None
        self._current_entry = None
        self._new_entry = None
        self._confirm_entry = None

    def build(self, win) -> list:
        from xx_ui import widgets
        from settings_page import (_section_placeholder, _section_title,
                                   _settings_entry, _settings_row)
        self._status = _section_placeholder(
            'Change the lock-screen PIN or the device password.')
        self._current_entry = _settings_entry(win, 'Current PIN',
                                              password=True)
        self._new_entry = _settings_entry(win, 'New PIN', password=True)
        self._confirm_entry = _settings_entry(win, 'New PIN again',
                                              password=True)
        return [_section_title(self.title),
                _settings_row('Current PIN', self._current_entry),
                _settings_row('New PIN', self._new_entry),
                _settings_row('Confirm new PIN', self._confirm_entry),
                widgets.primary_button(
                    'Change PIN', lambda: self._on_change_pin(win)),
                widgets.outline_button(
                    'Change device password…',
                    lambda: self._on_change_password(win)),
                self._status]

    def refresh(self, win) -> None:
        return None

    def _on_change_pin(self, win) -> None:
        try:
            current = self._current_entry.get_text()
            new = self._new_entry.get_text()
            confirm = self._confirm_entry.get_text()
        except Exception:
            self._say('Could not read the PIN fields.')
            return
        ok, message = change_pin(win.config, current, new, confirm)
        for entry in (self._current_entry, self._new_entry,
                      self._confirm_entry):
            try:
                entry.set_text('')
            except Exception:
                pass
        self._say(message)
        if ok:
            try:
                win._show_status('PIN changed.')
            except Exception:
                pass

    def _on_change_password(self, win) -> None:
        ok, detail = change_device_password()
        self._say('Password prompt opened in terminal.' if ok else detail)
        if ok:
            try:
                win._show_status('Password prompt opened in terminal.')
            except Exception:
                pass

    def _say(self, text: str) -> None:
        try:
            if self._status is not None:
                self._status.set_text(text)
        except Exception:
            pass
