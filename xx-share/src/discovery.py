"""UDP multicast discovery: announce, listen, register fallback, scan.

The socket factory and clock are injectable so headless tests drive the
responder logic with fakes. Network threads are daemon threads; nothing
here touches GTK.
"""
from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable

from protocol import (
    DEFAULT_PORT,
    MULTICAST_GROUP,
    MULTICAST_PORT,
    PATH_REGISTER,
    DeviceInfo,
    decode_announce,
    encode_announce,
)

PEER_TTL_SECONDS = 120.0
ANNOUNCE_INTERVAL_SECONDS = 20.0
REGISTER_HTTP_TIMEOUT = 4.0

logger = logging.getLogger(__name__)


def http_register(
    host: str,
    port: int,
    protocol: str,
    own: DeviceInfo,
    timeout: float = REGISTER_HTTP_TIMEOUT,
    cert_path: str | None = None,
    key_path: str | None = None,
) -> DeviceInfo | None:
    import http.client
    import json as _json
    import ssl as _ssl
    from pathlib import Path as _Path

    body = _json.dumps(own.to_dict()).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Content-Length": str(len(body))}
    if protocol == "http":
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    else:
        context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = _ssl.CERT_NONE
        if cert_path is not None and key_path is not None:
            context.load_cert_chain(str(_Path(cert_path)),
                                    str(_Path(key_path)))
        conn = http.client.HTTPSConnection(host, port, timeout=timeout,
                                           context=context)
    try:
        conn.request("POST", PATH_REGISTER, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        if response.status != 200:
            raise OSError(f"register refused with HTTP {response.status}")
    finally:
        conn.close()
    try:
        answered = _json.loads(raw.decode("utf-8"))
        info = DeviceInfo.from_dict(answered)
    except (ValueError, UnicodeDecodeError):
        return None
    from dataclasses import replace as _replace

    return _replace(info, port=int(port), protocol=str(protocol))


@dataclass
class Peer:
    info: DeviceInfo
    addr: str
    last_seen: float


SocketFactory = Callable[[], socket.socket]


def _default_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    reuse_port = getattr(socket, "SO_REUSEPORT", None)
    if reuse_port is not None:
        try:
            sock.setsockopt(socket.SOL_SOCKET, reuse_port, 1)
        except OSError:
            pass
    return sock


class Discovery:
    def __init__(
        self,
        info: DeviceInfo,
        bind_addrs: list[str] | None = None,
        socket_factory: SocketFactory | None = None,
        clock: Callable[[], float] | None = None,
        on_peers: Callable[[list[Peer]], None] | None = None,
        register_fn: Callable[
            [str, int, str, DeviceInfo], DeviceInfo | None] | None = None,
        cert_path: str | None = None,
        key_path: str | None = None,
    ) -> None:
        self.info = info
        self.bind_addrs = list(bind_addrs or [])
        self.socket_factory = socket_factory or _default_socket
        self.clock = clock or time.monotonic
        self.on_peers = on_peers or (lambda _peers: None)
        self.cert_path = cert_path
        self.key_path = key_path
        if register_fn is not None:
            self.register_fn = register_fn
        else:
            def _default_register(host: str, port: int, protocol: str,
                                  own: DeviceInfo) -> DeviceInfo | None:
                return http_register(host, port, protocol, own,
                                     cert_path=cert_path, key_path=key_path)

            self.register_fn = _default_register
        self.peers: dict[str, Peer] = {}
        self.lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        sock = self.socket_factory()
        self._sock = sock
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            pass
        reuse_port = getattr(socket, "SO_REUSEPORT", None)
        if reuse_port is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, reuse_port, 1)
            except OSError:
                pass
        try:
            sock.bind(("", MULTICAST_PORT))
        except OSError:
            pass
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        except OSError:
            pass
        joined: set[str] = set()
        candidates = list(self.bind_addrs)
        try:
            from netif import allowed_bind_addrs as _allowed

            for found in _allowed(include_loopback=True):
                if found not in candidates:
                    candidates.append(found)
        except (OSError, ValueError):
            pass
        for addr in candidates:
            if addr in joined:
                continue
            joined.add(addr)
            try:
                mreq = struct.pack("4s4s", socket.inet_aton(MULTICAST_GROUP),
                                   socket.inet_aton(addr))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError:
                continue
        try:
            mreq_any = struct.pack("4s4s", socket.inet_aton(MULTICAST_GROUP),
                                   socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq_any)
        except OSError:
            pass
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.announce()

    def stop(self) -> None:
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)

    def announce(self) -> None:
        if self._sock is None:
            return
        payload = encode_announce(self.info, True)
        try:
            self._sock.sendto(payload, (MULTICAST_GROUP, MULTICAST_PORT))
        except OSError:
            pass

    def note_http_peer(self, info: DeviceInfo, addr: str) -> None:
        if info.fingerprint == self.info.fingerprint:
            return
        host = addr or ""
        with self.lock:
            self.peers[info.fingerprint] = Peer(info=info, addr=host,
                                                last_seen=self.clock())
            peers = list(self.peers.values())
        self.on_peers(peers)

    def handle_datagram(self, data: bytes, addr: tuple) -> None:
        try:
            info, announce = decode_announce(data)
        except ValueError:
            return
        if info.fingerprint == self.info.fingerprint:
            return
        host = addr[0] if addr else ""
        with self.lock:
            self.peers[info.fingerprint] = Peer(info=info, addr=host,
                                                last_seen=self.clock())
            peers = list(self.peers.values())
        self.on_peers(peers)
        if announce and self._sock is not None:
            try:
                answered = self.register_fn(host, info.port,
                                            info.protocol, self.info)
            except Exception as error:
                logger.warning("register with %s failed: %s", host, error)
                try:
                    self._sock.sendto(encode_announce(self.info, False),
                                      (host, MULTICAST_PORT))
                except OSError:
                    pass
            else:
                if isinstance(answered, DeviceInfo):
                    self.note_http_peer(answered, host)

    def prune(self) -> None:
        now = self.clock()
        with self.lock:
            stale = [key for key, peer in self.peers.items()
                     if now - peer.last_seen > PEER_TTL_SECONDS]
            for key in stale:
                del self.peers[key]
            peers = list(self.peers.values())
        self.on_peers(peers)

    def live_peers(self) -> list[Peer]:
        self.prune()
        with self.lock:
            return list(self.peers.values())

    def _loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        try:
            sock.settimeout(0.5)
        except OSError:
            return
        last_announce = self.clock()
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(65536)
            except socket.timeout:
                pass
            except OSError:
                break
            else:
                self.handle_datagram(data, addr)
            now = self.clock()
            if now - last_announce >= ANNOUNCE_INTERVAL_SECONDS:
                last_announce = now
                self.prune()
                self.announce()


