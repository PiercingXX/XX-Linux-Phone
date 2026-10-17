"""The shell posts no missed-call cards: the dialer owns them all.

The dialer policy service is D-Bus activatable, so NoteCall wakes it
when it is not running — the shell-side fallback this file once
covered could only fire when a fake consult reported a silence verdict
without reaching the service, which the real consult never does (a
silence verdict always arrives after the reached report). One owner
posts missed cards, so a call can never card twice.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import call_screen  # noqa: E402
from call_wiring import CallWiring  # noqa: E402

NUMBER = "+14155550100"
STARTED = 1_700_000_000_000


class _Win:
    def __init__(self):
        self.config = None
        self._screen_pending = {"caller": "", "number": NUMBER,
                                "started_ms": STARTED,
                                "verdict": None, "reason": "",
                                "answered": False}
        self._screen_tracked = {}
        self._screen_done = set()
        self._call_ui = None
        self._call_bar = None
        self.notes = []
        self.notifs = []

    def get_application(self):
        return None

    def _ensure_shade(self):
        notifs = self.notifs

        class _Shade:
            def add_notification(self, *args):
                notifs.append(args)

        return _Shade()


def _wiring(win, monkeypatch):
    monkeypatch.setattr(call_screen, 'note_call',
                        lambda *a, **_k: win.notes.append(a))
    return CallWiring(win)


def test_no_shell_card_method_remains():
    assert not hasattr(CallWiring, '_post_silenced_missed')


def test_report_end_only_reports_the_call(monkeypatch):
    win = _Win()
    _wiring(win, monkeypatch)._report_call_end(
        NUMBER, 'incoming', STARTED, False)
    assert win.notes and win.notes[0][1] == 'incoming'
    assert win.notes[0][4] == 'missed'
    assert win.notifs == []


def test_answered_end_only_reports_the_call(monkeypatch):
    win = _Win()
    win._screen_pending['answered'] = True
    _wiring(win, monkeypatch)._report_call_end(
        NUMBER, 'incoming', STARTED, True)
    assert win.notes and win.notes[0][4] == 'answered'
    assert win.notifs == []


def test_call_ended_hook_only_reports_the_call(monkeypatch):
    win = _Win()
    wiring = _wiring(win, monkeypatch)
    wiring._on_call_ended()
    assert win.notes and win.notes[0][4] == 'missed'
    assert win.notifs == []
    assert win._screen_pending is None
