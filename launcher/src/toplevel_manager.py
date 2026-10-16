"""pywayland client for wlr-foreign-toplevel-management-unstable-v1.

Lists, activates and closes toplevel windows on a wlroots compositor (phoc).
The pywayland interaction is isolated behind a backend so the manager's pure
logic (app filtering, closed-handle pruning, ordering, dispatch) is testable
without a compositor or pywayland installed. Protocol bindings are generated
at runtime by the installed pywayland's own scanner (see wayland_bindings)
into a version-keyed cache, so one vendored generation cannot break the
switcher on a different pywayland. If pywayland, the bindings or the
compositor connection are unavailable the manager degrades to an empty state
instead of crashing, so the switcher can still show "No open apps".
"""

from __future__ import annotations

import logging
import struct
import threading
from dataclasses import dataclass

import wayland_bindings

log = logging.getLogger(__name__)

STATE_MAXIMIZED = 0
STATE_MINIMIZED = 1
STATE_ACTIVATED = 2
STATE_FULLSCREEN = 3


def decode_state_array(state: object) -> tuple[int, ...]:
    """Decode a foreign-toplevel ``state`` event payload to state ids.

    The protocol declares the argument as an array of uint32, which
    pywayland delivers as bytes in little-endian order. Lists and tuples
    of ints (test fakes, future bindings) are accepted as-is; anything
    else decodes to empty. A trailing partial word is ignored.
    """
    if state is None:
        return ()
    if isinstance(state, (bytes, bytearray, memoryview)):
        data = bytes(state)
        words = len(data) // 4
        if words == 0:
            return ()
        return tuple(struct.unpack('<' + 'I' * words, data[:words * 4]))
    if isinstance(state, (list, tuple)):
        try:
            return tuple(int(item) for item in state)
        except (TypeError, ValueError):
            return ()
    return ()

# The shell's own surfaces must never appear in the switcher, nor the
# on-screen keyboard (it is a regular toplevel while typing).
_OWN_APP_ID = 'io.piercingxx.XXWM'
_FILTERED_SUBSTRINGS = ('squeekboard',)

try:
    from pywayland.client import Display

    _DISPLAY_AVAILABLE = True
except (ImportError, OSError):
    # OSError covers a pywayland install whose native libwayland is missing;
    # ImportError covers missing pywayland entirely.
    _DISPLAY_AVAILABLE = False
    Display = None  # type: ignore[assignment,misc]


def _resolve_import_time_classes() -> tuple[object, object, object]:
    """Best-effort synchronous binding resolution for import time.

    Prefers bindings generated on a previous run (matching the installed
    pywayland by construction), then a hand-installed pywayland.protocol
    module. Never generates and never raises: generation happens in a worker
    thread via DeferredWaylandBackend.
    """
    cached = wayland_bindings.load_cached_bindings()
    if cached is not None:
        return (
            cached['WlSeat'],
            cached['ZwlrForeignToplevelManagerV1'],
            cached['ZwlrForeignToplevelHandleV1'],
        )
    try:
        from pywayland.protocol.wayland import WlSeat
        from pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1 import (
            ZwlrForeignToplevelHandleV1,
            ZwlrForeignToplevelManagerV1,
        )
    except (ImportError, OSError):
        return (None, None, None)
    return (WlSeat, ZwlrForeignToplevelManagerV1, ZwlrForeignToplevelHandleV1)


WlSeat, ZwlrForeignToplevelManagerV1, ZwlrForeignToplevelHandleV1 = (
    _resolve_import_time_classes()
)

_PYWAYLAND_AVAILABLE = (
    _DISPLAY_AVAILABLE
    and WlSeat is not None
    and ZwlrForeignToplevelManagerV1 is not None
    and ZwlrForeignToplevelHandleV1 is not None
)


def _post_to_main(callback: object, *args: object) -> None:
    """Run ``callback`` on the GTK main loop; call it directly when GLib is
    unavailable (headless seams and tests without a running loop)."""
    try:
        from gi.repository import GLib
    except Exception:
        callback(*args)  # type: ignore[operator]
        return
    GLib.idle_add(callback, *args)


