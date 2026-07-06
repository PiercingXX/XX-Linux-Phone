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

# MM1 Number property returns the remote party URI (tel:+1234567890)
def _strip_tel(number: str) -> str:
    return number.removeprefix('tel:').strip() or number


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
        GLib.idle_add(self._init_bus)

    def _init_bus(self) -> bool:
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
        except GLib.Error:
            pass
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
        # Subscribe to StateChanged on this specific call object
        self._bus.signal_subscribe(
            _MM1, _CALL_IFACE, 'StateChanged', obj_path,
            None, Gio.DBusSignalFlags.NONE,
            lambda _c, _s, _p, _i, _sig, params, _ud: self._on_state_changed(
                obj_path, number, params
            ),
            None,
        )

    def _on_state_changed(self, obj_path: str, number: str, params: object) -> None:
        _old, new_state, _reason = params.unpack()
        if new_state == _STATE_ACTIVE:
            GLib.idle_add(self._on_answered, '', number)
        elif new_state == _STATE_TERMINATED:
            GLib.idle_add(self._on_ended)

    def _on_interfaces_removed(
        self, _c, _sender, _path, _iface, _sig, params, _ud
    ) -> None:
        obj_path, removed = params.unpack()
        if obj_path == self._active_call_path and _CALL_IFACE in removed:
            self._active_call_path = None
            GLib.idle_add(self._on_ended)
