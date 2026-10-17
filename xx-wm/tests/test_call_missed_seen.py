"""Shell missed-call card: formatted number, tap target (lane missed_seen)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'common'))

import xx_phone  # noqa: E402
from call_wiring import CallWiring  # noqa: E402

NUMBER = "+14155550100"


class _App:
    def __init__(self):
        self.seen = []

    def _on_notification(self, notif_id, app_name, summary, body,
                         desktop_entry, hints, actions=None):
        self.seen.append({"id": notif_id, "summary": summary,
                          "body": body, "entry": desktop_entry,
                          "hints": hints})


class _Shade:
    def __init__(self):
        self.seen = []

    def add_notification(self, notif_id, app_name, summary, body,
                         desktop_entry="", actions=None, hints=None):
        self.seen.append({"id": notif_id, "summary": summary,
                          "body": body, "entry": desktop_entry})


class _Win:
    def __init__(self, app=None):
        self.config = None
        self._screen_pending = {"caller": "", "number": NUMBER}
        self._call_notif_seq = 0
        self._app = app
        self.shade = _Shade()

    def get_application(self):
        return self._app

    def _ensure_shade(self):
        return self.shade


def _expected():
    return xx_phone.display(
        NUMBER, xx_phone.user_country("", "", os.environ))


def test_card_text_uses_shared_display():
    app = _App()
    CallWiring(_Win(app))._post_silenced_missed(NUMBER, "unknown caller")
    (card,) = app.seen
    assert _expected() in card["summary"]
    assert card["body"].startswith("Silenced: ")


def test_card_tap_opens_dialer_recents():
    app = _App()
    CallWiring(_Win(app))._post_silenced_missed(NUMBER, "unknown caller")
    (card,) = app.seen
    assert card["entry"] == "io.piercingxx.XXDialer"


def test_shade_fallback_carries_number_and_entry():
    CallWiring(_Win(None))._post_silenced_missed(NUMBER, "unknown caller")
    win = _Win(None)
    CallWiring(win)._post_silenced_missed(NUMBER, "unknown caller")
    assert _expected() in win.shade.seen[-1]["summary"]
    assert win.shade.seen[-1]["entry"] == "io.piercingxx.XXDialer"


def test_withheld_number_never_crashes():
    app = _App()
    CallWiring(_Win(app))._post_silenced_missed("", "hidden caller")
    (card,) = app.seen
    assert card["summary"] == "Unknown call"
