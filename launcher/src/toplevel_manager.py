"""pywayland client for wlr-foreign-toplevel-management-unstable-v1.

Lists, activates and closes toplevel windows on a wlroots compositor (phoc).
The pywayland interaction is isolated behind a backend so the manager's pure
logic (own-app filtering, closed-handle pruning, ordering, dispatch) is
testable without a compositor or pywayland installed. If pywayland, the
generated protocol classes or the compositor connection are unavailable the
manager degrades to an empty state instead of crashing, so the switcher can
still show "No open apps".
"""

from __future__ import annotations

import logging
import os
import queue
import select
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

# The shell's own surfaces must never appear in the switcher.
_OWN_APP_ID = 'io.piercingxx.XXWM'
_STATE_ACTIVATED = 2

# Bind v1 only. phoc 0.56 still sends handle event 7 (`parent`, protocol v2+);
# the vendored scanner output only knows events 0–6, and pywayland then
# raises "has no event 7" and drops the rest of the burst — empty recents.
_FOREIGN_TOPLEVEL_BIND_VERSION = 1
_PUMP_INTERVAL_MS = 50

try:
    from pywayland.client import Display

    try:
        # Prefer the protocol classes vendored in this repo (upstream
        # pywayland ships no wlr-foreign-toplevel-management module).
        from wayland_proto.wayland import WlSeat
        from wayland_proto.wlr_foreign_toplevel_management_unstable_v1 import (
            ZwlrForeignToplevelHandleV1,
            ZwlrForeignToplevelManagerV1,
        )
    except (ImportError, TypeError):
        # Fall back to an environment where the generated wlr module was
        # installed into pywayland.protocol itself (e.g. hand-run scanner).
        # TypeError: pywayland 0.4.18 Global is not subscriptable; the
        # wayland_proto package patches that on import, but if the patch
        # did not run this must not take the shell down.
        from pywayland.protocol.wayland import WlSeat
        from pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1 import (
            ZwlrForeignToplevelHandleV1,
            ZwlrForeignToplevelManagerV1,
        )

    _PYWAYLAND_AVAILABLE = True
except (ImportError, OSError, TypeError):
    # OSError covers a pywayland install whose native libwayland is missing;
    # ImportError covers missing pywayland entirely and installs without the
    # generated wlr protocol module in either location. TypeError is the
    # Python 3.14 Global[Iface] failure if both import sources reject it.
    _PYWAYLAND_AVAILABLE = False
    Display = None  # type: ignore[assignment,misc]
    WlSeat = None  # type: ignore[assignment,misc]
    ZwlrForeignToplevelHandleV1 = None  # type: ignore[assignment,misc]
    ZwlrForeignToplevelManagerV1 = None  # type: ignore[assignment,misc]


@dataclass(frozen=True)
class Toplevel:
    """A foreign toplevel as surfaced to the switcher.

    ``handle`` is the opaque protocol object identity; it is stable for the
    lifetime of the window and is what activate()/close() are addressed by.
    """

    app_id: str
    title: str
    handle: object


