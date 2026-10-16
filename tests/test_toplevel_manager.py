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


def test_toplevel_dataclass_fields() -> None:
    t = Toplevel(app_id='org.a.App', title='A', handle='h1')
    assert t.app_id == 'org.a.App'
    assert t.title == 'A'
    assert t.handle == 'h1'


def test_squeekboard_surface_filtered(backend: FakeBackend, manager: ToplevelManager) -> None:
    backend.add('h1', 'org.a.App', 'A')
    backend.add('h2', 'squeekboard', 'Keyboard')
    backend.add('h3', 'org.b.App', 'B')
    assert [t.handle for t in manager.list()] == ['h1', 'h3']


def test_squeekboard_match_is_case_insensitive(
    backend: FakeBackend, manager: ToplevelManager,
) -> None:
    backend.add('h1', 'sm.puri.Squeekboard', 'Keyboard')
    assert manager.list() == []


class StubDeferredBackend:
    """Test double for DeferredWaylandBackend with a manual ready trigger."""

    def __init__(self) -> None:
        self._callback = None
        self._ready: list[object] = []
        self._available = False
        self.activated: list[object] = []
        self.closed: list[object] = []

    def available(self) -> bool:
        return self._available

    def connect(self, callback) -> None:
        self._callback = callback

    def on_ready(self, callback) -> None:
        self._ready.append(callback)

    def activate(self, handle: object) -> None:
        self.activated.append(handle)

    def close(self, handle: object) -> None:
        self.closed.append(handle)

    def emit(self, handle: object, app_id: str, title: str) -> None:
        self._callback(handle, app_id, title)

    def fire_ready(self) -> None:
        self._available = True
        for callback in self._ready:
            callback()


def test_manager_starts_unavailable_with_deferred_backend() -> None:
    mgr = ToplevelManager(backend=StubDeferredBackend())
    assert mgr.available is False
    assert mgr.list() == []


def test_manager_connects_and_notifies_when_deferred_backend_readies() -> None:
    stub = StubDeferredBackend()
    mgr = ToplevelManager(backend=stub)
    calls: list[list[Toplevel]] = []
    mgr.on_change(lambda: calls.append(mgr.list()))
    stub.emit('h1', 'org.a.App', 'A')
    assert [t.handle for t in mgr.list()] == ['h1']
    stub.fire_ready()
    assert mgr.available is True
    assert len(calls) == 2
    assert [t.handle for t in calls[-1]] == ['h1']


def test_manager_ignores_backends_without_ready_hook() -> None:
    mgr = ToplevelManager(backend=FakeBackend(available=False))
    assert mgr.available is False
    assert mgr.list() == []


def test_real_deferred_backend_reports_unavailable_until_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    import toplevel_manager
    import wayland_bindings

    monkeypatch.setattr(toplevel_manager, '_post_to_main',
                        lambda callback, *args: callback(*args))
    gate = threading.Event()

    def _blocked_ensure():
        assert gate.wait(timeout=10)
        return None

    monkeypatch.setattr(wayland_bindings, 'ensure_bindings', _blocked_ensure)
    mgr = ToplevelManager(backend=toplevel_manager.DeferredWaylandBackend())
    assert mgr.available is False
    gate.set()


def test_real_deferred_backend_wires_and_notifies_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time

    import toplevel_manager
    import wayland_bindings

    monkeypatch.setattr(toplevel_manager, '_post_to_main',
                        lambda callback, *args: callback(*args))
    release = threading.Event()

    def _gated_ensure():
        assert release.wait(timeout=10)
        return {'WlSeat': object,
                'ZwlrForeignToplevelManagerV1': object,
                'ZwlrForeignToplevelHandleV1': object}

    monkeypatch.setattr(wayland_bindings, 'ensure_bindings', _gated_ensure)
    activated: list[object] = []

    class FakeRealBackend:
        def __init__(self, **kwargs) -> None:
            self._callback = None

        def available(self) -> bool:
            return True

        def connect(self, callback) -> None:
            self._callback = callback

        def activate(self, handle: object) -> None:
            activated.append(handle)

        def close(self, handle: object) -> None:
            pass

    monkeypatch.setattr(toplevel_manager, 'WaylandToplevelBackend', FakeRealBackend)
    backend = toplevel_manager.DeferredWaylandBackend()
    mgr = ToplevelManager(backend=backend)
    assert mgr.available is False
    fired: list[bool] = []
    mgr.on_change(lambda: fired.append(mgr.available))
    release.set()
    deadline = time.time() + 10
    while not mgr.available and time.time() < deadline:
        time.sleep(0.01)
    assert mgr.available is True
    assert fired and fired[-1] is True
    mgr.activate('h1')
    assert activated == ['h1']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

def test_registry_global_handler_matches_wl_registry_global_event():
    # pywayland dispatches wl_registry.global as (proxy, name, interface,
    # version); an extra parameter makes every global raise TypeError and
    # the manager is never bound (seen on a real phoc session).
    import inspect
    import toplevel_manager
    params = list(inspect.signature(
        toplevel_manager.WaylandToplevelBackend._on_registry_global).parameters)
    assert params == ['self', 'registry', 'name', 'interface', 'version']
