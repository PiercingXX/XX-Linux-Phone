"""
In-process org.freedesktop.Notifications DBus service.

The freedesktop notification spec defines Notify as a METHOD call directed
at whichever process owns org.freedesktop.Notifications — not a broadcast
signal. Without a daemon owning that name, applications send their Notify
calls into the void. This daemon owns the name, receives method calls, and
routes them to a caller-supplied callback.

If another daemon (dunst, mako, fnott) is already running and owns the name,
we silently back off. The shade's fallback signal-subscription path remains
active in that case.

Capabilities advertised: body, body-markup, persistence
"""
from __future__ import annotations

from typing import Callable

from gi.repository import GLib, Gio

_IFACE_NAME = 'org.freedesktop.Notifications'
_OBJ_PATH = '/org/freedesktop/Notifications'

_INTROSPECTION_XML = """
<node>
  <interface name='org.freedesktop.Notifications'>
    <method name='GetCapabilities'>
      <arg direction='out' name='caps' type='as'/>
    </method>
    <method name='Notify'>
      <arg direction='in'  name='app_name'        type='s'/>
      <arg direction='in'  name='replaces_id'      type='u'/>
      <arg direction='in'  name='app_icon'         type='s'/>
      <arg direction='in'  name='summary'          type='s'/>
      <arg direction='in'  name='body'             type='s'/>
      <arg direction='in'  name='actions'          type='as'/>
      <arg direction='in'  name='hints'            type='a{sv}'/>
      <arg direction='in'  name='expire_timeout'   type='i'/>
      <arg direction='out' name='id'               type='u'/>
    </method>
    <method name='CloseNotification'>
      <arg direction='in' name='id' type='u'/>
    </method>
    <method name='GetServerInformation'>
      <arg direction='out' name='name'         type='s'/>
      <arg direction='out' name='vendor'       type='s'/>
      <arg direction='out' name='version'      type='s'/>
      <arg direction='out' name='spec_version' type='s'/>
    </method>
    <signal name='NotificationClosed'>
      <arg name='id'     type='u'/>
      <arg name='reason' type='u'/>
    </signal>
    <signal name='ActionInvoked'>
      <arg name='id'         type='u'/>
      <arg name='action_key' type='s'/>
    </signal>
  </interface>
</node>
"""

# NotificationClosed reason codes per spec
_REASON_EXPIRED = 1
_REASON_DISMISSED = 2
_REASON_CLOSED = 3


class NotificationDaemon:
    """
    Owns org.freedesktop.Notifications on the session bus.
    Fires on_notify(id, app_name, summary, body, desktop_entry) for each Notify call.
    Fires on_close(id) when CloseNotification is called or auto-expire fires.
    """

    def __init__(
        self,
        on_notify: Callable[[int, str, str, str, str], None],
        on_close: Callable[[int], None] | None = None,
    ) -> None:
        self._on_notify = on_notify
        self._on_close = on_close or (lambda _id: None)
        self._bus: Gio.DBusConnection | None = None
        self._reg_id: int = 0
        self._name_id: int = 0
        self._next_id: int = 1
        self._expire_timers: dict[int, int] = {}
        GLib.idle_add(self._start)

    def _start(self) -> bool:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            node_info = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION_XML)
            iface_info = node_info.lookup_interface(_IFACE_NAME)
            self._reg_id = self._bus.register_object(
                _OBJ_PATH,
                iface_info,
                self._on_method_call,
                None,
                None,
            )
            self._name_id = Gio.bus_own_name_on_connection(
                self._bus,
                _IFACE_NAME,
                Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                self._on_name_acquired,
                self._on_name_lost,
            )
        except GLib.Error:
            pass
        return False

    def _on_name_acquired(self, _conn: Gio.DBusConnection, name: str) -> None:
        from shell_log import get_logger
        get_logger('notif_daemon').info('acquired DBus name %s', name)

    def _on_name_lost(self, _conn: Gio.DBusConnection | None, name: str) -> None:
        # Another daemon already owns the name — back off silently.
        from shell_log import get_logger
        get_logger('notif_daemon').debug('could not own %s — another daemon is running', name)

    def _on_method_call(
        self,
        conn: Gio.DBusConnection,
        sender: str,
        _path: str,
        _iface: str,
        method: str,
        params: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        try:
            if method == 'GetCapabilities':
                invocation.return_value(
                    GLib.Variant('(as)', (['body', 'body-markup', 'persistence'],))
                )
            elif method == 'GetServerInformation':
                invocation.return_value(
                    GLib.Variant('(ssss)', ('PiercingOS', 'PiercingXX', '1.0', '1.2'))
                )
            elif method == 'Notify':
                notif_id = self._handle_notify(params)
                invocation.return_value(GLib.Variant('(u)', (notif_id,)))
            elif method == 'CloseNotification':
                notif_id = params.unpack()[0]
                self._close_notification(conn, notif_id, _REASON_CLOSED)
                invocation.return_value(None)
            else:
                invocation.return_error_literal(
                    Gio.dbus_error_quark(),
                    Gio.DBusError.UNKNOWN_METHOD,
                    f'Unknown method: {method}',
                )
        except Exception as exc:
            invocation.return_error_literal(
                Gio.dbus_error_quark(),
                Gio.DBusError.FAILED,
                str(exc),
            )

    def _handle_notify(self, params: GLib.Variant) -> int:
        parts = params.unpack()
        app_name = str(parts[0])
        replaces_id = int(parts[1]) if parts[1] else 0
        summary = str(parts[3])
        body = str(parts[4])
        hints: dict = parts[6] if len(parts) > 6 else {}
        expire_timeout = int(parts[7]) if len(parts) > 7 else -1

        desktop_entry = str(hints.get('desktop-entry', ''))

        notif_id = replaces_id if replaces_id else self._next_id
        self._next_id = max(self._next_id, notif_id) + 1

        # Cancel previous expire timer if replacing
        if notif_id in self._expire_timers:
            GLib.source_remove(self._expire_timers.pop(notif_id))

        GLib.idle_add(self._on_notify, notif_id, app_name, summary, body, desktop_entry)

        if expire_timeout > 0:
            timer_id = GLib.timeout_add(
                expire_timeout,
                lambda nid=notif_id: self._expire_notification(nid),
            )
            self._expire_timers[notif_id] = timer_id

        return notif_id

    def _expire_notification(self, notif_id: int) -> bool:
        self._expire_timers.pop(notif_id, None)
        if self._bus:
            self._close_notification(self._bus, notif_id, _REASON_EXPIRED)
        return False

    def _close_notification(
        self, conn: Gio.DBusConnection, notif_id: int, reason: int
    ) -> None:
        self._expire_timers.pop(notif_id, None)
        GLib.idle_add(self._on_close, notif_id)
        try:
            conn.emit_signal(
                None,
                _OBJ_PATH,
                _IFACE_NAME,
                'NotificationClosed',
                GLib.Variant('(uu)', (notif_id, reason)),
            )
        except GLib.Error:
            pass

    def close_from_shell(self, notif_id: int) -> None:
        """Called by the shade when the user dismisses a notification."""
        if self._bus:
            self._close_notification(self._bus, notif_id, _REASON_DISMISSED)

    def stop(self) -> None:
        if self._name_id:
            Gio.bus_unown_name(self._name_id)
        if self._reg_id and self._bus:
            self._bus.unregister_object(self._reg_id)
