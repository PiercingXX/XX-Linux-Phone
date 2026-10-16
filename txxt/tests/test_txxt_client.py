"""UI client tests: ``TxxtClient`` over fakes, no bus needed.

The fakes mirror the real Gio shapes: ``connection.call`` takes a
2-arg callback (no trailing user data) and ``call_finish`` unpacks
the reply; ``signal_subscribe`` handlers take 7 args.
"""

import inspect

from gi.repository import GLib

from txxt import txxt_client
from txxt.daemon import TXXT_IFACE, TXXT_OBJECT_PATH
from txxt.txxt_client import TxxtClient


class FakeReply:
    def __init__(self, values=None, error=None):
        self._values = values
        self._error = error

    def unpack(self):
        if self._error is not None:
            raise self._error
        return self._values


class FakeConnection:
    def __init__(self, replies=None):
        self.calls = []
        self.subscriptions = []
        self._replies = list(replies or [])

    def call(self, bus, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback):
        self.calls.append({
            "bus": bus, "path": path, "iface": iface, "method": method,
            "params": params, "reply_type": reply_type,
            "timeout": timeout, "cancellable": cancellable,
            "callback": callback,
        })
        reply = self._replies.pop(0) if self._replies else FakeReply(())
        callback(self, reply)

    def call_finish(self, reply):
        return reply

    def signal_subscribe(self, sender, iface, member, path, arg0,
                         flags, handler, user_data):
        self.subscriptions.append({
            "sender": sender, "iface": iface, "member": member,
            "path": path, "handler": handler, "user_data": user_data,
        })
        return 9


class FakeParams:
    def __init__(self, values=None, error=None):
        self._values = values
        self._error = error

    def unpack(self):
        if self._error is not None:
            raise self._error
        return self._values


def last_call(conn):
    assert conn.calls, "client made no D-Bus call"
    return conn.calls[-1]


def test_send_names_bus_path_iface_and_variant_reply():
    conn = FakeConnection([FakeReply((4242,))])
    seen = []
    TxxtClient(conn).send("+1555", "hello", lambda mid, err: seen.append((mid, err)))
    call = last_call(conn)
    assert (call["bus"], call["path"], call["iface"], call["method"]) == (
        "io.piercingxx.TxxT.Daemon", TXXT_OBJECT_PATH, TXXT_IFACE, "Send")
    assert call["params"].unpack() == ("+1555", "hello")
    assert isinstance(call["reply_type"], GLib.VariantType)
    assert call["cancellable"] is None
    assert len(inspect.signature(call["callback"]).parameters) == 2
    assert seen == [(4242, None)]


def test_send_error_surfaces_message_id_none():
    conn = FakeConnection([FakeReply(error=RuntimeError("down"))])
    seen = []
    TxxtClient(conn).send("+1555", "hi", lambda mid, err: seen.append((mid, err)))
    assert seen[0][0] is None and seen[0][1]


def test_mark_read_and_open_thread_use_no_reply_type():
    conn = FakeConnection([FakeReply(()), FakeReply(())])
    seen = []
    client = TxxtClient(conn)
    client.mark_read(7, lambda ok, err: seen.append((ok, err)))
    client.open_thread(7, lambda ok, err: seen.append((ok, err)))
    assert [c["method"] for c in conn.calls] == ["MarkRead", "OpenThread"]
    for call in conn.calls:
        assert call["reply_type"] is None
        assert call["params"].unpack() == (7,)
    assert seen == [(True, None), (True, None)]


def test_void_error_reports_false():
    conn = FakeConnection([FakeReply(error=RuntimeError("gone"))])
    seen = []
    TxxtClient(conn).mark_read(7, lambda ok, err: seen.append((ok, err)))
    assert seen[0][0] is False and seen[0][1]


def test_subscribe_shape_and_dispatch():
    conn = FakeConnection()
    seen = []
    sub = TxxtClient(conn).subscribe_messages_changed(seen.append)
    assert sub == 9
    only = conn.subscriptions[0]
    assert only["sender"] is None
    assert (only["iface"], only["member"]) == (TXXT_IFACE, "MessagesChanged")
    assert only["path"] == TXXT_OBJECT_PATH
    assert len(inspect.signature(only["handler"]).parameters) == 7
    only["handler"](None, None, None, None, None,
                    FakeParams((["3", "4"],)), None)
    assert seen == [["3", "4"]]
    only["handler"](None, None, None, None, None,
                    FakeParams(error=ValueError("x")), None)
    assert seen == [["3", "4"]]


def test_client_targets_daemon_name():
    assert txxt_client.TXXT_BUS_NAME == "io.piercingxx.TxxT.Daemon"


def test_send_mms_names_method_and_variant_reply():
    conn = FakeConnection([FakeReply((777,))])
    seen = []
    TxxtClient(conn).send_mms(
        ["+1555"], "look", ["/tmp/p.jpg"],
        lambda mid, err: seen.append((mid, err)))
    call = last_call(conn)
    assert (call["bus"], call["path"], call["iface"], call["method"]) == (
        "io.piercingxx.TxxT.Daemon", TXXT_OBJECT_PATH, TXXT_IFACE, "SendMms")
    assert call["params"].unpack() == (["+1555"], "look", ["/tmp/p.jpg"])
    assert isinstance(call["reply_type"], GLib.VariantType)
    assert call["reply_type"].dup_string() == "(x)"
    assert call["cancellable"] is None
    assert len(inspect.signature(call["callback"]).parameters) == 2
    assert seen == [(777, None)]


def test_send_mms_error_surfaces_none():
    conn = FakeConnection([FakeReply(error=RuntimeError("down"))])
    seen = []
    TxxtClient(conn).send_mms(
        ["+1555"], "hi", [], lambda mid, err: seen.append((mid, err)))
    assert seen[0][0] is None and seen[0][1]


def test_fetch_mms_returns_path_with_s_reply():
    conn = FakeConnection([FakeReply(("/data/mms/7/photo-0.jpg",))])
    seen = []
    TxxtClient(conn).fetch_mms(7, lambda path, err: seen.append((path, err)))
    call = last_call(conn)
    assert call["method"] == "FetchMms"
    assert call["params"].unpack() == (7,)
    assert isinstance(call["reply_type"], GLib.VariantType)
    assert call["reply_type"].dup_string() == "(s)"
    assert seen == [("/data/mms/7/photo-0.jpg", None)]


def test_fetch_mms_error_surfaces_empty_path():
    conn = FakeConnection([FakeReply(error=RuntimeError("gone"))])
    seen = []
    TxxtClient(conn).fetch_mms(7, lambda path, err: seen.append((path, err)))
    assert seen[0][0] == "" and seen[0][1]
