"""Contract tests between toplevel_manager and the real pywayland API.

Regression guard for the WS19 switcher defects:

- ``pywayland.client`` exports ``Display`` (never ``Client``);
- a proxy's ``Dispatcher`` maps event-name STRINGS to callables (the old code
  registered handlers as keys, which raises TypeError inside _connect and was
  swallowed into a permanently-None backend);
- the GLib fd watch is armed once the compositor connection succeeds, so
  events keep flowing after the initial blocking dispatch.

The fakes mirror pywayland 0.4.19 semantics verified against its published
source: handlers are stored per event name on each proxy's dispatcher and
invoked as ``handler(proxy, *event_args)``; unknown event names raise
KeyError; unregistered events are skipped.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

REGISTRY_EVENTS = ('global', 'global_remove')
MANAGER_EVENTS = ('toplevel', 'finished')
HANDLE_EVENTS = (
    'title', 'app_id', 'state', 'output_enter', 'output_leave', 'done', 'closed',
)

TOPLVL_IFACE = 'zwlr_foreign_toplevel_manager_v1'
SEAT_IFACE = 'wl_seat'


class FakeDispatcher:
    """Mimics pywayland's Dispatcher: str event-name keys, callable values."""

    def __init__(self, events: tuple[str, ...]) -> None:
        self._events = events
        self.registrations: dict[str, object] = {}

    def __setitem__(self, key: object, handler: object) -> None:
        if not isinstance(key, str):
            raise TypeError(
                f'dispatcher key must be an event-name string, got {key!r}'
            )
        if key not in self._events:
            raise KeyError(f'unknown event {key!r}')
        if not callable(handler):
            raise TypeError(f'dispatcher value for {key!r} must be callable')
        self.registrations[key] = handler

    def __getitem__(self, key: object) -> object:
        return self.registrations.get(key)  # type: ignore[return-value]


class FakeHandle:
    def __init__(self) -> None:
        self.dispatcher = FakeDispatcher(HANDLE_EVENTS)
        self.activate_calls: list[object] = []
        self.close_calls: list[bool] = []

    def activate(self, seat: object) -> None:
        self.activate_calls.append(seat)

    def close(self) -> None:
        self.close_calls.append(True)


class FakeManager:
    def __init__(self) -> None:
        self.dispatcher = FakeDispatcher(MANAGER_EVENTS)


class FakeSeat:
    pass


class FakeRegistry:
    def __init__(self, advertised: list[tuple[int, int, str, int]]) -> None:
        self.dispatcher = FakeDispatcher(REGISTRY_EVENTS)
        self.binds: list[tuple[int, object, int]] = []
        self.bound: dict[str, object] = {}
        self._advertised = advertised

    def bind(self, name: int, interface_cls: object, version: int) -> object:
        self.binds.append((name, interface_cls, version))
        iface = next(i for _s, n, i, _v in self._advertised if n == name)
        proxy: object
        if iface == TOPLVL_IFACE:
            proxy = FakeManager()
        elif iface == SEAT_IFACE:
            proxy = FakeSeat()
        else:
            proxy = object()
        self.bound[iface] = proxy
        return proxy


class FakeDisplay:
    def __init__(self, advertised: list[tuple[int, int, str, int]]) -> None:
        self.connected = False
        self.flushes = 0
        self.fd = 42
        self.registry: FakeRegistry | None = None
        self._advertised = advertised

    def connect(self) -> None:
        self.connected = True

    def get_registry(self) -> FakeRegistry:
        self.registry = FakeRegistry(self._advertised)
        return self.registry

    def dispatch(self, *, block: bool = False) -> int:
        assert self.registry is not None
        handler = self.registry.dispatcher.registrations.get('global')
        assert callable(handler)
        for serial, name, iface, version in self._advertised:
            handler(self.registry, serial, name, iface, version)
        return len(self._advertised)

    def flush(self) -> None:
        self.flushes += 1

    def get_fd(self) -> int:
        return self.fd