@dataclass(frozen=True)
class Toplevel:
    """A foreign toplevel as surfaced to the switcher.

    ``handle`` is the opaque protocol object identity; it is stable for the
    lifetime of the window and is what activate()/close() are addressed by.
    """

    app_id: str
    title: str
    handle: object
    activated: bool = False


def _is_filtered(app_id: str) -> bool:
    if app_id == _OWN_APP_ID:
        return True
    lowered = app_id.casefold()
    return any(marker in lowered for marker in _FILTERED_SUBSTRINGS)


class WaylandToplevelBackend:
    """pywayland client driving wlr-foreign-toplevel-management-unstable-v1.

    Runs on a GLib fd-watch so the GTK main loop is never blocked: the
    compositor's events are dispatched from the idle callback the watch
    schedules. The backend only mutates the manager's registry via the
    ``_upsert``/``_remove`` hooks; it never reads state back.

    ``seat_cls``/``manager_cls`` default to the import-time bindings; passing
    them explicitly (runtime-generated bindings, or fakes in tests) selects
    the protocol classes for this connection.
    """

    def __init__(
        self,
        seat_cls: object | None = None,
        manager_cls: object | None = None,
        display: object | None = None,
    ) -> None:
        if display is None:
            if Display is None:
                raise RuntimeError('pywayland is not available')
            display = Display()
        if seat_cls is None:
            seat_cls = WlSeat
        if manager_cls is None:
            manager_cls = ZwlrForeignToplevelManagerV1
        if seat_cls is None or manager_cls is None:
            raise RuntimeError('wayland protocol bindings are not available')
        self._display = display
        self._seat_cls = seat_cls
        self._manager_cls = manager_cls
        self._registry: object | None = None
        self._manager: object | None = None
        self._seat: object | None = None
        self._handles: dict[object, object] = {}
        self._info: dict[object, list[str]] = {}
        self._manager_cb = None
        self._state_cb = None
        self._fd_source = None
        self._connect()

    def _connect(self) -> None:
        try:
            self._display.connect()
        except Exception as exc:
            raise RuntimeError(f'cannot connect to Wayland display: {exc}') from exc
        # pywayland resolves new_id event args through the live WlRegistry
        # proxy, so it must stay referenced for the connection's lifetime.
        self._registry = self._display.get_registry()
        self._registry.dispatcher['global'] = self._on_registry_global
        self._registry.dispatcher['global_remove'] = self._on_registry_global_remove
        self._display.dispatch(block=True)
        self._display.flush()
        if self._manager is None:
            raise RuntimeError('compositor does not offer wlr-foreign-toplevel-management')
        self._arm_fd_watch()

    # -- GLib fd-watch -----------------------------------------------------

    def _arm_fd_watch(self) -> None:
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import GLib

        fd = self._display.get_fd()
        # PyGObject has no unix_fd_add; newer releases move fd_add_full to
        # GLibUnix and deprecate the GLib alias.
        try:
            from gi.repository import GLibUnix
            fd_add_full = GLibUnix.fd_add_full
        except (ImportError, AttributeError):
            fd_add_full = GLib.unix_fd_add_full
        self._fd_source = fd_add_full(
            GLib.PRIORITY_DEFAULT, fd,
            GLib.IOCondition.IN | GLib.IOCondition.HUP, self._on_fd_ready,
        )

    def _on_fd_ready(self, _fd: int, _cond: int) -> bool:
        from gi.repository import GLib

        if _cond & GLib.IOCondition.HUP:
            # Compositor closed the socket; stop watching instead of spinning
            # on a dead fd forever.
            log.error('wayland compositor connection closed (HUP)')
            return False
        try:
            # The fd is readable, so a reading dispatch returns at once;
            # dispatch(block=False) only drains already-queued events, never
            # reads the socket, and the still-readable fd spins the loop.
            self._display.dispatch(block=True)
            self._display.flush()
        except Exception as exc:  # noqa: BLE001 - compositor went away
            log.error('wayland dispatch failed: %s', exc)
            return False  # dispatch is unrecoverable; stop the fd-watch
        return True  # keep watching

    # -- protocol glue -----------------------------------------------------

    def _on_registry_global(self, registry, name: int, interface: str, version: int) -> None:
        if interface == 'wl_seat':
            self._seat = registry.bind(name, self._seat_cls, 1)
        elif interface == 'zwlr_foreign_toplevel_manager_v1':
            self._manager = registry.bind(name, self._manager_cls, min(3, version))
            self._manager.dispatcher['toplevel'] = self._on_toplevel
            self._manager.dispatcher['finished'] = self._on_finished

    def _on_registry_global_remove(self, _registry, _name: int) -> None:
        pass

    def _on_toplevel(self, manager, toplevel) -> None:
        self._handles[id(toplevel)] = toplevel
        toplevel.dispatcher['title'] = self._on_title
        toplevel.dispatcher['app_id'] = self._on_app_id
        toplevel.dispatcher['state'] = self._on_state
        toplevel.dispatcher['output_enter'] = self._on_output_enter
        toplevel.dispatcher['output_leave'] = self._on_output_leave
        toplevel.dispatcher['done'] = self._on_done
        toplevel.dispatcher['closed'] = self._on_closed
        toplevel.dispatcher['parent'] = self._on_parent
        self._upsert(id(toplevel), '', '')

    def _on_title(self, toplevel, title: str) -> None:
        info = self._info.setdefault(id(toplevel), ['', ''])
        info[1] = title
        self._upsert(id(toplevel), info[0], info[1])

    def _on_app_id(self, toplevel, app_id: str) -> None:
        info = self._info.setdefault(id(toplevel), ['', ''])
        info[0] = app_id
        self._upsert(id(toplevel), info[0], info[1])

    def _on_state(self, toplevel, state) -> None:
        try:
            activated = STATE_ACTIVATED in decode_state_array(state)
        except Exception:  # noqa: BLE001 - a bad state array must not break dispatch
            activated = False
        if self._state_cb is not None:
            self._state_cb(id(toplevel), activated)

    def _on_parent(self, toplevel, _parent) -> None:
        pass

    def _on_output_enter(self, toplevel, _output) -> None:
        pass

    def _on_output_leave(self, toplevel, _output) -> None:
        pass

    def _on_done(self, toplevel) -> None:
        pass

    def _on_closed(self, toplevel) -> None:
        self._remove(id(toplevel))
        self._handles.pop(id(toplevel), None)
        self._info.pop(id(toplevel), None)

    def _on_finished(self, _manager) -> None:
        # Compositor dropped the protocol; clear everything.
        for handle in list(self._handles):
            self._remove(handle)
        self._handles.clear()
        self._info.clear()

    # -- manager hooks -----------------------------------------------------

    def _upsert(self, handle: object, app_id: str, title: str) -> None:
        if self._manager_cb is not None:
            self._manager_cb(handle, app_id, title)

    def _remove(self, handle: object) -> None:
        if self._manager_cb is not None:
            self._manager_cb(handle, None, None)

    # -- public API (backend contract) -------------------------------------

    def available(self) -> bool:
        return self._manager is not None

    def connect(self, callback) -> None:
        self._manager_cb = callback

    def connect_state(self, callback) -> None:
        """Register ``callback(handle, activated)`` for state events."""
        self._state_cb = callback

    def activate(self, handle: object) -> None:
        toplevel = self._handles.get(handle)
        if toplevel is None:
            return
        if self._seat is None:
            log.warning('cannot activate %r: no wl_seat bound', handle)
            return
        toplevel.activate(self._seat)
        self._display.flush()

    def close(self, handle: object) -> None:
        toplevel = self._handles.get(handle)
        if toplevel is not None:
            toplevel.close()
            self._display.flush()


