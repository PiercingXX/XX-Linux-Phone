"""Session-bus API between the xx-share UI and the background receiver.

Bus name: ``io.piercingxx.XXShare1``
Object path: ``/io/piercingxx/XXShare``
Interface: ``io.piercingxx.XXShare1``

Methods (all async on the real bus; the dispatcher below is Gio-free so
headless tests drive it through a fake connection):

- ``SetVisible(b visible)`` — start/stop the receiver.
- ``ListPeers() -> (aa{sv})`` — nearby devices from discovery. Each peer
  dict carries string keys ``alias``, ``fingerprint``, ``addr``,
  ``device_type`` and ``port`` (uint32).
- ``Send(s fingerprint, as files, s pin) -> (s transfer_id)`` — start a
  background send; progress arrives via ``TransferProgress``.
- ``Cancel(s transfer_id)`` — abort an in-flight outgoing transfer.

Signals:

- ``TransferProgress(s transfer_id, s file_id, x sent, x total)``
- ``TransferDone(s transfer_id, b ok, s message)``
- ``IncomingRequest(s session_id, s alias, s fingerprint,
  as file_names, x total_bytes)``

Gio callbacks follow the real signatures: method replies unpack as a
TUPLE of out-args (``reply.unpack()[0]``), async ``call`` callbacks take
``(conn, result)`` with no trailing ``user_data``, and signal handlers
take the seven ``signal_subscribe`` args.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

BUS_NAME = "io.piercingxx.XXShare1"
OBJECT_PATH = "/io/piercingxx/XXShare"
INTERFACE = BUS_NAME

INTROSPECTION_XML = f"""<node>
  <interface name="{INTERFACE}">
    <method name="SetVisible">
      <arg name="visible" type="b" direction="in"/>
    </method>
    <method name="ListPeers">
      <arg name="peers" type="aa{{sv}}" direction="out"/>
    </method>
    <method name="Send">
      <arg name="fingerprint" type="s" direction="in"/>
      <arg name="files" type="as" direction="in"/>
      <arg name="pin" type="s" direction="in"/>
      <arg name="transfer_id" type="s" direction="out"/>
    </method>
    <method name="Cancel">
      <arg name="transfer_id" type="s" direction="in"/>
    </method>
    <signal name="TransferProgress">
      <arg name="transfer_id" type="s"/>
      <arg name="file_id" type="s"/>
      <arg name="sent" type="x"/>
      <arg name="total" type="x"/>
    </signal>
    <signal name="TransferDone">
      <arg name="transfer_id" type="s"/>
      <arg name="ok" type="b"/>
      <arg name="message" type="s"/>
    </signal>
    <signal name="IncomingRequest">
      <arg name="session_id" type="s"/>
      <arg name="alias" type="s"/>
      <arg name="fingerprint" type="s"/>
      <arg name="file_names" type="as"/>
      <arg name="total_bytes" type="x"/>
    </signal>
  </interface>
