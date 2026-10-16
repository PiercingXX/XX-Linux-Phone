"""Async client for the privileged helper service (``f_helper``).

The shell never runs as root: battery charge limits, per-app network
blocks and cgroup freezing go through the ``io.piercingxx.XXWM.Helper1``
system-bus service. Every call here is async on the caller's GLib main
loop — ``on_done(ok, result)`` fires exactly once with ``(True, value)``
or ``(False, error_string)`` — and a missing helper degrades to the
``HELPER_NOT_INSTALLED`` error instead of raising.

Gio is imported lazily so this module stays importable without
PyGObject (tests, doc builds); the first call on a system without Gio
reports failure through the callback.
"""
from __future__ import annotations

from typing import Any, Callable

BUS_NAME = 'io.piercingxx.XXWM.Helper1'
OBJECT_PATH = '/io/piercingxx/XXWM/Helper1'
INTERFACE = BUS_NAME

HELPER_NOT_INSTALLED = 'helper not installed'

_CALL_TIMEOUT_MS = 25_000

ResultCallback = Callable[[bool, Any], None]

_capabilities_cache: dict | None = None


def _gio():
    from gi.repository import Gio, GLib
    return Gio, GLib


def invalidate_capabilities() -> None:
    """Drop the cached capability probe (call when the helper is installed
    or upgraded mid-session)."""
    global _capabilities_cache
    _capabilities_cache = None


def _is_missing_service(message: str) -> bool:
    lowered = (message or '').lower()
    return ('serviceunknown' in lowered or 'namehasnoowner' in lowered
            or 'activat' in lowered or 'no such service' in lowered)


def _call_helper(method: str, params, on_done: ResultCallback) -> None:
    """One async helper call; ``params`` is a GLib.Variant or None."""
    try:
        Gio, _GLib = _gio()
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return

    def _fail(message: str) -> None:
        on_done(False, HELPER_NOT_INSTALLED
                if _is_missing_service(message) else message)

    def _on_bus(source, result) -> None:
        try:
            bus = Gio.bus_get_finish(result)
        except Exception as err:
            _fail(str(err))
            return

        def _on_reply(conn, reply_result) -> None:
            try:
                reply = conn.call_finish(reply_result)
            except Exception as err:
                _fail(str(err))
                return
            try:
                out = reply.unpack()
            except Exception as err:
                _fail(str(err))
                return
            on_done(True, out[0] if out else None)

        try:
            bus.call(
                BUS_NAME, OBJECT_PATH, INTERFACE, method, params, None,
                Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None, _on_reply,
            )
        except Exception as err:
            _fail(str(err))

    try:
        Gio.bus_get(Gio.BusType.SYSTEM, None, _on_bus)
    except Exception as err:
        _fail(str(err))


def get_capabilities(on_done: ResultCallback, *, refresh: bool = False) -> None:
    """Probe helper capabilities (cached per session unless ``refresh``).

    ``on_done(True, {'charge_limit_supported': bool, 'nftables': bool,
    'cgroup2': bool, 'freezer': bool})`` or ``(False, error)`` where a
    missing helper yields ``HELPER_NOT_INSTALLED``.
    """
    if _capabilities_cache is not None and not refresh:
        on_done(True, dict(_capabilities_cache))
        return

    def _on_reply(ok: bool, result) -> None:
        if ok and isinstance(result, dict):
            global _capabilities_cache
            _capabilities_cache = dict(result)
            on_done(True, dict(result))
            return
        on_done(False, result)

    _call_helper('GetCapabilities', None, _on_reply)


def set_charge_limit(percent: int, on_done: ResultCallback) -> None:
    """Ask the helper to clamp and apply a battery charge limit (50..100).

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(u)', (max(0, int(percent)),))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    except (TypeError, ValueError):
        on_done(False, 'charge limit is not a number: %r' % (percent,))
        return
    _call_helper('SetChargeLimit', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def set_app_network(cgroup_path: str, allowed: bool,
                    on_done: ResultCallback) -> None:
    """Block (``allowed`` False) or unblock network for one app cgroup.

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(sb)', (str(cgroup_path), bool(allowed)))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('SetAppNetwork', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def freeze_cgroup(cgroup_path: str, frozen: bool,
                  on_done: ResultCallback) -> None:
    """Freeze (``frozen`` True) or thaw one app cgroup (non-systemd path).

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(sb)', (str(cgroup_path), bool(frozen)))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('FreezeCgroup', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))
