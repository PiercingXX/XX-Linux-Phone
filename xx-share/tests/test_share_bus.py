"""Session-bus API on a fake bus: no display, no system bus needed."""
import pytest

from share_bus import (
    BUS_NAME,
    INTERFACE,
    INTROSPECTION_XML,
    OBJECT_PATH,
    BusClient,
    BusDispatcher,
    BusError,
    BusServer,
    incoming_args,
    peer_from_dict,
    peer_to_dict,
    progress_args,
)


def test_interface_identity():
    assert BUS_NAME == "io.piercingxx.XXShare1"
    assert OBJECT_PATH == "/io/piercingxx/XXShare"
    assert INTERFACE == BUS_NAME
    for token in ("SetVisible", "ListPeers", "Send", "Cancel",
                  "TransferProgress", "TransferDone", "IncomingRequest"):
        assert token in INTROSPECTION_XML


def test_peer_roundtrip():
    peer = peer_to_dict("phone", "ab" * 32, "10.0.0.5", "mobile", 53317)
    assert peer_from_dict(peer) == peer


def test_peer_rejects_garbage():
    for bad in ({}, {"alias": "", "fingerprint": "x", "addr": "y"},
                {"alias": "x", "fingerprint": "", "addr": "y"},
                {"alias": "x", "fingerprint": "y", "addr": ""},
                {"alias": "x", "fingerprint": "y", "addr": "z",
                 "port": True}):
        with pytest.raises(BusError):
            peer_from_dict(bad)


def test_peers_variant_unpacks_to_plain_dicts():
    from share_bus import peers_variant

    peer = peer_to_dict("you", "bb" * 32, "10.0.0.6", "mobile", 53317)
    value = peers_variant([peer])
    (peers,) = value.unpack()
    assert peers[0]["alias"] == "you"
    assert peers[0]["port"] == 53317


def test_signal_arg_shapes():
    assert progress_args("tx", "f1", 3, 9) == ("tx", "f1", 3, 9)
    assert incoming_args("s", "phone", "fp", ["a.jpg"], 10) == (
        "s", "phone", "fp", ["a.jpg"], 10)


class FakeBackend:
    def __init__(self):
        self.visible = False
        self.peers = [peer_to_dict("you", "bb" * 32, "10.0.0.6")]
        self.sent = []
        self.cancelled = []

    def set_visible(self, visible):
        self.visible = bool(visible)

    def list_peers(self):
        return list(self.peers)

    def start_send(self, fingerprint, files, pin):
        self.sent.append((fingerprint, files, pin))
        if fingerprint != "bb" * 32:
            raise ValueError("unknown device")
        return "tx-1"

    def cancel_send(self, transfer_id):
        self.cancelled.append(transfer_id)
        return transfer_id == "tx-1"


def test_dispatcher_routes_methods():
    backend = FakeBackend()
    dispatcher = BusDispatcher(backend)
    assert dispatcher.dispatch("SetVisible", (True,)) == ()
    assert backend.visible is True
    (peers,) = dispatcher.dispatch("ListPeers", ())
    assert peers[0]["alias"] == "you"
    (transfer_id,) = dispatcher.dispatch(
        "Send", ("bb" * 32, ["a.jpg"], ""))
    assert transfer_id == "tx-1"
    assert dispatcher.dispatch("Cancel", ("tx-1",)) == ()


def test_dispatcher_rejects_bad_calls():
    dispatcher = BusDispatcher(FakeBackend())
    with pytest.raises(BusError):
        dispatcher.dispatch("Send", ("", ["a.jpg"], ""))
    with pytest.raises(BusError):
        dispatcher.dispatch("Send", ("bb" * 32, [], ""))
    with pytest.raises(BusError):
        dispatcher.dispatch("Cancel", ("nope",))
    with pytest.raises(BusError):
        dispatcher.dispatch("Nope", ())


class FakeReply:
    def __init__(self, values):
        self._values = values

    def unpack(self):
        return self._values


class FakeBus:
    def __init__(self, replies=None, errors=None):
        self.replies = replies or {}
        self.errors = errors or {}
        self.calls = []
        self.subscriptions = []
        self.emitted = []

    def call(self, name, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback):
        args = params.unpack() if params is not None else ()
        self.calls.append({"method": method, "args": args})
        if method in self.errors:
            raise self.errors[method]
        callback(self, self.replies.get(method, ()))

    def call_finish(self, result):
        if isinstance(result, Exception):
            raise result
        return FakeReply(result)

    def signal_subscribe(self, *args):
        self.subscriptions.append(args)
        return len(self.subscriptions)

    def register_object(self, *args):
        return 1

    def emit_signal(self, *args):
        self.emitted.append(args)