def parse_info_payload(data: bytes) -> DeviceInfo:
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("info payload is not JSON") from error
    return DeviceInfo.from_dict(raw)


def scan_subnet(
    prefix: str,
    port: int = DEFAULT_PORT,
    timeout: float = 0.6,
    fetch: Callable[[str, int, float], bytes] | None = None,
    cert_path: str | None = None,
    key_path: str | None = None,
) -> list[DeviceInfo]:
    """Probe every host in a /24 for the info endpoint.

    fetch(host, port, timeout) returns the raw body; injectable for tests.
    """
    from client import fetch_info

    if fetch is not None:
        do_fetch = fetch
    else:
        def do_fetch(host: str, port_: int, timeout_: float) -> bytes:
            return fetch_info(host, port_, timeout_,
                              cert_path=cert_path, key_path=key_path)
    found: list[DeviceInfo] = []
    lock = threading.Lock()
    threads = []

    def probe(last: int) -> None:
        try:
            body = do_fetch(f"{prefix}.{last}", port, timeout)
        except (OSError, ValueError):
            return
        try:
            info = parse_info_payload(body)
        except ValueError:
            return
        with lock:
            found.append(info)

    for last in range(1, 255):
        thread = threading.Thread(target=probe, args=(last,), daemon=True)
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join(timeout + 5)
    return found