class DeferredWaylandBackend:
    """Backend contract over bindings generated in a worker thread.

    Construction returns immediately: binding generation (a scanner
    subprocess) and the compositor connect run off the GTK main loop in a
    daemon thread. ``available()`` is False until the real backend is up;
    callbacks registered via ``connect``/``on_ready`` are then wired and the
    manager is notified on the main loop. Any failure degrades silently to
    permanently unavailable — the switcher keeps its empty state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._callback = None
        self._state_callback = None
        self._real: WaylandToplevelBackend | None = None
        self._ready_callbacks: list[object] = []
        thread = threading.Thread(
            target=self._prepare, name='xx-wm-wayland-bindings', daemon=True,
        )
        thread.start()

    def available(self) -> bool:
        with self._lock:
            real = self._real
        return real is not None and real.available()

    def connect(self, callback) -> None:
        with self._lock:
            self._callback = callback
            real = self._real
        if real is not None:
            real.connect(callback)

    def connect_state(self, callback) -> None:
        with self._lock:
            self._state_callback = callback
            real = self._real
        if real is not None:
            real.connect_state(callback)

    def on_ready(self, callback) -> None:
        """Invoke ``callback`` on the main loop once the real backend is up."""
        with self._lock:
            if self._real is None:
                self._ready_callbacks.append(callback)
                return
        _post_to_main(callback)

    def activate(self, handle: object) -> None:
        with self._lock:
            real = self._real
        if real is not None and real.available():
            real.activate(handle)

    def close(self, handle: object) -> None:
        with self._lock:
            real = self._real
        if real is not None and real.available():
            real.close(handle)

    def _prepare(self) -> None:
        try:
            bindings = wayland_bindings.ensure_bindings()
        except Exception as exc:  # noqa: BLE001 - degrade on any failure
            log.info('wayland bindings failed: %s', exc)
            return
        if bindings is None:
            return
        try:
            real = WaylandToplevelBackend(
                seat_cls=bindings['WlSeat'],
                manager_cls=bindings['ZwlrForeignToplevelManagerV1'],
            )
        except Exception as exc:  # noqa: BLE001 - graceful absence on any failure
            log.info('failed to start toplevel backend: %s', exc)
            return
        with self._lock:
            self._real = real
            if self._callback is not None:
                real.connect(self._callback)
            if self._state_callback is not None:
                connect_state = getattr(real, 'connect_state', None)
                if callable(connect_state):
                    connect_state(self._state_callback)
            ready = list(self._ready_callbacks)
            self._ready_callbacks.clear()
        for callback in ready:
            _post_to_main(callback)


class ToplevelManager:
    """Tracks foreign toplevels and dispatches activate/close.

    The backend is the only component that touches pywayland. It must expose:

        available() -> bool
        connect(callback) -> None   # callback(handle, app_id, title) on change,
                                    # callback(handle, None, None) on removal
        activate(handle) -> None
        close(handle) -> None

    Backends that report window state additionally expose
    ``connect_state(callback)`` with ``callback(handle, activated)``; the
    manager tolerates backends without it (no activation tracking).

    A backend that is still preparing (DeferredWaylandBackend) additionally
    exposes ``on_ready(callback)``; the manager connects immediately and is
    notified on the main loop once the backend is up, so the switcher
    refreshes without polling.

    The manager keeps the authoritative registry and applies app filtering
    and closed-handle pruning on top of whatever the backend reports.
    """

    def __init__(self, backend: object | None = None) -> None:
        if backend is None:
            backend = _make_default_backend()
        self._backend = backend
        self._registry: dict[object, Toplevel] = {}
        self._callbacks: list[object] = []
        self._activated: dict[object, bool] = {}
        self._state_seq: dict[object, int] = {}
        self._state_tick = 0
        self._activated_callbacks: list[object] = []
        self._current_active: str | None = None
        if backend is None:
            return
        if backend.available():
            backend.connect(self._on_backend_event)
            self._connect_state(backend)
        else:
            on_ready = getattr(backend, 'on_ready', None)
            if callable(on_ready):
                backend.connect(self._on_backend_event)
                self._connect_state(backend)
                on_ready(self._on_bindings_ready)

    def _connect_state(self, backend: object) -> None:
        connect_state = getattr(backend, 'connect_state', None)
        if callable(connect_state):
            connect_state(self._on_backend_state)

    def _on_bindings_ready(self) -> None:
        self._notify()

    @property
    def available(self) -> bool:
        """True when a working protocol backend could be established."""
        return self._backend is not None and self._backend.available()

    def list(self) -> list[Toplevel]:
        """Current toplevels, shell and keyboard surfaces filtered out."""
        return [t for t in self._registry.values() if not _is_filtered(t.app_id)]

    def activate(self, handle: object) -> None:
        if self._backend is not None and self._backend.available():
            self._backend.activate(handle)

    def close(self, handle: object) -> None:
        if self._backend is not None and self._backend.available():
            self._backend.close(handle)

    def on_change(self, callback: object) -> None:
        """Register a zero-arg callable invoked whenever the registry changes."""
        self._callbacks.append(callback)

    def on_activated_changed(self, callback: object) -> None:
        """Register ``callback(app_id | None)`` for foreground changes.

        The argument is the activated app toplevel's app_id, or None when
        no app toplevel is activated (e.g. the shell itself has focus).
        Filtered surfaces (the shell, the keyboard) never report as active.
        """
        self._activated_callbacks.append(callback)

    @property
    def active_app_id(self) -> str | None:
        """Currently activated app toplevel's app_id, or None."""
        return self._current_active

    def _notify(self) -> None:
        for cb in self._callbacks:
            try:
                cb()
            except Exception as exc:  # noqa: BLE001 - one bad callback must not break the registry
                log.error('toplevel change callback failed: %s', exc)

    def _notify_activated(self, app_id: str | None) -> None:
        for cb in self._activated_callbacks:
            try:
                cb(app_id)
            except Exception as exc:  # noqa: BLE001 - one bad callback must not break the registry
                log.error('toplevel activated callback failed: %s', exc)

    def _recompute_active(self) -> None:
        best: str | None = None
        best_seq = -1
        for handle, topo in self._registry.items():
            if not self._activated.get(handle, False):
                continue
            if not topo.app_id or _is_filtered(topo.app_id):
                continue
            seq = self._state_seq.get(handle, -1)
            if best is None or seq >= best_seq:
                best = topo.app_id
                best_seq = seq
        if best != self._current_active:
            self._current_active = best
            self._notify_activated(best)

    def _on_backend_state(self, handle: object, activated: object) -> None:
        self._activated[handle] = bool(activated)
        self._state_tick += 1
        self._state_seq[handle] = self._state_tick
        current = self._registry.get(handle)
        if current is not None:
            self._registry[handle] = Toplevel(
                app_id=current.app_id, title=current.title,
                handle=current.handle, activated=bool(activated),
            )
        self._recompute_active()

    def _on_backend_event(self, handle: object, app_id: object, title: object) -> None:
        if app_id is None:
            self._registry.pop(handle, None)
            self._activated.pop(handle, None)
            self._state_seq.pop(handle, None)
            self._notify()
            self._recompute_active()
            return
        self._registry[handle] = Toplevel(
            app_id=app_id, title=title or '', handle=handle,
            activated=self._activated.get(handle, False),
        )
        self._notify()
        self._recompute_active()


def _make_default_backend() -> object | None:
    """Build the binding backend, or None when pywayland is unavailable.

    Generation and connect run in a worker thread inside
    DeferredWaylandBackend, so this never blocks the GTK main loop.
    """
    if not _DISPLAY_AVAILABLE:
        log.info('pywayland unavailable, toplevel manager disabled')
        return None
    return DeferredWaylandBackend()