class FakeParams:
    def __init__(self, values):
        self._values = values

    def unpack(self):
        return self._values


class FakeInvocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_error_literal(self, _quark, _code, message):
        self.error = message


def test_client_roundtrip_on_fake_bus():
    peer = peer_to_dict("you", "bb" * 32, "10.0.0.6")
    replies = {"ListPeers": ([peer],), "Send": ("tx-9",)}
    bus = FakeBus(replies=replies)
    client = BusClient(bus_factory=lambda: bus)
    done = []
    client.set_visible(True, lambda ok, res: done.append((ok, res)))
    assert done == [(True, None)]
    assert bus.calls[0]["method"] == "SetVisible"
    client.list_peers(lambda ok, res: done.append((ok, res)))
    assert done[1][0] is True
    assert done[1][1][0]["fingerprint"] == "bb" * 32
    client.send("bb" * 32, ["a.jpg"], "", lambda ok, res: done.append((ok, res)))
    assert done[2] == (True, "tx-9")
    sent = bus.calls[2]
    assert sent["args"][0] == "bb" * 32
    assert sent["args"][1] == ["a.jpg"]
    client.cancel("tx-9", lambda ok, res: done.append((ok, res)))
    assert done[3] == (True, None)


def test_client_reports_missing_service():
    from gi.repository import GLib

    bus = FakeBus(errors={"ListPeers": GLib.Error(
        "GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown: "
        "The name io.piercingxx.XXShare1 was not provided by any "
        ".service files")})
    client = BusClient(bus_factory=lambda: bus)
    done = []
    client.list_peers(lambda ok, res: done.append((ok, res)))
    assert done[0][0] is False


def test_client_subscribes_to_all_signals():
    bus = FakeBus()
    client = BusClient(bus_factory=lambda: bus)
    seen = []
    assert client.subscribe(seen.append, seen.append, seen.append) is True
    assert len(bus.subscriptions) == 3
    handler = bus.subscriptions[0][6]
    handler(None, None, None, None, "TransferProgress",
            FakeParams(("tx", "f1", 1, 2)))
    assert seen == [("tx", "f1", 1, 2)]


def test_server_dispatches_on_fake_bus():
    backend = FakeBackend()
    server = BusServer(BusDispatcher(backend))
    invocation = FakeInvocation()
    server._on_method_call(object(), "sender", OBJECT_PATH, INTERFACE,
                           "ListPeers", FakeParams(()), invocation)
    assert invocation.error is None
    assert invocation.value.unpack()[0][0]["alias"] == "you"
    invocation = FakeInvocation()
    server._on_method_call(object(), "sender", OBJECT_PATH, INTERFACE,
                           "SetVisible", FakeParams((True,)), invocation)
    assert invocation.error is None
    assert invocation.value is None
    assert backend.visible is True
    invocation = FakeInvocation()
    server._on_method_call(object(), "sender", OBJECT_PATH, INTERFACE,
                           "Nope", FakeParams(()), invocation)
    assert invocation.error is not None
    assert "UnknownMethod" in invocation.error


def test_server_send_reply_shape():
    backend = FakeBackend()
    server = BusServer(BusDispatcher(backend))
    invocation = FakeInvocation()
    server._on_method_call(object(), "sender", OBJECT_PATH, INTERFACE,
                           "Send", FakeParams(("bb" * 32, ["a.jpg"], "")),
                           invocation)
    assert invocation.error is None
    assert invocation.value.unpack() == ("tx-1",)


def test_server_emit_is_noop_without_connection():
    server = BusServer(BusDispatcher(FakeBackend()))
    server.emit("TransferProgress", ("tx", "f1", 1, 2))

    emitted = []
    server.connection = FakeBus()
    server.connection.emit_signal = lambda *args: emitted.append(args)
    server.emit("TransferProgress", ("tx", "f1", 1, 2))
    server.emit("IncomingRequest", ("s", "a", "f", ["x"], 1))
    server.emit("TransferDone", ("tx", True, "ok"))
    server.emit("Bogus", ())
    assert len(emitted) == 3
