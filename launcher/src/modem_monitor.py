"""
Watches ModemManager1 DBus for incoming/outgoing voice calls and emits
callbacks so the shell can show/hide CallUI and CallBar.

ModemManager call lifecycle via DBus:
  - ObjectManager.InterfacesAdded on /org/freedesktop/ModemManager1
    with interface org.freedesktop.ModemManager1.Call added
  - Call object: Direction (0=unknown,1=incoming,2=outgoing)
               State    (0=unknown,1=dialing,2=ringing-out,
                         3=ringing-in,4=active,5=held,6=waiting,7=terminated)
  - ObjectManager.InterfacesRemoved when call ends
"""
from __future__ import annotations

from typing import Callable

from gi.repository import GLib, Gio

_MM1 = 'org.freedesktop.ModemManager1'
_MM1_PATH = '/org/freedesktop/ModemManager1'
_CALL_IFACE = 'org.freedesktop.ModemManager1.Call'
_OBJMGR_IFACE = 'org.freedesktop.DBus.ObjectManager'

_DIR_INCOMING = 1
_STATE_RINGING_IN = 3
_STATE_ACTIVE = 4
_STATE_TERMINATED = 7

_CALL_TIMEOUT_MS = 5_000

_last_monitor: ModemMonitor | None = None


# MM1 Number property returns the remote party URI (tel:+1234567890)
def _strip_tel(number: str) -> str:
    return number.removeprefix('tel:').strip() or number


def active_call_path() -> str | None:
    """Object path of the call currently tracked by the monitor, if any."""
    if _last_monitor is None:
        return None
    return _last_monitor._active_call_path


CallResultCallback = Callable[[bool, str | None], None]


def accept_call(call_path: str, on_done: CallResultCallback) -> None:
    """
    Asynchronously Accept the call at ``call_path``.

    ``on_done(success, error)`` fires exactly once on the main loop when the
    MM1 method completes — or immediately on local failure (no monitor, no
    live bus connection). The D-Bus round trip rides Gio's async machinery,
    so a wedged ModemManager can never block the calling thread.
    """
    _call_method(call_path, 'Accept', on_done)


def hangup_call(call_path: str, on_done: CallResultCallback) -> None:
    """Asynchronously Hangup; same delivery contract as :func:`accept_call`."""
    _call_method(call_path, 'Hangup', on_done)


def _call_method(call_path: str, method: str, on_done: CallResultCallback) -> None:
    from shell_log import get_logger

    def _fail(message: str) -> None:
        get_logger('modem_monitor').warning('%s %s failed: %s', call_path, method, message)
        on_done(False, message)

    monitor = _last_monitor
    bus = monitor._bus if monitor is not None else None
    if bus is None:
        # No live system-bus connection (no monitor yet, or its bus init
        # failed): fail immediately instead of stalling the UI thread.
        _fail('system bus unavailable')
        return

    def _on_called(conn, result) -> None:
        try:
            conn.call_finish(result)
        except GLib.Error as err:
            _fail(getattr(err, 'message', None) or str(err))
            return
        on_done(True, None)

    try:
        bus.call(
            _MM1, call_path, _CALL_IFACE, method, None, None,
            Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None, _on_called,
        )
    except GLib.Error as err:
        # Dispatch itself failed (e.g. connection died between the check and
        # the call): still honour the exactly-once callback contract.
        _fail(getattr(err, 'message', None) or str(err))


class ModemMonitor:
    """
    Subscribe once; fires callbacks on the GLib main loop:
        on_incoming(caller, number)
        on_answered(caller, number)
        on_ended()
    """

    def __init__(
        self,
        on_incoming: Callable[[str, str], None],
        on_answered: Callable[[str, str], None],
        on_ended: Callable[[], None],
    ) -> None:
        self._on_incoming = on_incoming
        self._on_answered = on_answered
        self._on_ended = on_ended
        self._bus: Gio.DBusConnection | None = None
        self._active_call_path: str | None = None
        self._state_subs: dict[str, int] = {}
        global _last_monitor
        _last_monitor = self
        GLib.idle_add(self._init_bus)

    def _init_bus(self) -> bool:
        from shell_log import get_logger
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._bus.signal_subscribe(
                _MM1, _OBJMGR_IFACE, 'InterfacesAdded', _MM1_PATH,
                None, Gio.DBusSignalFlags.NONE,
                self._on_interfaces_added, None,
            )
            self._bus.signal_subscribe(
                _MM1, _OBJMGR_IFACE, 'InterfacesRemoved', _MM1_PATH,
                None, Gio.DBusSignalFlags.NONE,
                self._on_interfaces_removed, None,
            )
        except GLib.Error as err:
            get_logger('modem_monitor').warning('system bus unavailable: %s', err)
        return False

    def _on_interfaces_added(
        self, _c, _sender, _path, _iface, _sig, params, _ud
    ) -> None:
        obj_path, interfaces = params.unpack()
        if _CALL_IFACE not in interfaces:
            return
        props = interfaces[_CALL_IFACE]
        direction = props.get('Direction', 0)
        state = props.get('State', 0)
        if direction != _DIR_INCOMING:
            return
        self._active_call_path = obj_path
        number = _strip_tel(props.get('Number', ''))
        if state == _STATE_RINGING_IN:
            GLib.idle_add(self._on_incoming, 'Incoming call', number)
        elif state == _STATE_ACTIVE:
            GLib.idle_add(self._on_answered, 'Active call', number)
        if obj_path in self._state_subs or self._bus is None:
            return
        sub_id = self._bus.signal_subscribe(
            _MM1, _CALL_IFACE, 'StateChanged', obj_path,
            None, Gio.DBusSignalFlags.NONE,
            lambda _c, _s, _p, _i, _sig, params, _ud: self._on_state_changed(
                obj_path, number, params
            ),
            None,
        )
        self._state_subs[obj_path] = sub_id

    def _on_state_changed(self, obj_path: str, number: str, params: object) -> None:
        _old, new_state, _reason = params.unpack()
        if new_state == _STATE_ACTIVE:
            GLib.idle_add(self._on_answered, '', number)
        elif new_state == _STATE_TERMINATED:
            self._drop_subscription(obj_path)
            GLib.idle_add(self._on_ended)

    def _drop_subscription(self, obj_path: str) -> None:
        sub_id = self._state_subs.pop(obj_path, None)
        if sub_id is not None and self._bus is not None:
            self._bus.signal_unsubscribe(sub_id)

    def _on_interfaces_removed(
        self, _c, _sender, _path, _iface, _sig, params, _ud
    ) -> None:
        obj_path, removed = params.unpack()
        if _CALL_IFACE in removed:
            self._drop_subscription(obj_path)
        if obj_path == self._active_call_path:
            self._active_call_path = None
            GLib.idle_add(self._on_ended)
