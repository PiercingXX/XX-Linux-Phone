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
from dataclasses import dataclass

log = logging.getLogger(__name__)

# The shell's own surfaces must never appear in the switcher.
_OWN_APP_ID = 'io.piercingxx.XXWM'

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

    Runs on a GLib fd-watch so the GTK main loop is never blocked: the
    compositor's events are dispatched from the idle callback the watch
    schedules. The backend only mutates the manager's registry via the
    ``_upsert``/``_remove`` hooks; it never reads state back.
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
        self._fd_source = None
        self._connect()

    def _connect(self) -> None:
        try:
            self._display.connect()
        except Exception as exc:
            raise RuntimeError(f'cannot connect to Wayland display: {exc}') from exc
        registry = self._display.get_registry()
        registry.dispatcher['global'] = self._on_registry_global
        registry.dispatcher['global_remove'] = self._on_registry_global_remove
        # Never block the GTK thread on compositor round-trips: that froze
        # power-button idle_add, lock, and Settings until a hard reboot.
        self._arm_fd_watch()
        try:
            self._display.dispatch(block=False)
            self._display.flush()
        except Exception:
            pass

    # -- GLib fd-watch -----------------------------------------------------

    def _arm_fd_watch(self) -> None:
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import GLib

        fd = self._display.get_fd()
        condition = GLib.IOCondition.IN | GLib.IOCondition.HUP
        # Tablet GLib GI has unix_fd_add_full / io_add_watch, not unix_fd_add.
        if hasattr(GLib, 'unix_fd_add_full'):
            self._fd_source = GLib.unix_fd_add_full(
                getattr(GLib, 'PRIORITY_DEFAULT', 0), fd, condition,
                self._on_fd_ready, None)
        elif hasattr(GLib, 'io_add_watch'):
            self._fd_source = GLib.io_add_watch(fd, condition, self._on_fd_ready)
        elif hasattr(GLib, 'unix_fd_add'):
            self._fd_source = GLib.unix_fd_add(fd, condition, self._on_fd_ready)
        else:
            raise RuntimeError('no GLib fd-watch API')
        def _kick() -> bool:
            try:
                self._display.dispatch(block=False)
                self._display.flush()
            except Exception as exc:  # noqa: BLE001
                # EAGAIN (11) means the fd had nothing; the watch will fire.
                if '11' not in str(exc) and 'EAGAIN' not in str(exc).upper():
                    log.error('wayland kick dispatch failed: %s', exc)
            return False
        GLib.idle_add(_kick)

    def _on_fd_ready(self, *args) -> bool:
        from gi.repository import GLib

        # unix_fd_add_full may pass (fd, condition, user_data); io_add_watch
        # passes (fd, condition). Treat as HUP only when HUP is set without IN.
        cond = 0
        for arg in args:
            try:
                cond = int(arg)
            except (TypeError, ValueError):
                continue
        if cond & GLib.IOCondition.HUP and not (cond & GLib.IOCondition.IN):
            log.error('wayland compositor connection closed (HUP)')
            return False
        try:
            self._display.dispatch(block=False)
            self._display.flush()
        except Exception as exc:  # noqa: BLE001 - compositor went away
            if '11' in str(exc) or 'EAGAIN' in str(exc).upper():
                return True
            log.error('wayland dispatch failed: %s', exc)
            return False
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
            self._manager = registry.bind(name, ZwlrForeignToplevelManagerV1, min(3, version))
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
        self._upsert(id(toplevel), '', '')

    def _on_title(self, toplevel, title: str) -> None:
        info = self._info.setdefault(id(toplevel), ['', ''])
        info[1] = title
        self._upsert(id(toplevel), info[0], info[1])

    def _on_app_id(self, toplevel, app_id: str) -> None:
        info = self._info.setdefault(id(toplevel), ['', ''])
        info[0] = app_id
        self._upsert(id(toplevel), info[0], info[1])

    def _on_state(self, toplevel, _state) -> None:
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
        if backend is not None:
            # Bind before the first globals arrive: the backend is now
            # non-blocking, so available() may still be False at construct.
            backend.connect(self._on_backend_event)

    @property
    def available(self) -> bool:
        """True when a working protocol backend could be established."""
        return self._backend is not None and self._backend.available()

    def list(self) -> list[Toplevel]:
        """Current toplevels, own surfaces filtered out, insertion-ordered."""
        return [t for t in self._registry.values() if t.app_id != _OWN_APP_ID]

    def activate(self, handle: object) -> None:
        if self._backend is not None and self._backend.available():
            self._backend.activate(handle)

    def close(self, handle: object) -> None:
        if self._backend is not None and self._backend.available():
            self._backend.close(handle)

    def on_change(self, callback: object) -> None:
        """Register a zero-arg callable invoked whenever the registry changes."""
        self._callbacks.append(callback)

    def _notify(self) -> None:
        for cb in self._callbacks:
            try:
                cb()
            except Exception as exc:  # noqa: BLE001 - one bad callback must not break the registry
                log.error('toplevel change callback failed: %s', exc)

    def _on_backend_event(self, handle: object, app_id: object, title: object) -> None:
        if app_id is None:
            self._registry.pop(handle, None)
            self._notify()
            return
        self._registry[handle] = Toplevel(
            app_id=app_id, title=title or '', handle=handle,
        )
        self._notify()


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
