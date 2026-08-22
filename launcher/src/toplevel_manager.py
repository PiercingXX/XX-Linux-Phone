"""pywayland client for wlr-foreign-toplevel-management-unstable-v1.

Lists, activates and closes toplevel windows on a wlroots compositor (phoc).
The pywayland interaction is isolated behind a backend so the manager's pure
logic (own-app filtering, closed-handle pruning, ordering, dispatch) is
testable without a compositor or pywayland installed. If pywayland or the
protocol is unavailable the manager degrades to an empty state instead of
crashing, so the switcher can still show "No open apps".
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# The shell's own surfaces must never appear in the switcher.
_OWN_APP_ID = 'io.piercingxx.XXWM'

_PYWAYLAND_AVAILABLE = (
    importlib.util.find_spec('pywayland') is not None
    and importlib.util.find_spec('pywayland.client') is not None
)
if _PYWAYLAND_AVAILABLE:
    from pywayland.client import Client
    from pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1 import (
        ZwlrForeignToplevelHandleV1,
        ZwlrForeignToplevelManagerV1,
    )
else:  # pragma: no cover - environment dependent
    Client = None  # type: ignore[assignment,misc]
    ZwlrForeignToplevelHandleV1 = None  # type: ignore[assignment,misc]


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
        if Client is None:
            raise RuntimeError('pywayland is not available')
        self._client = Client()
        self._manager: object | None = None
        self._handles: dict[object, object] = {}
        self._manager_cb = None
        self._fd_source = None
        self._display = self._client.display
        self._connect()

    def _connect(self) -> None:
        try:
            self._client.connect()
        except Exception as exc:
            raise RuntimeError(f'cannot connect to Wayland display: {exc}') from exc
        registry = self._display.get_registry()
        registry.dispatcher[self._on_registry_global] = 'ZwlpRegistry'
        registry.dispatcher[self._on_registry_global_remove] = 'ZwlpRegistry'
        self._display.dispatch(block=True)
        self._display.flush()
        if self._manager is None:
            raise RuntimeError('compositor does not offer wlr-foreign-toplevel-management')

    # -- GLib fd-watch -----------------------------------------------------

    def _arm_fd_watch(self) -> None:
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import GLib

        fd = self._client.get_fd()
        self._fd_source = GLib.unix_fd_add(
            fd, GLib.IOCondition.IN | GLib.IOCondition.HUP, self._on_fd_ready,
        )

    def _on_fd_ready(self, _fd: int, _cond: int) -> bool:
        from gi.repository import GLib

        if _cond & GLib.IOCondition.HUP:
            # Compositor closed the socket; stop watching instead of spinning
            # on a dead fd forever.
            log.error('wayland compositor connection closed (HUP)')
            return False
        try:
            self._display.dispatch(block=False)
            self._display.flush()
        except Exception as exc:  # noqa: BLE001 - compositor went away
            log.error('wayland dispatch failed: %s', exc)
            return False  # dispatch is unrecoverable; stop the fd-watch
        return True  # keep watching

    # -- protocol glue -----------------------------------------------------

    def _on_registry_global(self, registry, _serial: int, name: int, interface: str, _version: int) -> None:
        if interface == 'zwlr_foreign_toplevel_manager_v1':
            self._manager = registry.bind(
                name, ZwlrForeignToplevelManagerV1, 3,
            )
            self._manager.dispatcher[self._on_toplevel] = 'zwlr_foreign_toplevel_manager_v1'
            self._manager.dispatcher[self._on_finished] = 'zwlr_foreign_toplevel_manager_v1'
            self._manager.create_toplevel()

    def _on_registry_global_remove(self, _registry, _name: int) -> None:
        pass

    def _on_toplevel(self, manager, toplevel: ZwlrForeignToplevelHandleV1) -> None:
        self._handles[id(toplevel)] = toplevel
        toplevel.dispatcher[self._on_title] = 'zwlr_foreign_toplevel_handle_v1'
        toplevel.dispatcher[self._on_app_id] = 'zwlr_foreign_toplevel_handle_v1'
        toplevel.dispatcher[self._on_closed] = 'zwlr_foreign_toplevel_handle_v1'
        toplevel.dispatcher[self._on_state] = 'zwlr_foreign_toplevel_handle_v1'
        self._upsert(id(toplevel), '', '')

    def _on_title(self, toplevel, title: str) -> None:
        self._upsert(id(toplevel), self._app_id_of(toplevel), title)

    def _on_app_id(self, toplevel, app_id: str) -> None:
        self._upsert(id(toplevel), app_id, self._title_of(toplevel))

    def _on_state(self, toplevel, _state) -> None:
        pass

    def _on_closed(self, toplevel) -> None:
        self._remove(id(toplevel))
        self._handles.pop(id(toplevel), None)

    def _on_finished(self, _manager) -> None:
        # Compositor dropped the protocol; clear everything.
        for handle in list(self._handles):
            self._remove(handle)
        self._handles.clear()

    def _app_id_of(self, toplevel) -> str:
        return getattr(toplevel, 'app_id', '') or ''

    def _title_of(self, toplevel) -> str:
        return getattr(toplevel, 'title', '') or ''

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
        if toplevel is not None:
            toplevel.activate()
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
        if backend is not None and backend.available():
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