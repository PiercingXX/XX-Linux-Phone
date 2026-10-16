import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from discovery import Discovery, http_register
from protocol import DeviceInfo, decode_announce


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.inbox = []
        self.closed = False
        self.options = []

    def bind(self, addr):
        pass

    def setsockopt(self, *args):
        self.options.append(args)

    def settimeout(self, value):
        pass

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def recvfrom(self, size):
        raise OSError("closed")

    def close(self):
        self.closed = True


def _info(alias, fingerprint, **overrides):
    return DeviceInfo(alias=alias, fingerprint=fingerprint, **overrides)


def test_announce_true_registers_over_http_first():
    sock = FakeSocket()
    calls = []
    local = _info("me", "aa")
    remote = _info("you", "bb", port=54321, protocol="https")
    disc = Discovery(local, socket_factory=lambda: sock,
                     register_fn=lambda *args: calls.append(args))
    disc._sock = sock
    from protocol import encode_announce

    disc.handle_datagram(encode_announce(remote, True), ("10.0.0.5", 53317))
    assert disc.peers["bb"].addr == "10.0.0.5"
    assert calls == [("10.0.0.5", 54321, "https", local)]
    assert sock.sent == []


def test_announce_true_falls_back_to_multicast():
    sock = FakeSocket()

    def fail(*args):
        raise OSError("no route")

    local = _info("me", "aa")
    remote = _info("you", "bb")
    disc = Discovery(local, socket_factory=lambda: sock, register_fn=fail)
    disc._sock = sock
    from protocol import encode_announce

    disc.handle_datagram(encode_announce(remote, True), ("10.0.0.5", 53317))
    assert disc.peers["bb"].addr == "10.0.0.5"
    replies = [decode_announce(data) for data, _ in sock.sent]
    assert replies == [(local, False)]
    assert sock.sent[0][1] == ("10.0.0.5", 53317)


def test_announce_false_never_replies():
    sock = FakeSocket()
    calls = []
    disc = Discovery(_info("me", "aa"), socket_factory=lambda: sock,
                     register_fn=lambda *args: calls.append(args))
    disc._sock = sock
    from protocol import encode_announce

    disc.handle_datagram(encode_announce(_info("you", "bb"), False),
                         ("10.0.0.6", 53317))
    assert "bb" in disc.peers
    assert calls == []
    assert sock.sent == []


def test_http_register_posts_to_announcer():
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            received["path"] = self.path
            received["body"] = json.loads(self.rfile.read(length).decode())
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        local = _info("me", "aa")
        http_register("127.0.0.1", port, "http", local)
        assert received["path"] == "/api/localsend/v2/register"
        assert received["body"]["alias"] == "me"
        assert received["body"]["port"] == 53317
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_ignores_self_and_garbage():
    sock = FakeSocket()
    disc = Discovery(_info("me", "aa"), socket_factory=lambda: sock)
    disc._sock = sock
    from protocol import encode_announce

    disc.handle_datagram(encode_announce(_info("me", "aa"), True),
                         ("10.0.0.5", 53317))
    disc.handle_datagram(b"garbage", ("10.0.0.5", 53317))
    assert disc.peers == {}
    assert sock.sent == []


def test_peers_expire():
    now = [1000.0]
    sock = FakeSocket()
    disc = Discovery(_info("me", "aa"), socket_factory=lambda: sock,
                     clock=lambda: now[0])
    disc._sock = sock
    from protocol import encode_announce

    disc.handle_datagram(encode_announce(_info("you", "bb"), False),
                         ("10.0.0.6", 53317))
    assert len(disc.live_peers()) == 1
    now[0] += 600.0
    assert disc.live_peers() == []


def test_two_instances_hear_each_other_on_one_host():
    import socket as _socket
    import time as _time

    first = Discovery(_info("first", "aa" * 32),
                      register_fn=lambda *args: None)
    second = Discovery(_info("second", "bb" * 32),
                       register_fn=lambda *args: None)
    for disc in (first, second):
        try:
            disc.start()
        except OSError:
            first.stop()
            second.stop()
            import pytest as _pytest

            _pytest.skip("multicast unavailable on this host")
    try:
        assert first._sock is not None and second._sock is not None
        for sock in (first._sock, second._sock):
            assert sock.getsockopt(
                _socket.SOL_SOCKET, _socket.SO_REUSEADDR) != 0
            reuse_port = getattr(_socket, "SO_REUSEPORT", None)
            if reuse_port is not None:
                try:
                    assert sock.getsockopt(
                        _socket.SOL_SOCKET, reuse_port) != 0
                except OSError:
                    pass
            assert sock.getsockopt(
                _socket.IPPROTO_IP, _socket.IP_MULTICAST_LOOP) != 0
        first.announce()
        second.announce()
        end = _time.monotonic() + 5.0
        while _time.monotonic() < end:
            if ("bb" * 32 in first.peers
                    and "aa" * 32 in second.peers):
                break
            _time.sleep(0.05)
            first.announce()
            second.announce()
        assert "bb" * 32 in first.peers
        assert "aa" * 32 in second.peers
    finally:
        first.stop()
        second.stop()