class FakeIOCondition:
    IN = 1
    HUP = 2


class FakeGLib:
    IOCondition = FakeIOCondition
    fd_adds: list[tuple[int, int, object]] = []

    @classmethod
    def unix_fd_add(cls, fd: int, condition: int, callback: object) -> int:
        cls.fd_adds.append((fd, condition, callback))
        return len(cls.fd_adds)


class Harness:
    """Fake pywayland + gi in sys.modules, with a fresh toplevel_manager."""

    def __init__(self, advertised: list[tuple[int, int, str, int]]) -> None:
        self.display = FakeDisplay(advertised)
        self.saved: dict[str, object] = {}

    def __enter__(self) -> Harness:
        self.saved = {
            name: sys.modules[name]
            for name in (
                'pywayland', 'pywayland.client', 'pywayland.protocol',
                'pywayland.protocol.wayland',
                'pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1',
                'gi', 'gi.repository', 'toplevel_manager',
            )
            if name in sys.modules
        }
        for name in self.saved:
            sys.modules.pop(name, None)

        pw = types.ModuleType('pywayland')
        client = types.ModuleType('pywayland.client')
        client.Display = lambda: self.display  # type: ignore[attr-defined]
        proto = types.ModuleType('pywayland.protocol')
        wayland = types.ModuleType('pywayland.protocol.wayland')
        wayland.WlSeat = FakeSeat  # type: ignore[attr-defined]
        wlr = types.ModuleType(
            'pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1'
        )
        wlr.ZwlrForeignToplevelManagerV1 = FakeManager  # type: ignore[attr-defined]
        wlr.ZwlrForeignToplevelHandleV1 = FakeHandle  # type: ignore[attr-defined]
        pw.client = client  # type: ignore[attr-defined]
        pw.protocol = proto  # type: ignore[attr-defined]
        proto.wayland = wayland  # type: ignore[attr-defined]
        proto.wlr_foreign_toplevel_management_unstable_v1 = wlr  # type: ignore[attr-defined]
        for mod in (pw, client, proto, wayland, wlr):
            sys.modules[mod.__name__] = mod

        gi = types.ModuleType('gi')
        gi.require_version = lambda *args, **kwargs: None  # type: ignore[attr-defined]
        repo = types.ModuleType('gi.repository')
        repo.GLib = FakeGLib  # type: ignore[attr-defined]
        gi.repository = repo  # type: ignore[attr-defined]
        sys.modules['gi'] = gi
        sys.modules['gi.repository'] = repo

        self.module = importlib.import_module('toplevel_manager')
        return self

    def __exit__(self, *exc: object) -> None:
        for name in (
            'pywayland', 'pywayland.client', 'pywayland.protocol',
            'pywayland.protocol.wayland',
            'pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1',
            'gi', 'gi.repository', 'toplevel_manager',
        ):
            sys.modules.pop(name, None)
        sys.modules.update(self.saved)


ADVERTISED = [
    # (serial, name, interface, version) in compositor advertise order
    (1, 1, TOPLVL_IFACE, 3),
    (2, 2, SEAT_IFACE, 7),
]


@pytest.fixture
def harness() -> Harness:
    FakeGLib.fd_adds.clear()
    with Harness(list(ADVERTISED)) as h:
        yield h


def _all_registrations(harness: Harness) -> dict[str, object]:
    registry = harness.display.registry
    assert registry is not None
    flat: dict[str, object] = {}
    flat.update(registry.dispatcher.registrations)
    manager = registry.bound[TOPLVL_IFACE]
    flat.update(manager.dispatcher.registrations)  # type: ignore[attr-defined]
    return flat


def test_backend_connects_and_binds_globals(harness: Harness) -> None:
    backend = harness.module.WaylandToplevelBackend()
    registry = harness.display.registry
    assert harness.display.connected
    assert backend.available() is True
    assert registry is not None
    # Manager global arrives before the seat; binding must not depend on order.
    assert [(n, v) for n, _c, v in registry.binds] == [(1, 3), (2, 1)]
    assert registry.bound[SEAT_IFACE] is backend._seat


