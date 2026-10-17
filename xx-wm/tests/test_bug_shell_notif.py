"""Lane bug_shell notification findings: 4, 7 and lower-severity L4.

4: a forged replaces_id must not poison the id counter (or hand out
   shell-reserved ids); ids wrap within 1..2^32-1.
7: closing a notification must remove its expiry timer from the main loop.
L4: muted (never closed) notifications must not grow _notif_hints without
   bound, and stored hints must not hoard image-data bytes.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


class FakeVariant:
    def __init__(self, *args):
        self.args = args


class FakeGLib:
    """Controllable GLib: idle/timeout callbacks only run when drained."""

    Error = Exception
    Variant = FakeVariant

    def __init__(self) -> None:
        self.idle_queue: list = []
        self.timeouts: dict = {}
        self.removed: list = []
        self._next_id = 100

    def idle_add(self, callback, *args):
        self.idle_queue.append((callback, args))
        return len(self.idle_queue)

    def run_idle(self) -> None:
        queued, self.idle_queue = self.idle_queue, []
        for callback, args in queued:
            callback(*args)

    def timeout_add(self, ms, callback, *args):
        tid = self._next_id
        self._next_id += 1
        self.timeouts[tid] = (ms, callback, args)
        return tid

    def source_remove(self, tid):
        self.removed.append(tid)
        self.timeouts.pop(tid, None)
        return True


@pytest.fixture(autouse=True)
def _isolate_modules():
    """Leave no imported shell module behind: sibling files import window
    and friends against their own gi seams (test_folders idiom)."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


class FakeParams:
    def __init__(self, values):
        self._values = values

    def unpack(self):
        return self._values


class FakeBus:
    def __init__(self) -> None:
        self.signals: list = []

    def emit_signal(self, *args):
        self.signals.append(args)


@pytest.fixture
def env(monkeypatch):
    import notif_daemon
    glib = FakeGLib()
    monkeypatch.setattr(notif_daemon, 'GLib', glib)
    yield SimpleNamespace(glib=glib, module=notif_daemon)


def _bare_daemon(env, **overrides):
    daemon = object.__new__(env.module.NotificationDaemon)
    daemon._on_notify = lambda *a: None
    daemon._on_close = lambda i: None
    daemon._bus = FakeBus()
    daemon._reg_id = 0
    daemon._name_id = 0
    daemon._next_id = 1
    daemon._owns_name = False
    daemon._expire_timers = {}
    daemon._notif_hints = {}
    daemon._takes_actions = False
    for key, value in overrides.items():
        setattr(daemon, key, value)
    return daemon


def _params(replaces=0, expire=-1, hints=None):
    return FakeParams(('App', replaces, '', 'S', 'B', [], hints or {}, expire))


class TestForgedReplacesId:
    def test_huge_replaces_does_not_poison_counter(self, env) -> None:
        daemon = _bare_daemon(env)
        nid = daemon._handle_notify(_params(replaces=0xFFFFFFFF))
        assert nid != 0xFFFFFFFF
        assert daemon._next_id <= 0xFFFFFFFF
        nid2 = daemon._handle_notify(_params())
        assert nid2 <= 0xFFFFFFFF

    def test_unknown_replaces_allocates_fresh_id(self, env) -> None:
        daemon = _bare_daemon(env)
        first = daemon._handle_notify(_params())
        forged = daemon._handle_notify(_params(replaces=999900))
        assert forged != 999900
        assert forged not in (first, 999900)

    def test_live_replaces_is_honoured(self, env) -> None:
        daemon = _bare_daemon(env)
        nid = daemon._handle_notify(_params())
        again = daemon._handle_notify(_params(replaces=nid))
        assert again == nid

    def test_reserved_ids_never_handed_out(self, env) -> None:
        daemon = _bare_daemon(env)
        seen = {daemon._handle_notify(_params()) for _ in range(20)}
        assert not (seen & {999901, 999902, 999903, 999904, 999905})
        assert all(n < 1_900_000_000 for n in seen)

    def test_counter_wraps_within_u32(self, env) -> None:
        daemon = _bare_daemon(env, _next_id=0xFFFFFFFF)
        nid = daemon._handle_notify(_params())
        assert 1 <= nid <= 0xFFFFFFFF
        nid2 = daemon._handle_notify(_params())
        assert 1 <= nid2 <= 0xFFFFFFFF


class TestCloseRemovesTimer:
    def test_close_removes_expiry_timer(self, env) -> None:
        daemon = _bare_daemon(env)
        nid = daemon._handle_notify(_params(expire=5000))
        assert nid in daemon._expire_timers
        timer = daemon._expire_timers[nid]
        daemon._close_notification(daemon._bus, nid, 2)
        assert nid not in daemon._expire_timers
        assert timer in env.glib.removed

    def test_stale_timer_cannot_close_replacement(self, env) -> None:
        daemon = _bare_daemon(env)
        nid = daemon._handle_notify(_params(expire=5000))
        daemon._close_notification(daemon._bus, nid, 2)
        env.glib.run_idle()
        assert daemon._expire_timers == {}


class TestHintsBounded:
    def test_muted_never_closed_stays_bounded(self, env) -> None:
        daemon = _bare_daemon(env)
        for _ in range(500):
            daemon._handle_notify(_params())
        assert len(daemon._notif_hints) <= 256

    def test_image_data_not_hoarded(self, env) -> None:
        daemon = _bare_daemon(env)
        big = 'x' * 100000
        nid = daemon._handle_notify(
            _params(hints={'image-data': big, 'category': 'im'}))
        stored = daemon._notif_hints[nid]
        assert len(str(stored.get('image-data', ''))) < 1000


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