</node>
"""

PEER_KEYS = ("alias", "fingerprint", "addr", "device_type", "port")


class BusError(Exception):
    def __init__(self, name: str, message: str) -> None:
        super().__init__(message)
        self.name = name
        self.message = message


def peer_to_dict(alias: str, fingerprint: str, addr: str,
                 device_type: str | None = None, port: int = 53317) -> dict:
    return {
        "alias": str(alias),
        "fingerprint": str(fingerprint),
        "addr": str(addr),
        "device_type": str(device_type or ""),
        "port": int(port),
    }


def peer_from_dict(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise BusError("InvalidPeer", "peer must be a dict")
    alias = raw.get("alias")
    fingerprint = raw.get("fingerprint")
    addr = raw.get("addr")
    if not isinstance(alias, str) or not alias:
        raise BusError("InvalidPeer", "peer needs an alias")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise BusError("InvalidPeer", "peer needs a fingerprint")
    if not isinstance(addr, str) or not addr:
        raise BusError("InvalidPeer", "peer needs an address")
    port = raw.get("port", 53317)
    if isinstance(port, bool) or not isinstance(port, int):
        raise BusError("InvalidPeer", "peer port must be a number")
    device_type = raw.get("device_type", "")
    if device_type is not None and not isinstance(device_type, str):
        raise BusError("InvalidPeer", "peer device_type must be a string")
    return {
        "alias": alias,
        "fingerprint": fingerprint,
        "addr": addr,
        "device_type": str(device_type or ""),
        "port": port,
    }


def peers_variant(peers: list[dict]) -> object:
    """Wrap plain peer dicts for the ``aa{sv}`` out-arg.

    GLib does not auto-box plain values into variants, so every value is
    wrapped explicitly (strings as ``s``, the port as ``u``).
    """
    from gi.repository import GLib as _GLib

    wrapped = []
    for peer in peers:
        item = dict(peer)
        wrapped.append({
            "alias": _GLib.Variant("s", str(item.get("alias", ""))),
            "fingerprint": _GLib.Variant("s", str(item.get("fingerprint", ""))),
            "addr": _GLib.Variant("s", str(item.get("addr", ""))),
            "device_type": _GLib.Variant("s", str(item.get("device_type", ""))),
            "port": _GLib.Variant("u", int(item.get("port", 53317))),
        })
    return _GLib.Variant("(aa{sv})", (wrapped,))


def progress_args(transfer_id: str, file_id: str, sent: int, total: int) -> tuple:
    return (str(transfer_id), str(file_id), int(sent), int(total))


def incoming_args(session_id: str, alias: str, fingerprint: str,
                  file_names: list[str], total_bytes: int) -> tuple:
    return (str(session_id), str(alias), str(fingerprint),
            [str(name) for name in file_names], int(total_bytes))


class BusDispatcher:
    """Route bus methods to a service backend without touching Gio.

    ``backend`` provides ``set_visible(bool)``, ``list_peers()`` (plain
    peer dicts), ``start_send(fingerprint, files, pin)`` (transfer id)
    and ``cancel_send(transfer_id)`` (True when known).
    """

    def __init__(self, backend: object) -> None:
        self.backend = backend

    def dispatch(self, method: str, args: tuple) -> tuple:
        if method == "SetVisible":
            (visible,) = args
            self.backend.set_visible(bool(visible))  # type: ignore[union-attr]
            return ()
        if method == "ListPeers":
            peers = self.backend.list_peers()  # type: ignore[union-attr]
            return ([dict(item) for item in peers],)
        if method == "Send":
            fingerprint, files, pin = args
            if not isinstance(fingerprint, str) or not fingerprint:
                raise BusError("InvalidArgs", "Send needs a peer fingerprint")
            if (not isinstance(files, (list, tuple)) or not files
                    or not all(isinstance(item, str) and item for item in files)):
                raise BusError("InvalidArgs", "Send needs at least one file")
            transfer_id = self.backend.start_send(  # type: ignore[union-attr]
                fingerprint, [str(item) for item in files], str(pin or ""))
            return (str(transfer_id),)
        if method == "Cancel":
            (transfer_id,) = args
            if not self.backend.cancel_send(str(transfer_id)):  # type: ignore[union-attr]
                raise BusError("UnknownTransfer", "no such transfer")
            return ()
        raise BusError("UnknownMethod", f"no such method: {method}")


def _gio():
    from gi.repository import Gio, GLib  # noqa: E402
    return Gio, GLib


class BusServer:
    """Export the dispatcher on the session bus (the --service side)."""

    def __init__(self, dispatcher: BusDispatcher,
                 connection: object | None = None) -> None:
        self.dispatcher = dispatcher
        self.connection = connection
        self._reg_id = 0
        self._name_id = 0

    def start(self) -> bool:
        try:
            Gio, GLib = _gio()
        except ImportError:
            return False
        try:
            bus = self.connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return False
        self.connection = bus
        try:
            node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
            iface = node.lookup_interface(INTERFACE)
            self._reg_id = bus.register_object(
                OBJECT_PATH, iface, self._on_method_call, None, None)
            self._name_id = Gio.bus_own_name_on_connection(
                bus, BUS_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                self._on_name_acquired, self._on_name_lost)
        except GLib.Error:
            return False
        return True

    def _on_name_acquired(self, _conn: object, _name: str) -> None:
        pass

    def _on_name_lost(self, _conn: object, _name: str) -> None:
        pass

    def emit(self, signal: str, args: tuple) -> None:
        bus = self.connection
        if bus is None:
            return
        try:
            Gio, GLib = _gio()
            variant: object
            if signal == "TransferProgress":
                transfer_id, file_id, sent, total = args
                variant = GLib.Variant("(ssxx)", (str(transfer_id), str(file_id),
                                                  int(sent), int(total)))
            elif signal == "TransferDone":
                transfer_id, ok, message = args
                variant = GLib.Variant("(sbs)", (str(transfer_id), bool(ok),
                                                  str(message)))
            elif signal == "IncomingRequest":
                session_id, alias, fingerprint, names, total = args
                variant = GLib.Variant("(sssasx)", (str(session_id), str(alias),
                                                    str(fingerprint),
                                                    [str(n) for n in names],
                                                    int(total)))
            else:
                return
            bus.emit_signal(None, OBJECT_PATH, INTERFACE, signal, variant)
        except GLib.Error as error:
            logger.warning("emit %s failed: %s", signal, error)

    def _on_method_call(self, conn: object, _sender: str, _path: str,
                        _iface: str, method: str, params: object,
                        invocation: object) -> None:
        from gi.repository import Gio as _Gio
        try:
            args = params.unpack() if params is not None else ()  # type: ignore[union-attr]
            result = self.dispatcher.dispatch(str(method), tuple(args))
        except BusError as error:
            invocation.return_error_literal(  # type: ignore[union-attr]
                _Gio.dbus_error_quark(), _Gio.DBusError.FAILED,
                f"{error.name}: {error.message}")
            return
        except Exception as error:
            invocation.return_error_literal(  # type: ignore[union-attr]
                _Gio.dbus_error_quark(), _Gio.DBusError.FAILED, str(error))
            return
        try:
            from gi.repository import GLib as _GLib
            if method == "ListPeers":
                invocation.return_value(peers_variant(result[0]))  # type: ignore[union-attr]
            elif method == "Send":
                invocation.return_value(_GLib.Variant("(s)", result))  # type: ignore[union-attr]
            else:
                invocation.return_value(None)  # type: ignore[union-attr]
        except Exception as error:
            invocation.return_error_literal(  # type: ignore[union-attr]
                _Gio.dbus_error_quark(), _Gio.DBusError.FAILED, str(error))


ResultCallback = Callable[[bool, Any], None]

_CALL_TIMEOUT_MS = 25_000


def _is_missing_service(message: str) -> bool:
    lowered = (message or "").lower()
    return ("serviceunknown" in lowered or "namehasnoowner" in lowered
            or "activat" in lowered or "no such service" in lowered)


class BusClient:
    """UI-side proxy; ``bus_factory`` injects a fake bus in tests."""

    def __init__(self, bus_factory: Callable[[], object] | None = None) -> None:
        self.bus_factory = bus_factory
        self._signal_ids: list[int] = []

    def _bus(self) -> object:
        if self.bus_factory is not None:
            return self.bus_factory()
        Gio, _GLib = _gio()
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def _call(self, method: str, params: object,
              reply_type: object | None, on_done: ResultCallback) -> None:
        from gi.repository import GLib as _GLib

        try:
            bus = self._bus()
        except _GLib.Error as error:
            on_done(False, str(error))
            return

        def _on_reply(conn: object, result: object) -> None:
            try:
                reply = conn.call_finish(result)  # type: ignore[union-attr]
            except Exception as error:
                message = str(error)
                on_done(False, "service not running"
                        if _is_missing_service(message) else message)
                return
            try:
                out = reply.unpack()  # type: ignore[union-attr]
            except Exception as error:
                on_done(False, str(error))
                return
            on_done(True, out[0] if out else None)

        try:
            from gi.repository import Gio as _Gio
            bus.call(  # type: ignore[union-attr]
                BUS_NAME, OBJECT_PATH, INTERFACE, method, params,
                reply_type, _Gio.DBusCallFlags.NONE,
                _CALL_TIMEOUT_MS, None, _on_reply)
        except _GLib.Error as error:
            on_done(False, str(error))

    def set_visible(self, visible: bool, on_done: ResultCallback) -> None:
        from gi.repository import GLib as _GLib
        self._call("SetVisible", _GLib.Variant("(b)", (bool(visible),)),
                   None, lambda ok, _result: on_done(ok, None if ok else _result))

    def list_peers(self, on_done: ResultCallback) -> None:
        from gi.repository import GLib as _GLib

        def _got(ok: bool, result: object) -> None:
            if not ok:
                on_done(False, result)
                return
            try:
                peers = [peer_from_dict(item) for item in (result or [])]
            except BusError as error:
                on_done(False, str(error))
                return
            on_done(True, peers)

        self._call("ListPeers", None,
                   _GLib.VariantType.new("(aa{sv})"), _got)

    def send(self, fingerprint: str, files: list[str], pin: str,
             on_done: ResultCallback) -> None:
        from gi.repository import GLib as _GLib
        params = _GLib.Variant("(sass)", (
            str(fingerprint), [str(item) for item in files], str(pin or "")))
        self._call("Send", params, _GLib.VariantType.new("(s)"), on_done)

    def cancel(self, transfer_id: str, on_done: ResultCallback) -> None:
        from gi.repository import GLib as _GLib
        self._call("Cancel", _GLib.Variant("(s)", (str(transfer_id),)),
                   None, lambda ok, _result: on_done(ok, None if ok else _result))

    def subscribe(self, on_progress: Callable[[tuple], None],
                  on_incoming: Callable[[tuple], None],
                  on_done: Callable[[tuple], None] | None = None) -> bool:
        try:
            Gio, GLib = _gio()
            bus = self._bus()
        except ImportError:
            return False
        except GLib.Error:
            return False
        try:
            self._signal_ids.append(bus.signal_subscribe(  # type: ignore[union-attr]
                BUS_NAME, INTERFACE, "TransferProgress", OBJECT_PATH, None,
                Gio.DBusSignalFlags.NONE, self._wrap_signal(on_progress)))
            self._signal_ids.append(bus.signal_subscribe(  # type: ignore[union-attr]
                BUS_NAME, INTERFACE, "IncomingRequest", OBJECT_PATH, None,
                Gio.DBusSignalFlags.NONE, self._wrap_signal(on_incoming)))
            if on_done is not None:
                self._signal_ids.append(bus.signal_subscribe(  # type: ignore[union-attr]
                    BUS_NAME, INTERFACE, "TransferDone", OBJECT_PATH, None,
                    Gio.DBusSignalFlags.NONE, self._wrap_signal(on_done)))
        except GLib.Error:
            return False
        return True

    @staticmethod
    def _wrap_signal(callback: Callable[[tuple], None]) -> Callable:
        def _handler(_conn: object, _sender: object, _path: object,
                     _iface: object, _signal: object, params: object,
                     _user_data: object = None) -> None:
            try:
                callback(tuple(params.unpack()))  # type: ignore[union-attr]
            except Exception as error:
                logger.warning("bus signal callback failed: %s", error)

        return _handler