def test_dispatcher_registrations_use_real_event_names(harness: Harness) -> None:
    harness.module.WaylandToplevelBackend()
    registry = harness.display.registry
    assert registry is not None
    assert set(registry.dispatcher.registrations) == set(REGISTRY_EVENTS)
    manager = registry.bound[TOPLVL_IFACE]
    assert set(manager.dispatcher.registrations) == set(MANAGER_EVENTS)  # type: ignore[attr-defined]
    for key, value in _all_registrations(harness).items():
        assert isinstance(key, str), f'non-string dispatcher key: {key!r}'
        assert callable(value), f'dispatcher value for {key!r} not callable'


def test_handle_events_registered_on_toplevel_event(harness: Harness) -> None:
    backend = harness.module.WaylandToplevelBackend()
    registry = harness.display.registry
    assert registry is not None
    manager = registry.bound[TOPLVL_IFACE]
    handle = FakeHandle()
    manager.dispatcher.registrations['toplevel'](manager, handle)  # type: ignore[index]
    assert set(handle.dispatcher.registrations) == set(HANDLE_EVENTS)
    assert id(handle) in backend._handles


def test_arm_fd_watch_called_on_successful_connect(harness: Harness) -> None:
    harness.module.WaylandToplevelBackend()
    assert len(FakeGLib.fd_adds) == 1
    fd, condition, callback = FakeGLib.fd_adds[0]
    assert fd == harness.display.fd
    assert condition == FakeIOCondition.IN | FakeIOCondition.HUP
    assert callable(callback)


def test_events_flow_after_connect_via_upserts(harness: Harness) -> None:
    backend = harness.module.WaylandToplevelBackend()
    events: list[tuple[object, object, object]] = []
    backend.connect(lambda *args: events.append(args))
    registry = harness.display.registry
    assert registry is not None
    manager = registry.bound[TOPLVL_IFACE]
    handle = FakeHandle()

    manager.dispatcher.registrations['toplevel'](manager, handle)  # type: ignore[index]
    handle.dispatcher.registrations['app_id'](handle, 'org.a.App')  # type: ignore[index]
    handle.dispatcher.registrations['title'](handle, 'Window')  # type: ignore[index]

    assert events[-1] == (id(handle), 'org.a.App', 'Window')

    handle.dispatcher.registrations['closed'](handle)  # type: ignore[index]
    assert events[-1] == (id(handle), None, None)
    assert id(handle) not in backend._handles


def test_activate_passes_bound_seat(harness: Harness) -> None:
    backend = harness.module.WaylandToplevelBackend()
    registry = harness.display.registry
    assert registry is not None
    manager = registry.bound[TOPLVL_IFACE]
    handle = FakeHandle()
    manager.dispatcher.registrations['toplevel'](manager, handle)  # type: ignore[index]

    backend.activate(id(handle))
    assert handle.activate_calls == [registry.bound[SEAT_IFACE]]

    backend.close(id(handle))
    assert handle.close_calls == [True]


def test_graceful_degradation_without_wlr_protocol_module() -> None:
    # pywayland present but the generated wlr protocol classes missing
    # (upstream pywayland does not ship them): import must survive and the
    # default backend factory must degrade to None instead of crashing.
    FakeGLib.fd_adds.clear()
    with Harness(list(ADVERTISED)):
        pw = sys.modules['pywayland']
        wlr_name = 'pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1'
        sys.modules.pop(wlr_name, None)
        delattr(pw.protocol, 'wlr_foreign_toplevel_management_unstable_v1')  # type: ignore[attr-defined]
        sys.modules.pop('toplevel_manager', None)
        mod = importlib.import_module('toplevel_manager')
        assert mod._PYWAYLAND_AVAILABLE is False
        assert mod._make_default_backend() is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
