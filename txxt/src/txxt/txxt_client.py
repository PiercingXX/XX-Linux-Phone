"""UI client for the daemon's ``io.piercingxx.TxxT1`` API.

The L4 UI never touches the modem stack and never writes the SQLite
store behind the daemon: outbound traffic goes through ``Send``,
read state through ``MarkRead``, and every refresh is driven by the
``MessagesChanged`` signal — no polling. Method shapes mirror the
exact introspection XML in :mod:`txxt.daemon` (``Send(s address,
s body) -> (x message_id)``; ``SendMms(as recipients, s text,
as file_paths) -> (x message_id)``; ``FetchMms(x message_id) ->
(s media_path)``; ``OpenThread(x)`` / ``MarkRead(x)``;
``MessagesChanged(as)``).

Every ``reply_type`` is a ``GLib.VariantType`` or None, never a
string — real PyGObject raises ``TypeError`` on a string. Calls are
async-only (``connection.call``, 2-arg callback); the GTK main loop
never blocks. Gio/GLib only: headless-testable.
"""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from txxt import DAEMON_BUS_NAME as TXXT_BUS_NAME  # noqa: E402
from txxt.daemon import TXXT_IFACE, TXXT_OBJECT_PATH  # noqa: E402

SEND_TIMEOUT_MS = 10_000
READ_TIMEOUT_MS = 5_000
MMS_TIMEOUT_MS = 30_000

REPLY_MESSAGE_ID = GLib.VariantType.new("(x)")
REPLY_MEDIA_PATH = GLib.VariantType.new("(s)")


def _call(
    connection, method: str, parameters: GLib.Variant,
    reply_type: GLib.VariantType | None, timeout_ms: int, callback,
) -> None:
    """One async method call; ``callback(conn, result)`` takes 2 args."""
    connection.call(
        TXXT_BUS_NAME, TXXT_OBJECT_PATH, TXXT_IFACE, method,
        parameters, reply_type, Gio.DBusCallFlags.NONE,
        timeout_ms, None, callback,
    )


class TxxtClient:
    """Async ``TxxT1`` caller bound to a live session-bus connection."""

    def __init__(self, connection) -> None:
        self._connection = connection

    def send(self, address: str, body: str, callback=None) -> None:
        """Send one SMS via the daemon; callback gets (message_id, error)."""
        def _done(conn, result) -> None:
            try:
                (message_id,) = conn.call_finish(result).unpack()
            except Exception as exc:
                if callback is not None:
                    callback(None, str(exc))
                return
            if callback is not None:
                callback(int(message_id), None)

        _call(
            self._connection, "Send",
            GLib.Variant("(ss)", (address, body)),
            REPLY_MESSAGE_ID, SEND_TIMEOUT_MS, _done,
        )

    def send_mms(
        self, recipients: list[str], text: str = "",
        file_paths: list[str] | None = None, callback=None,
    ) -> None:
        """Send one MMS via the daemon; callback gets (message_id, error)."""
        def _done(conn, result) -> None:
            try:
                (message_id,) = conn.call_finish(result).unpack()
            except Exception as exc:
                if callback is not None:
                    callback(None, str(exc))
                return
            if callback is not None:
                callback(int(message_id), None)

        _call(
            self._connection, "SendMms",
            GLib.Variant("(assas)", (
                [str(r) for r in (recipients or [])],
                str(text or ""),
                [str(p) for p in (file_paths or [])],
            )),
            REPLY_MESSAGE_ID, MMS_TIMEOUT_MS, _done,
        )

    def fetch_mms(self, message_id: int, callback=None) -> None:
        """Fetch one MMS photo via the daemon; callback gets (path, error)."""
        def _done(conn, result) -> None:
            try:
                (media_path,) = conn.call_finish(result).unpack()
            except Exception as exc:
                if callback is not None:
                    callback("", str(exc))
                return
            if callback is not None:
                callback(str(media_path or ""), None)

        _call(
            self._connection, "FetchMms",
            GLib.Variant("(x)", (int(message_id),)),
            REPLY_MEDIA_PATH, MMS_TIMEOUT_MS, _done,
        )

    def mark_read(self, conversation_id: int, callback=None) -> None:
        """Mark one thread read via the daemon; callback gets (ok, error)."""
        self._void_call(
            "MarkRead", GLib.Variant("(x)", (int(conversation_id),)),
            callback,
        )

    def open_thread(self, conversation_id: int, callback=None) -> None:
        """Ask the daemon to surface one thread; callback gets (ok, error)."""
        self._void_call(
            "OpenThread", GLib.Variant("(x)", (int(conversation_id),)),
            callback,
        )

    def _void_call(self, method: str, parameters: GLib.Variant, callback) -> None:
        def _done(conn, result) -> None:
            try:
                conn.call_finish(result).unpack()
            except Exception as exc:
                if callback is not None:
                    callback(False, str(exc))
                return
            if callback is not None:
                callback(True, None)

        _call(
            self._connection, method, parameters,
            None, READ_TIMEOUT_MS, _done,
        )

    def subscribe_messages_changed(self, on_changed) -> int | None:
        """Refresh hook: ``on_changed(ids)`` per ``MessagesChanged``.

        The 7-arg D-Bus handler shape unwraps the ``(as)`` params and
        forwards the id list; unparseable signals are dropped.
        """
        def _handler(conn, sender, path, iface, signal, params, user_data):
            try:
                (ids,) = params.unpack()
            except Exception:
                return
            try:
                on_changed([str(cid) for cid in ids])
            except Exception:
                pass

        try:
            return self._connection.signal_subscribe(
                None, TXXT_IFACE, "MessagesChanged", TXXT_OBJECT_PATH,
                None, Gio.DBusSignalFlags.NONE, _handler, None,
            )
        except Exception:
            return None