class WaylandToplevelBackend:
    """pywayland client driving wlr-foreign-toplevel-management-unstable-v1.

    A dedicated thread owns this Display so GTK's libwayland connection is
    not starved. The wakeup pipe lets activate/close run without waiting
    for a compositor event. FakeDisplay tests stay on the GLib pump.
    """

    def __init__(self) -> None:
        if Display is None:
            raise RuntimeError('pywayland is not available')
        self._display = Display()
        self._manager: object | None = None
        self._seat: object | None = None
        self._handles: dict[object, object] = {}
        self._info: dict[object, list[str]] = {}
        self._manager_cb = None
        self._focus_cb = None
        self._fd_source = None
        self._thread: threading.Thread | None = None
        self._wl_registry: object | None = None
        self._closing = False
        self._requests: queue.Queue = queue.Queue()
        self._wakeup_r: int | None = None
        self._wakeup_w: int | None = None
        self._connect()

    def _connect(self) -> None:
        try:
            self._display.connect()
        except Exception as exc:
            raise RuntimeError(f'cannot connect to Wayland display: {exc}') from exc
        registry = self._display.get_registry()
        # Keep the proxy: if it is GC'd the dispatcher dies and globals
        # (and later the bound manager) stop being delivered.
        self._wl_registry = registry
        registry.dispatcher['global'] = self._on_registry_global
        registry.dispatcher['global_remove'] = self._on_registry_global_remove
        # Do not start the wayland thread here: the initial registry burst
        # would upsert into a still-None manager callback and recents stays
        # empty. connect() starts it after the callback is bound.
        if self._is_real_display():
            self._wakeup_r, self._wakeup_w = os.pipe()
            os.set_blocking(self._wakeup_r, False)
            self._flush_display()
        else:
            self._arm_pump()
            self._pump()

    def _is_real_display(self) -> bool:
        cls = type(self._display)
        return cls.__name__ == 'Display' and 'pywayland' in (cls.__module__ or '')

    def _wayland_fd(self) -> int | None:
        get_fd = getattr(self._display, 'get_fd', None)
        if not callable(get_fd):
            return None
        try:
            fd = get_fd()
        except Exception:
            return None
        return fd if isinstance(fd, int) and fd >= 0 else None

    def _flush_display(self) -> None:
        try:
            self._display.flush()
        except Exception:
            pass

    def _dispatch_error_is_fatal(self, exc: BaseException) -> bool:
        if self._closing:
            return True
        msg = str(exc)
        if 'destroyed' in msg.lower():
            return True
        if '11' in msg or 'EAGAIN' in msg.upper():
            return False
        if 'has no event' in msg:
            log.warning('wayland event skipped: %s', exc)
            return False
        log.error('wayland thread dispatch failed: %s', exc)
        return True

    def _dispatch_pending(self) -> bool:
        try:
            self._display.dispatch(block=False)
        except Exception as exc:  # noqa: BLE001
            return not self._dispatch_error_is_fatal(exc)
        return True

    def _read_and_dispatch(self) -> bool:
        # pywayland dispatch(block=False) is wl_display_dispatch_pending and
        # does not read the display fd. Calling only that after select left
        # recents at "No open apps" (available=False). Display.read() is
        # prepare_read + read_events; then pending dispatch delivers the
        # registry/toplevel events. No Display.read → blocking dispatch.
        reader = getattr(self._display, 'read', None)
        try:
            if callable(reader):
                reader()
            else:
                self._display.dispatch(block=True)
        except Exception as exc:  # noqa: BLE001
            if self._dispatch_error_is_fatal(exc):
                return False
            return True
        return self._dispatch_pending()

    def _thread_loop(self) -> None:
        self._flush_display()
        wl_fd = self._wayland_fd()
        while not self._closing:
            self._drain_requests()
            self._flush_display()
            if not self._dispatch_pending():
                return
            fds = []
            if wl_fd is not None:
                fds.append(wl_fd)
            if self._wakeup_r is not None:
                fds.append(self._wakeup_r)
            if not fds:
                if not self._read_and_dispatch():
                    return
                continue
            try:
                ready, _, _ = select.select(fds, [], [], 0.25)
            except Exception as exc:  # noqa: BLE001
                log.error('wayland thread select failed: %s', exc)
                return
            if self._wakeup_r is not None and self._wakeup_r in ready:
                try:
                    os.read(self._wakeup_r, 64)
                except BlockingIOError:
                    pass
                self._drain_requests()
                self._flush_display()
            if wl_fd is not None and wl_fd in ready:
                if not self._read_and_dispatch():
                    return
                self._flush_display()

    def _arm_pump(self) -> None:
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import GLib

        self._fd_source = GLib.timeout_add(_PUMP_INTERVAL_MS, self._pump)

    def _pump(self) -> bool:
        # Flush first: dispatch(block=False) raises EAGAIN on an empty
        # queue, and a combined try would skip flush — the get_registry
        # / bind requests never leave the client (empty recents).
        try:
            self._display.flush()
        except Exception:
            pass
        try:
            self._display.dispatch(block=False)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if '11' in msg or 'EAGAIN' in msg.upper():
                pass
            elif 'has no event' in msg:
                log.warning('wayland event skipped: %s', exc)
            else:
                log.error('wayland dispatch failed: %s', exc)
                return False
        try:
            self._display.flush()
        except Exception:
            pass
        return True

    # -- protocol glue -----------------------------------------------------

    def _on_registry_global(self, registry, *args) -> None:
        # pywayland 0.4.18 dispatcher: (name, interface, version).
        # The contract-test fake (and some older bindings) also pass a
        # leading serial. Accept both; a TypeError here used to make
        # dispatch(block=True) miss every global, including wlr-foreign-toplevel.
        if len(args) == 4:
            _serial, name, interface, version = args
        elif len(args) == 3:
            name, interface, version = args
        else:
            return
        if interface == 'wl_seat':
            self._seat = registry.bind(name, WlSeat, 1)
        elif interface == 'zwlr_foreign_toplevel_manager_v1':
            self._manager = registry.bind(
                name, ZwlrForeignToplevelManagerV1, _FOREIGN_TOPLEVEL_BIND_VERSION)
            self._manager.dispatcher['toplevel'] = self._on_toplevel
            self._manager.dispatcher['finished'] = self._on_finished

    def _on_registry_global_remove(self, _registry, _name: int) -> None:
        pass

    def _on_toplevel(self, manager, toplevel) -> None:
        log.info('foreign toplevel created %s', id(toplevel))
        self._handles[id(toplevel)] = toplevel
        toplevel.dispatcher['title'] = self._on_title
        toplevel.dispatcher['app_id'] = self._on_app_id
        toplevel.dispatcher['state'] = self._on_state
        toplevel.dispatcher['output_enter'] = self._on_output_enter
        toplevel.dispatcher['output_leave'] = self._on_output_leave
        toplevel.dispatcher['done'] = self._on_done
        toplevel.dispatcher['closed'] = self._on_closed
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
        activated = _STATE_ACTIVATED in _parse_states(state)
        cb = getattr(self, '_focus_cb', None)
        if cb is not None:
            cb(id(toplevel), activated)

    def _on_output_enter(self, toplevel, _output) -> None:
        pass

    def _on_output_leave(self, toplevel, _output) -> None:
        pass

    def _on_done(self, toplevel) -> None:
        pass

    def _on_closed(self, toplevel) -> None:
        log.info('foreign toplevel closed %s', id(toplevel))
        self._remove(id(toplevel))
        self._handles.pop(id(toplevel), None)
        self._info.pop(id(toplevel), None)

    def _on_finished(self, _manager) -> None:
        # Compositor dropped the protocol; clear everything.
        log.info('foreign-toplevel manager finished')
        self._manager = None
        for handle in list(self._handles):
            self._remove(handle)
        self._handles.clear()
        self._info.clear()

    # -- manager hooks -----------------------------------------------------

    def _call_manager_cb(self, handle: object, app_id: object, title: object) -> None:
        cb = self._manager_cb
        if cb is None:
            return
        # Invoke directly: ToplevelManager is thread-safe and marshals
        # GTK refresh itself. idle_add here dropped the initial burst
        # when the GTK source did not run before the first show.
        cb(handle, app_id, title)

    def _upsert(self, handle: object, app_id: str, title: str) -> None:
        self._call_manager_cb(handle, app_id, title)

    def _remove(self, handle: object) -> None:
        self._call_manager_cb(handle, None, None)

    # -- public API (backend contract) -------------------------------------

    def available(self) -> bool:
        return self._manager is not None

    def connect(self, callback) -> None:
        self._manager_cb = callback
        self._replay_handles()
        # Start the wayland thread only after the callback is bound so the
        # initial registry/toplevel burst is not upserted into None.
        if self._is_real_display() and self._thread is None:
            self._thread = threading.Thread(
                target=self._thread_loop, daemon=True, name='xx-wm-toplevel')
            self._thread.start()

    def connect_focus(self, callback) -> None:
        self._focus_cb = callback

    def _replay_handles(self) -> None:
        for handle in list(self._handles):
            info = self._info.get(handle, ['', ''])
            self._call_manager_cb(handle, info[0], info[1])

    def _drain_requests(self) -> None:
        while True:
            try:
                op, handle = self._requests.get_nowait()
            except queue.Empty:
                return
            self._apply_request(op, handle)

    def _apply_request(self, op: str, handle: object) -> None:
        toplevel = self._handles.get(handle)
        if toplevel is None:
            log.warning('toplevel %s %r: handle gone', op, handle)
            return
        try:
            if op == 'activate':
                if self._seat is None:
                    log.warning('cannot activate %r: no wl_seat bound', handle)
                    return
                toplevel.activate(self._seat)
            elif op == 'close':
                toplevel.close()
            self._display.flush()
        except Exception as exc:  # noqa: BLE001
            log.warning('toplevel %s %r failed: %s', op, handle, exc)

    def _wakeup(self) -> None:
        if self._wakeup_w is None:
            self._drain_requests()
            return
        try:
            os.write(self._wakeup_w, b'\0')
        except OSError:
            pass

    def activate(self, handle: object) -> None:
        self._requests.put(('activate', handle))
        self._wakeup()

    def close(self, handle: object) -> None:
        self._requests.put(('close', handle))
        self._wakeup()

    def shutdown(self) -> None:
        self._closing = True
        self._wakeup()
        try:
            disconnect = getattr(self._display, 'disconnect', None)
            if callable(disconnect):
                disconnect()
        except Exception:
            pass
        for attr in ('_wakeup_r', '_wakeup_w'):
            fd = getattr(self, attr)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, attr, None)


