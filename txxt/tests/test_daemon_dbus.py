"""L3 daemon bus API tests: ``io.piercingxx.TxxT1`` over fakes plus a
real private-bus round trip.

The fakes mirror the real Gio shapes (tuple ``GLib.Variant`` params,
``invocation.return_value`` / ``return_dbus_error``); the round trip at
the bottom drives ``Send``/``MarkRead``/``OpenThread`` through a real
bus provided by ``dbus-run-session``.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from gi.repository import Gio

from txxt import daemon
from txxt.daemon import (
    ERROR_INVALID_ARGUMENT,
    INTROSPECTION_XML,
    TXXT_IFACE,
    TXXT_OBJECT_PATH,
    Daemon,
    InboundPipeline,
)
from txxt.store import Store

ROOT = Path(__file__).resolve().parent.parent


class FakeInvocation:
    def __init__(self):
        self.reply = None
        self.error = None

    def return_value(self, parameters):
        self.reply = parameters

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class FakeConnection:
    def __init__(self):
        self.objects = []
        self.signals = []

    def register_object(self, path, iface, handler):
        self.objects.append((path, iface, handler))
        return 1

    def emit_signal(self, destination, path, interface, signal, parameters):
        self.signals.append((destination, path, interface, signal, parameters))
        return True


class FakeParams:
    def __init__(self, values):
        self._values = tuple(values)

    def unpack(self):
        return self._values


class InlineExecutor:
    def submit(self, fn, *args):
        fn(*args)


def immediate_idle(fn, *args):
    fn(*args)
    return 1


def make_daemon(store, **kwargs):
    kwargs.setdefault("system_bus_fn", lambda: None)
    kwargs.setdefault("session_bus_fn", lambda: None)
    kwargs.setdefault("idle_add", immediate_idle)
    d = Daemon(store, **kwargs)
    d._executor = InlineExecutor()
    return d


class TestIntrospection:
    def test_xml_names_full_interface(self):
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        assert len(node.interfaces) == 1
        iface = node.interfaces[0]
        assert iface.name == TXXT_IFACE == "io.piercingxx.TxxT1"
        methods = {m.name: m for m in iface.methods}
        assert set(methods) == {
            "Send", "SendMms", "FetchMms", "OpenThread", "MarkRead",
            "HandleNotificationAction",
        }
        assert [a.signature for a in methods["Send"].in_args] == ["s", "s"]
        assert [a.signature for a in methods["Send"].out_args] == ["x"]
        assert [a.signature for a in methods["SendMms"].in_args] == ["as", "s", "as"]
        assert [a.signature for a in methods["SendMms"].out_args] == ["x"]
        assert [a.signature for a in methods["FetchMms"].in_args] == ["x"]
        assert [a.signature for a in methods["FetchMms"].out_args] == ["s"]
        assert [a.signature for a in methods["OpenThread"].in_args] == ["x"]
        assert [a.signature for a in methods["MarkRead"].in_args] == ["x"]
        action = methods["HandleNotificationAction"]
        assert [a.signature for a in action.in_args] == ["u", "s"]
        assert [a.signature for a in action.out_args] == []
        signals = {s.name: s for s in iface.signals}
        assert set(signals) == {"MessagesChanged"}
        assert [a.signature for a in signals["MessagesChanged"].args] == ["as"]

    def test_object_path(self):
        assert TXXT_OBJECT_PATH == "/io/piercingxx/TxxT/Daemon"

    def test_register_object_uses_path_and_iface(self):
        d = make_daemon(Store.memory())
        conn = FakeConnection()
        assert d.register_on(conn) == 1
        assert d._dbus_connection is conn
        path, iface, handler = conn.objects[0]
        assert path == TXXT_OBJECT_PATH
        assert iface.name == TXXT_IFACE


class TestSendMessage:
    def test_failure_enters_retry_queue(self):
        store = Store.memory()
        d = make_daemon(store, sender=lambda b, m, a, t: (False, "no signal"))
        message_id = d.send_message("+1555", "hello")
        row = store.get_message(message_id)
        assert row is not None and row.direction == "OUTGOING"
        assert row.sent is False
        assert len(d._retry) == 1
        pending = store.pending_outgoing_sms()
        assert [r.id for r in pending] == [message_id]

    def test_success_marks_sent_and_clears_queue(self):
        store = Store.memory()
        deleted = []
        bus = FakeDeleteBus(deleted)
        d = make_daemon(
            store,
            system_bus_fn=lambda: bus,
            sender=lambda b, m, a, t: (True, "/s/9"),
        )
        d._modem_path = "/modem/0"
        message_id = d.send_message("+1555", "hello")
        assert store.get_message(message_id).sent is True
        assert len(d._retry) == 0
        assert deleted == ["/s/9"]

    def test_no_modem_queues_for_retry(self):
        store = Store.memory()
        d = make_daemon(store, sender=_explode_sender)
        message_id = d.send_message("+1555", "hello")
        assert len(d._retry) == 1
        assert store.pending_outgoing_sms()[0].id == message_id

    def test_empty_address_or_body_is_invalid(self):
        d = make_daemon(Store.memory())
        with pytest.raises(ValueError):
            d.send_message("", "hi")
        with pytest.raises(ValueError):
            d.send_message("+1555", "  ")


def _explode_sender(bus, modem, address, body):
    raise AssertionError("sender must not run without a modem")


class FakeDeleteBus:
    def __init__(self, deleted):
        self._deleted = deleted

    def call_sync(self, bus, path, iface, method, params, *rest):
        if method == "GetManagedObjects":
            return _TupleReply(({
                "/modem/0": {
                    "org.freedesktop.ModemManager1.Modem": {},
                    "org.freedesktop.ModemManager1.Modem.Messaging": {},
                },
            },))
        if method == "Delete":
            self._deleted.append(params.unpack()[0])
            return _TupleReply(())
        raise AssertionError(method)


class _TupleReply:
    def __init__(self, values):
        self._values = tuple(values)

    def unpack(self):
        return self._values


class TestMethodDispatch:
    def test_send_replies_message_id_without_blocking(self):
        store = Store.memory()
        d = make_daemon(store, sender=lambda b, m, a, t: (False, "down"))
        invocation = FakeInvocation()
        d.handle_method_call(
            None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "Send",
            FakeParams(("+1555", "hello via bus")), invocation,
        )
        assert invocation.error is None
        (message_id,) = invocation.reply.unpack()
        assert store.get_message(int(message_id)).body == "hello via bus"
        assert len(d._retry) == 1

    def test_send_handler_returns_before_worker_replies(self):
        store = Store.memory()
        d = Daemon(
            store,
            system_bus_fn=lambda: None,
            session_bus_fn=lambda: None,
            sender=lambda b, m, a, t: (False, "down"),
        )
        invocation = FakeInvocation()
        d.handle_method_call(
            None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "Send",
            FakeParams(("+1555", "hi")), invocation,
        )
        d._executor.shutdown(wait=True)
        # No main loop runs here, so the idle_add reply is still pending —
        # the handler itself never blocked on the send.
        assert invocation.reply is None and invocation.error is None
        assert len(d._retry) == 1

    def test_send_empty_body_replies_invalid_argument(self):
        d = make_daemon(Store.memory())
        invocation = FakeInvocation()
        d.handle_method_call(
            None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "Send",
            FakeParams(("+1555", "  ")), invocation,
        )
        assert invocation.reply is None
        assert invocation.error[0] == ERROR_INVALID_ARGUMENT

    def test_mark_read_marks_and_replies(self):
        store = Store.memory()
        inbound_id = store.persist_inbound_sms("+1555", "hi", 1717243200000)
        row = store.get_message(inbound_id)
        d = make_daemon(store)
        conn = FakeConnection()
        d.register_on(conn)
        invocation = FakeInvocation()
        d.handle_method_call(
            None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "MarkRead",
            FakeParams((row.conversation_id,)), invocation,
        )
        assert invocation.error is None
        assert invocation.reply.unpack() == ()
        assert store.get_message(inbound_id).is_read is True
        assert conn.signals and conn.signals[0][3] == "MessagesChanged"
        assert conn.signals[0][4].unpack() == ([str(row.conversation_id)],)

    def test_open_thread_replies_and_opens(self, monkeypatch):
        d = make_daemon(Store.memory())
        opened = []
        monkeypatch.setattr(daemon, "open_thread", lambda cid: opened.append(cid))
        invocation = FakeInvocation()
        d.handle_method_call(
            None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "OpenThread",
            FakeParams((4242,)), invocation,
        )
        assert invocation.error is None
        assert invocation.reply.unpack() == ()
        assert opened == [4242]

    def test_unknown_method_is_invalid_argument(self):
        d = make_daemon(Store.memory())
        invocation = FakeInvocation()
        d.handle_method_call(
            None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "HangUp",
            FakeParams(()), invocation,
        )
        assert invocation.reply is None
        assert invocation.error[0] == ERROR_INVALID_ARGUMENT

    def test_bad_params_are_invalid_argument(self):
        d = make_daemon(Store.memory())
        invocation = FakeInvocation()
        d.handle_method_call(
            None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "MarkRead",
            FakeParams(()), invocation,
        )
        assert invocation.reply is None
        assert invocation.error[0] == ERROR_INVALID_ARGUMENT


class TestNotificationAction:
    def _posted(self, store, notification_id=77, conversation_id=4242):
        d = make_daemon(store)
        d._pipeline._notification_map[notification_id] = conversation_id
        return d

    def test_posted_id_opens_thread(self, monkeypatch):
        d = self._posted(Store.memory())
        opened = []
        monkeypatch.setattr(daemon, "open_thread", lambda cid: opened.append(cid))
        assert d.handle_notification_action(77, "reply") is True
        assert opened == [4242]

    def test_open_key_opens_thread(self, monkeypatch):
        d = self._posted(Store.memory())
        opened = []
        monkeypatch.setattr(daemon, "open_thread", lambda cid: opened.append(cid))
        assert d.handle_notification_action(77, "open") is True
        assert opened == [4242]

    def test_unknown_id_is_silent_noop(self, monkeypatch):
        d = self._posted(Store.memory())
        opened = []
        monkeypatch.setattr(daemon, "open_thread", lambda cid: opened.append(cid))
        assert d.handle_notification_action(999, "reply") is False
        assert opened == []

    def test_unknown_key_is_silent_noop(self, monkeypatch):
        d = self._posted(Store.memory())
        opened = []
        monkeypatch.setattr(daemon, "open_thread", lambda cid: opened.append(cid))
        assert d.handle_notification_action(77, "call-back") is False
        assert opened == []

    def test_dispatch_replies_empty_tuple(self, monkeypatch):
        d = self._posted(Store.memory())
        opened = []
        monkeypatch.setattr(daemon, "open_thread", lambda cid: opened.append(cid))
        invocation = FakeInvocation()
        d.handle_method_call(
            None, None, TXXT_OBJECT_PATH, TXXT_IFACE,
            "HandleNotificationAction", FakeParams((77, "reply")), invocation,
        )
        assert invocation.error is None
        assert invocation.reply.unpack() == ()
        assert opened == [4242]

    def test_dispatch_foreign_id_still_replies(self, monkeypatch):
        d = self._posted(Store.memory())
        opened = []
        monkeypatch.setattr(daemon, "open_thread", lambda cid: opened.append(cid))
        invocation = FakeInvocation()
        d.handle_method_call(
            None, None, TXXT_OBJECT_PATH, TXXT_IFACE,
            "HandleNotificationAction", FakeParams((999, "reply")), invocation,
        )
        assert invocation.error is None
        assert invocation.reply.unpack() == ()
        assert opened == []


class TestMessagesChanged:
    def test_emit_shape_is_as_tuple(self):
        d = make_daemon(Store.memory())
        conn = FakeConnection()
        d.register_on(conn)
        d._emit_messages_changed(["7", "9"])
        dest, path, iface, signal, params = conn.signals[0]
        assert (dest, path, iface, signal) == (
            None, TXXT_OBJECT_PATH, TXXT_IFACE, "MessagesChanged",
        )
        assert params.unpack() == (["7", "9"],)

    def test_no_connection_no_emit_no_crash(self):
        make_daemon(Store.memory())._emit_messages_changed(["1"])

    def test_inbound_store_change_emits_thread(self):
        store = Store.memory()
        d = make_daemon(store, pipeline=InboundPipeline(store))
        conn = FakeConnection()
        d.register_on(conn)

        dynamics = {"Number": "tel:+15550002222", "Text": "live",
                    "State": 3, "PduType": 1,
                    "Timestamp": "2024-06-01T12:00:00+00:00"}

        class Bus:
            def call_sync(self, bus, path, iface, method, params, *rest):
                if method == "GetAll":
                    return _TupleReply((dict(dynamics),))
                if method == "GetManagedObjects":
                    return _TupleReply(({},))
                if method == "Delete":
                    return _TupleReply(())
                raise AssertionError(method)

        d._system_bus_fn = lambda: Bus()
        d._fetch_and_handle("/s/live")
        assert conn.signals and conn.signals[0][3] == "MessagesChanged"


_HELPER = r'''
import sys

txxt_src, db_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, txxt_src)

from gi.repository import GLib, Gio

from txxt import daemon as daemon_mod
from txxt.store import Store

BUS = "io.piercingxx.TxxT.Daemon"
PATH = "/io/piercingxx/TxxT/Daemon"
IFACE = "io.piercingxx.TxxT1"

store = Store(db_path)
ADDRESS = "+15550100200"
store.persist_inbound_sms(ADDRESS, "earlier", 1717243200000)

sent = []

def failing_sender(bus, modem, address, body):
    sent.append((address, body))
    return False, "no modem in test"

class _Reply:
    def __init__(self, values):
        self._values = tuple(values)

    def unpack(self):
        return self._values

class _FakeSystemBus:
    def call_sync(self, busname, path, iface, method, params, *rest):
        assert method == "GetManagedObjects", method
        return _Reply(({
            "/modem/0": {"org.freedesktop.ModemManager1.Modem": {},
                         "org.freedesktop.ModemManager1.Modem.Messaging": {}},
        },))

d = daemon_mod.Daemon(
    store, sender=failing_sender,
    system_bus_fn=_FakeSystemBus, session_bus_fn=lambda: None)
loop = GLib.MainLoop()
state = {}
seen = []

conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

def on_bus_acquired(connection, name):
    d.register_on(connection)

daemon_mod.own_bus_name(on_bus_acquired)

def on_signal(c, sender, path, iface, signal, params, user_data):
    seen.append(params.unpack())

conn.signal_subscribe(
    BUS, IFACE, "MessagesChanged", PATH, None,
    Gio.DBusSignalFlags.NONE, on_signal, None)

GLib.timeout_add(20000, loop.quit)

def fail(message):
    state["error"] = message
    loop.quit()

def on_open(c2, result):
    try:
        c2.call_finish(result).unpack()
    except Exception as exc:
        fail(f"OpenThread failed: {exc}")
        return
    GLib.timeout_add(2000, loop.quit)

def on_mark_read(c2, result):
    try:
        c2.call_finish(result).unpack()
    except Exception as exc:
        fail(f"MarkRead failed: {exc}")
        return
    rows = [item for conv in store.get_all_conversations()
            for item in store.get_messages_for_conversation(conv.id)]
    if not all(item.is_read for item in rows if item.direction == "INCOMING"):
        fail("MarkRead did not mark the thread read")
        return
    c2.call(BUS, PATH, IFACE, "OpenThread",
             GLib.Variant("(x)", (state["conversation_id"],)),
             None, Gio.DBusCallFlags.NONE, 5000, None, on_open)

def on_send(c2, result):
    try:
        (message_id,) = c2.call_finish(result).unpack()
    except Exception as exc:
        fail(f"Send failed: {exc}")
        return
    state["message_id"] = int(message_id)
    row = store.get_message(int(message_id))
    if row is None or row.direction != "OUTGOING":
        fail("Send did not persist the outgoing row")
        return
    state["conversation_id"] = row.conversation_id
    c2.call(BUS, PATH, IFACE, "MarkRead",
             GLib.Variant("(x)", (row.conversation_id,)),
             None, Gio.DBusCallFlags.NONE, 5000, None, on_mark_read)

def start():
    conn.call(BUS, PATH, IFACE, "Send",
              GLib.Variant("(ss)", (ADDRESS, "hello from the UI")),
              None, Gio.DBusCallFlags.NONE, 5000, None, on_send)
    return False

GLib.idle_add(start)
loop.run()

assert "error" not in state, state["error"]
message_id = state["message_id"]
row = store.get_message(message_id)
assert row is not None and row.body == "hello from the UI"
assert row.sent is False, "failing sender must leave the row unsent"
assert sent == [(ADDRESS, "hello from the UI")], sent
assert [r.id for r in store.pending_outgoing_sms()] == [message_id]
assert len(d._retry) == 1, "failed UI send never reached the retry queue"
assert any(str(state["conversation_id"]) in uids
           for (uids,) in seen), \
    f"MessagesChanged never named the thread: {seen!r}"
print("DAEMON-BUS-OK")
'''


def test_send_through_real_bus_lands_in_store_and_retry(tmp_path):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is not installed")
    helper = tmp_path / "daemon_bus_helper.py"
    helper.write_text(_HELPER)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(helper),
         str(ROOT / "src"), str(tmp_path / "txxt.db")],
        env=env, capture_output=True, text=True, timeout=120)
    assert "DAEMON-BUS-OK" in proc.stdout, (
        f"helper failed (rc={proc.returncode}):\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
    assert proc.returncode == 0
