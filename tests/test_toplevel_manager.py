"""Tests for toplevel_manager.py - pure logic via a fake protocol backend.

The manager's registry logic (own-app filtering, closed-handle pruning,
ordering, activate/close dispatch) is exercised without pywayland or a
compositor by injecting a fake backend that simulates protocol events.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from toplevel_manager import Toplevel, ToplevelManager


class FakeBackend:
    """Simulates the pywayland backend contract for the manager."""

    def __init__(self, available: bool = True) -> None:
        self._available = available
        self._callback = None
        self.activated: list[object] = []
        self.closed: list[object] = []

    def available(self) -> bool:
        return self._available

    def connect(self, callback) -> None:
        self._callback = callback

    def activate(self, handle: object) -> None:
        self.activated.append(handle)

    def close(self, handle: object) -> None:
        self.closed.append(handle)

    # -- test drivers ------------------------------------------------------

    def add(self, handle: object, app_id: str, title: str) -> None:
        self._callback(handle, app_id, title)

    def update(self, handle: object, app_id: str, title: str) -> None:
        self._callback(handle, app_id, title)

    def remove(self, handle: object) -> None:
        self._callback(handle, None, None)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def manager(backend: FakeBackend) -> ToplevelManager:
    return ToplevelManager(backend=backend)


def test_available_true_with_working_backend(manager: ToplevelManager) -> None:
    assert manager.available is True


def test_available_false_when_backend_unavailable() -> None:
    mgr = ToplevelManager(backend=FakeBackend(available=False))
    assert mgr.available is False


def test_list_empty_by_default(manager: ToplevelManager) -> None:
    assert manager.list() == []


def test_list_returns_insertion_order(backend: FakeBackend, manager: ToplevelManager) -> None:
    backend.add('h1', 'org.a.App', 'A')
    backend.add('h2', 'org.b.App', 'B')
    backend.add('h3', 'org.c.App', 'C')
    assert [t.handle for t in manager.list()] == ['h1', 'h2', 'h3']


def test_own_app_surfaces_filtered(backend: FakeBackend, manager: ToplevelManager) -> None:
    backend.add('h1', 'org.a.App', 'A')
    backend.add('h2', 'io.piercingxx.XXWM', 'Shell')
    backend.add('h3', 'org.b.App', 'B')
    result = manager.list()
    assert [t.handle for t in result] == ['h1', 'h3']


def test_own_app_only(backend: FakeBackend, manager: ToplevelManager) -> None:
    backend.add('h1', 'io.piercingxx.XXWM', 'Shell')
    assert manager.list() == []


def test_title_update_preserves_handle(backend: FakeBackend, manager: ToplevelManager) -> None:
    backend.add('h1', 'org.a.App', 'Old')
    backend.update('h1', 'org.a.App', 'New')
    result = manager.list()
    assert len(result) == 1
    assert result[0].title == 'New'
    assert result[0].handle == 'h1'


def test_closed_handle_pruned(backend: FakeBackend, manager: ToplevelManager) -> None:
    backend.add('h1', 'org.a.App', 'A')
    backend.add('h2', 'org.b.App', 'B')
    backend.remove('h1')
    result = manager.list()
    assert [t.handle for t in result] == ['h2']


def test_removing_unknown_handle_is_noop(backend: FakeBackend, manager: ToplevelManager) -> None:
    backend.add('h1', 'org.a.App', 'A')
    backend.remove('h2')
    assert [t.handle for t in manager.list()] == ['h1']


def test_activate_dispatches_to_backend(backend: FakeBackend, manager: ToplevelManager) -> None:
    backend.add('h1', 'org.a.App', 'A')
    manager.activate('h1')
    assert backend.activated == ['h1']


def test_close_dispatches_to_backend(backend: FakeBackend, manager: ToplevelManager) -> None:
    backend.add('h1', 'org.a.App', 'A')
    manager.close('h1')
    assert backend.closed == ['h1']


def test_activate_close_noop_when_backend_unavailable() -> None:
    backend = FakeBackend(available=False)
    mgr = ToplevelManager(backend=backend)
    mgr.activate('h1')
    mgr.close('h1')
    assert backend.activated == []
    assert backend.closed == []


def test_on_change_called_on_add(backend: FakeBackend, manager: ToplevelManager) -> None:
    calls: list[list[Toplevel]] = []
    manager.on_change(lambda: calls.append(manager.list()))
    backend.add('h1', 'org.a.App', 'A')
    assert len(calls) == 1
    assert [t.handle for t in calls[0]] == ['h1']


def test_on_change_called_on_remove(backend: FakeBackend, manager: ToplevelManager) -> None:
    backend.add('h1', 'org.a.App', 'A')
    calls: list[list[Toplevel]] = []
    manager.on_change(lambda: calls.append(manager.list()))
    backend.remove('h1')
    assert len(calls) == 1
    assert calls[0] == []


def test_bad_callback_does_not_break_registry(backend: FakeBackend, manager: ToplevelManager) -> None:
    def boom() -> None:
        raise RuntimeError('boom')

    manager.on_change(boom)
    backend.add('h1', 'org.a.App', 'A')  # must not raise
    assert [t.handle for t in manager.list()] == ['h1']


def test_resync_clears_registry_and_accepts_new_backend() -> None:
    first = FakeBackend()
    manager = ToplevelManager(backend=first)
    first.add('h1', 'org.a.App', 'A')
    assert [t.handle for t in manager.list()] == ['h1']
    second = FakeBackend()
    seen: list[list] = []
    manager.on_change(lambda: seen.append(manager.list()))
    manager.resync(backend=second)
    assert manager.list() == []
    assert seen[-1] == []
    second.add('h2', 'org.gnome.Calculator', 'Calculator')
    assert [t.handle for t in manager.list()] == ['h2']
    assert [t.title for t in seen[-1]] == ['Calculator']


def test_toplevel_dataclass_fields() -> None:
    t = Toplevel(app_id='org.a.App', title='A', handle='h1')
    assert t.app_id == 'org.a.App'
    assert t.title == 'A'
    assert t.handle == 'h1'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])