class ToplevelManager:
    """Tracks foreign toplevels and dispatches activate/close.

    The backend is the only component that touches pywayland. It must expose:

        available() -> bool
        connect(callback) -> None   # callback(handle, app_id, title) on change,
                                    # callback(handle, None, None) on removal
        activate(handle) -> None
        close(handle) -> None

    The manager keeps the authoritative registry and applies own-app filtering
    and closed-handle pruning on top of whatever the backend reports.
    """

    def __init__(self, backend: object | None = None) -> None:
        if backend is None:
            backend = _make_default_backend()
        self._backend = backend
        self._registry: dict[object, Toplevel] = {}
        self._callbacks: list[object] = []
        self._lock = threading.Lock()
        self._activated: object | None = None
        if backend is not None:
            # Bind before the first globals arrive: the backend is now
            # non-blocking, so available() may still be False at construct.
            backend.connect(self._on_backend_event)
            connect_focus = getattr(backend, 'connect_focus', None)
            if callable(connect_focus):
                connect_focus(self._on_focus)

    @property
    def available(self) -> bool:
        """True when a working protocol backend could be established."""
        return self._backend is not None and self._backend.available()

    def list(self) -> list[Toplevel]:
        """Current toplevels, own surfaces filtered out, insertion-ordered."""
        with self._lock:
            return [t for t in self._registry.values() if t.app_id != _OWN_APP_ID]

    def activated_app_id(self) -> str | None:
        """app_id of the focused foreign toplevel, or None."""
        with self._lock:
            t = self._registry.get(self._activated)
        if t is None or t.app_id == _OWN_APP_ID or not t.app_id:
            return None
        return t.app_id

    def activate(self, handle: object) -> None:
        if self._backend is not None and self._backend.available():
            self._backend.activate(handle)

    def close(self, handle: object) -> None:
        if self._backend is not None and self._backend.available():
            self._backend.close(handle)

    def resync(self, backend: object | None = None) -> None:
        """Drop the current protocol client and bind again.

        A live connection can stay available() True after it stops receiving
        new toplevels (phoc sends the initial snapshot, then silence). Recents
        then shows "No open apps" while windows are on screen. Re-binding
        pulls the current list, the same way a fresh standalone client does.
        """
        old = self._backend
        with self._lock:
            self._registry.clear()
            self._activated = None
        if backend is None:
            backend = _make_default_backend()
        self._backend = backend
        if backend is not None:
            backend.connect(self._on_backend_event)
            connect_focus = getattr(backend, 'connect_focus', None)
            if callable(connect_focus):
                connect_focus(self._on_focus)
        closer = getattr(old, 'shutdown', None)
        if callable(closer) and old is not backend:
            closer()
        self._notify()

    def on_change(self, callback: object) -> None:
        """Register a zero-arg callable invoked whenever the registry changes."""
        self._callbacks.append(callback)

    def _notify(self) -> None:
        if threading.current_thread() is threading.main_thread():
            self._emit_change()
            return
        from gi.repository import GLib
        GLib.idle_add(self._emit_change_idle)

    def _emit_change_idle(self) -> bool:
        self._emit_change()
        return False

    def _emit_change(self) -> None:
        for cb in self._callbacks:
            try:
                cb()
            except Exception as exc:  # noqa: BLE001 - one bad callback must not break the registry
                log.error('toplevel change callback failed: %s', exc)

    def _on_focus(self, handle: object, activated: bool) -> None:
        with self._lock:
            if activated:
                self._activated = handle
            elif self._activated == handle:
                self._activated = None

    def _on_backend_event(self, handle: object, app_id: object, title: object) -> None:
        with self._lock:
            if app_id is None:
                self._registry.pop(handle, None)
                if self._activated == handle:
                    self._activated = None
            else:
                self._registry[handle] = Toplevel(
                    app_id=app_id, title=title or '', handle=handle,
                )
        self._notify()


def _parse_states(state: object) -> set[int]:
    if state is None:
        return set()
    if isinstance(state, (bytes, bytearray, memoryview)):
        raw = bytes(state)
        return {
            int.from_bytes(raw[i:i + 4], 'little')
            for i in range(0, len(raw) - 3, 4)
        }
    if isinstance(state, (list, tuple, set)):
        out: set[int] = set()
        for item in state:
            try:
                out.add(int(item))
            except (TypeError, ValueError):
                continue
        return out
    return set()


def _make_default_backend() -> object | None:
    """Build the pywayland backend, or None when pywayland is unavailable."""
    if not _PYWAYLAND_AVAILABLE:
        log.info('pywayland unavailable, toplevel manager disabled')
        return None
    try:
        return WaylandToplevelBackend()
    except Exception as exc:  # noqa: BLE001 - graceful absence on any failure
        log.info('failed to start toplevel backend: %s', exc)
        return None
