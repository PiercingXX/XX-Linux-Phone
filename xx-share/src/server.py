"""Receiving HTTPS server: LocalSend v2 endpoints with held prepare-upload.

The prepare-upload request stays open until the local user Accepts or
Declines (or a timeout fires); uploads only start after a 200 answer, so
nothing is staged before an explicit Accept (or a favorites
auto-accept). Runs on threads, never on the GTK main loop.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from protocol import (
    PATH_CANCEL,
    PATH_INFO,
    PATH_PREPARE_UPLOAD,
    PATH_REGISTER,
    PATH_UPLOAD,
    DeviceInfo,
    UploadFile,
    build_prepare_response,
    parse_prepare_upload,
)
from sanitize import sanitize_filename, unique_path

WAITING = "waiting"
ACCEPTED = "accepted"
DECLINED = "declined"
CANCELLED = "cancelled"
FINISHED = "finished"

_JSON_BODY_LIMIT = 1024 * 1024


def _load_pyopenssl():
    try:
        from OpenSSL import SSL as _mod

        return _mod
    except ImportError:
        return None


def _peer_der_from_x509(cert):
    if cert is None:
        return None
    try:
        peer_crypto = cert.to_cryptography()
        from cryptography.hazmat.primitives import serialization

        return peer_crypto.public_bytes(serialization.Encoding.DER)
    except Exception:
        try:
            from OpenSSL import crypto as _crypto

            return _crypto.dump_certificate(_crypto.FILETYPE_ASN1, cert)
        except Exception:
            return None


class _PyOpenSSLFile:
    def __init__(self, tls_conn):
        self._tls = tls_conn
        self._buf = bytearray()
        self.closed = False

    def _recv_once(self, size):
        from OpenSSL.SSL import SysCallError, WantReadError, WantWriteError
        from OpenSSL.SSL import ZeroReturnError

        while True:
            try:
                return self._tls.recv(size)
            except (WantReadError, WantWriteError):
                continue
            except (ZeroReturnError, SysCallError):
                return b""

    def read(self, size=-1):
        if size is None or size < 0:
            chunks = []
            if self._buf:
                chunks.append(bytes(self._buf))
                self._buf.clear()
            while True:
                data = self._recv_once(16384)
                if not data:
                    break
                chunks.append(data)
            return b"".join(chunks)
        if size == 0:
            return b""
        while len(self._buf) < size:
            data = self._recv_once(max(16384, size - len(self._buf)))
            if not data:
                break
            self._buf.extend(data)
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out

    def readline(self, limit=-1):
        if limit is None or limit < 0:
            limit = 65537
        while True:
            index = self._buf.find(b"\n")
            if index >= 0:
                end = index + 1
                if limit > 0 and end > limit:
                    out = bytes(self._buf[:limit])
                    del self._buf[:limit]
                    return out
                out = bytes(self._buf[:end])
                del self._buf[:end]
                return out
            if limit > 0 and len(self._buf) >= limit:
                out = bytes(self._buf[:limit])
                del self._buf[:limit]
                return out
            data = self._recv_once(16384)
            if not data:
                out = bytes(self._buf)
                self._buf.clear()
                return out
            self._buf.extend(data)

    def write(self, data):
        self._tls.sendall(bytes(data))
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _PyOpenSSLConn:
    def __init__(self, tls_conn, raw_sock, peer_der):
        self._tls = tls_conn
        self._raw = raw_sock
        self._peer_der = peer_der

    def getpeercert(self, binary_form=False):
        if binary_form:
            return self._peer_der
        return None

    def makefile(self, mode, _bufsize=-1):
        return _PyOpenSSLFile(self._tls)

    def recv(self, size, _flags=0):
        return self._tls.recv(size)

    def send(self, data, _flags=0):
        return self._tls.send(data)

    def sendall(self, data, _flags=0):
        return self._tls.sendall(data)

    def settimeout(self, timeout):
        return self._raw.settimeout(timeout)

    def gettimeout(self):
        return self._raw.gettimeout()

    def shutdown(self, _how=socket.SHUT_WR):
        try:
            self._tls.shutdown()
        except Exception:
            pass

    def close(self):
        try:
            self._tls.close()
        except Exception:
            pass
        try:
            self._raw.close()
        except OSError:
            pass


class _PyOpenSSLHTTPServer(ThreadingHTTPServer):
    def __init__(self, *args, tls_context=None, **kwargs):
        self._tls_context = tls_context
        super().__init__(*args, **kwargs)

    def get_request(self):
        raw, addr = self.socket.accept()
        from OpenSSL.SSL import Connection as _Connection

        tls = _Connection(self._tls_context, raw)
        tls.set_accept_state()
        try:
            tls.do_handshake()
        except Exception:
            try:
                raw.close()
            except OSError:
                pass
            try:
                tls.close()
            except Exception:
                pass
            raise OSError("TLS handshake failed")
        try:
            peer = tls.get_peer_certificate()
        except Exception:
            peer = None
        return _PyOpenSSLConn(tls, raw, _peer_der_from_x509(peer)), addr

    def shutdown_request(self, request):
        try:
            request.shutdown(socket.SHUT_WR)
        except (OSError, ValueError):
            pass
        self.close_request(request)

    def close_request(self, request):
        try:
            request.close()
        except (OSError, ValueError):
            pass


@dataclass
class Session:
    session_id: str
    sender: DeviceInfo
    files: dict[str, UploadFile]
    tokens: dict[str, str]
    state: str = WAITING
    received: set[str] = field(default_factory=set)
    auto: bool = False
    delivered: list[Path] = field(default_factory=list)
    decision: threading.Event = field(default_factory=threading.Event)
    accepted: set[str] = field(default_factory=set)
    verified: bool = False


class _Handler(BaseHTTPRequestHandler):
    server_version = "xx-share/0.1"

    @property
    def _share(self) -> "ShareServer":
        return self.server.share_server  # type: ignore[attr-defined]

    def log_message(self, *args: object) -> None:
        pass

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str) -> None:
        self._send_json(code, {"message": message})

    def _send_empty(self, code: int) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_json(self) -> object:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("bad Content-Length")
        if length <= 0 or length > _JSON_BODY_LIMIT:
            raise ValueError("bad body size")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == PATH_INFO:
            self._send_json(200, self._share.device_info.to_dict())
        else:
            self._error(404, "unknown endpoint")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == PATH_REGISTER:
                self._handle_register()
            elif parsed.path == PATH_PREPARE_UPLOAD:
                self._handle_prepare(query)
            elif parsed.path == PATH_UPLOAD:
                self._handle_upload(query)
            elif parsed.path == PATH_CANCEL:
                self._handle_cancel(query)
            else:
                self._error(404, "unknown endpoint")
        except ValueError as error:
            self._error(400, str(error))
        except (OSError, ssl.SSLError):
            pass

    def _client_cert_fingerprint(self) -> str | None:
        try:
            der = self.connection.getpeercert(binary_form=True)
        except (ValueError, ssl.SSLError, AttributeError):
            return None
        if not der:
            return None
        return hashlib.sha256(der).hexdigest().lower()

    def _claimed_verified(self, claimed: str) -> bool:
        actual = self._client_cert_fingerprint()
        if actual is None:
            return False
        return actual == str(claimed).strip().lower()

    def _handle_register(self) -> None:
        raw = self._read_json()
        peer = DeviceInfo.from_dict(raw)
        verified = self._claimed_verified(peer.fingerprint)
        addr = self.client_address[0] if self.client_address else ""
        self._share.note_peer(peer, addr=addr, verified=verified)
        self._send_json(200, self._share.device_info.to_register_dict())

    def _handle_prepare(self, query: dict) -> None:
        raw = self._read_json()
        if not isinstance(raw, dict):
            raise ValueError("prepare-upload body must be an object")
        sender, files = parse_prepare_upload(raw)
        verified = self._claimed_verified(sender.fingerprint)
        share = self._share
        pin = query.get("pin", [None])[0]
        if pin is None:
            body_pin = raw.get("pin", "")
            pin = body_pin if isinstance(body_pin, str) else ""
        if share.require_pin:
            if not isinstance(pin, str) or not hmac.compare_digest(pin, share.require_pin):
                self._error(401, "PIN required")
                return
        if not share.register_attempt():
            self._error(429, "too many requests")
            return
        if share.has_active_session():
            self._error(409, "blocked by another session")
            return
        total = sum(item.size for item in files)
        if total > share.max_transfer_bytes:
            self._error(413, "transfer exceeds size limit")
            return
        session = share.create_session(sender, files, verified=verified)
        if share.decide(session):
            session.auto = True
            share.accept_session(session.session_id)
        share.announce_session(session)
        decided = session.decision.wait(timeout=share.decision_timeout)
        with share.lock:
            accepted_now = session.state == ACCEPTED
            accepted_ids = set(session.accepted) & set(session.files)
        if not accepted_now:
            if not decided:
                share.cancel_session(session.session_id, declined=True)
            self._error(403, "rejected")
            return
        with share.lock:
            if not accepted_ids:
                session.state = FINISHED
            else:
                for file_id in list(session.files):
                    if file_id not in accepted_ids:
                        del session.files[file_id]
                        del session.tokens[file_id]
                tokens = dict(session.tokens)
        if not accepted_ids:
            self._send_empty(204)
            return
        self._send_json(200, build_prepare_response(session.session_id, tokens))

    def _drain_body(self, length: int) -> None:
        try:
            remaining = min(max(length, 0), self._share.max_transfer_bytes)
        except (TypeError, ValueError):
            return
        while remaining > 0:
            try:
                chunk = self.rfile.read(min(65536, remaining))
            except (OSError, ValueError):
                return
            if not chunk:
                return
            remaining -= len(chunk)

    def _handle_upload(self, query: dict) -> None:
        session_id = query.get("sessionId", [None])[0]
        file_id = query.get("fileId", [None])[0]
        token = query.get("token", [None])[0]
        share = self._share
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            self._error(400, "bad Content-Length")
            return
        if length < 0:
            self._error(400, "bad Content-Length")
            return
        with share.lock:
            session = share.sessions.get(session_id or "")
            if session is None:
                self._drain_body(length)
                self._error(404, "unknown session")
                return
            expected = session.tokens.get(file_id or "")
            if expected is None or token is None:
                self._drain_body(length)
                self._error(404, "unknown file")
                return
            if not hmac.compare_digest(token, expected):
                self._drain_body(length)
                self._error(403, "bad token")
                return
            if file_id in session.received:
                self._drain_body(length)
                self._send_json(200, {})
                return
            if session.state in (DECLINED, CANCELLED):
                self._drain_body(length)
                self._error(403, "session closed")
                return
            if session.state == WAITING:
                self._drain_body(length)
                self._error(403, "transfer not accepted yet")
                return
            meta = session.files[file_id]
        if length > meta.size:
            self._drain_body(length)
            self._error(400, "bad body size")
            return
        staged = share.stage_path(session, file_id)
        try:
            digest = hashlib.sha256()
            remaining = length
            with open(staged, "wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            if remaining:
                staged.unlink(missing_ok=True)
                self._error(400, "truncated body")
                return
            if meta.sha256 and digest.hexdigest() != meta.sha256.lower():
                staged.unlink(missing_ok=True)
                self._error(422, "checksum mismatch")
                return
        except OSError:
            self._error(500, "storage failed")
            return
        with share.lock:
            session.received.add(file_id)
            complete = session.received == set(session.files)
        if complete:
            share.session_complete(session)
        self._send_json(200, {})

    def _handle_cancel(self, query: dict) -> None:
        session_id = query.get("sessionId", [None])[0]
        if self._share.cancel_session(session_id or ""):
            self._send_json(200, {})
        else:
            self._error(404, "unknown session")


class ShareServer:
    """One HTTPS endpoint per bind address, shared session table."""

    def __init__(
        self,
        device_info: DeviceInfo,
        cert_path: str | Path,
        key_path: str | Path,
        bind_addrs: list[str],
        port: int,
        staging_root: str | Path,
        download_dir: str | Path,
        require_pin: str = "",
        max_transfer_bytes: int = 500 * 1024 * 1024,
        decision_timeout: float = 120.0,
        rate_limit_max: int = 30,
        rate_limit_window: float = 60.0,
        decide: Callable[[Session], bool] | None = None,
        on_session: Callable[[Session], None] | None = None,
        on_complete: Callable[[Session], None] | None = None,
        on_peer: Callable[..., None] | None = None,
        on_cancel: Callable[[Session], None] | None = None,
    ) -> None:
        self.device_info = device_info
        self.cert_path = Path(cert_path)
        self.key_path = Path(key_path)
        self.bind_addrs = list(bind_addrs)
        self.port = port
        self.staging_root = Path(staging_root)
        self.download_dir = Path(download_dir)
        self.require_pin = require_pin
        self.max_transfer_bytes = max_transfer_bytes
        self.decision_timeout = decision_timeout
        self.rate_limit_max = rate_limit_max
        self.rate_limit_window = rate_limit_window
        self.decide = decide or (lambda _session: False)
        self.on_session = on_session or (lambda _session: None)
        self.on_complete = on_complete or (lambda _session: None)
        self.on_peer = on_peer or (lambda *_args: None)
        self.on_cancel = on_cancel
        self.lock = threading.Lock()
        self.sessions: dict[str, Session] = {}
        self._prepare_times: list[float] = []
        self._servers: list[ThreadingHTTPServer] = []
        self._threads: list[threading.Thread] = []

    def _make_server(self, addr: str) -> ThreadingHTTPServer:
        pyssl = _load_pyopenssl()
        if pyssl is not None:
            tls_context = pyssl.Context(pyssl.TLS_SERVER_METHOD)
            tls_context.use_certificate_chain_file(str(self.cert_path))
            tls_context.use_privatekey_file(str(self.key_path))
            tls_context.set_verify(pyssl.VERIFY_PEER, lambda *args: True)
            server = _PyOpenSSLHTTPServer(
                (addr, self.port), _Handler, bind_and_activate=False,
                tls_context=tls_context)
            server.share_server = self  # type: ignore[attr-defined]
            server.handle_error = lambda *args: None  # type: ignore[method-assign]
            server.daemon_threads = True
            server.allow_reuse_address = True
            try:
                server.server_bind()
                server.server_activate()
            except OSError:
                server.server_close()
                raise
            return server
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.cert_path), str(self.key_path))
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        server = ThreadingHTTPServer((addr, self.port), _Handler,
                                     bind_and_activate=False)
        server.share_server = self  # type: ignore[attr-defined]
        server.handle_error = lambda *args: None  # type: ignore[method-assign]
        server.daemon_threads = True
        server.allow_reuse_address = True
        try:
            server.server_bind()
            server.server_activate()
        except OSError:
            server.server_close()
            raise
        server.socket = context.wrap_socket(server.socket, server_side=True)
        return server

    def start(self) -> None:
        for addr in self.bind_addrs:
            server = self._make_server(addr)
            self._servers.append(server)
            thread = threading.Thread(target=server.serve_forever,
                                      kwargs={"poll_interval": 0.2},
                                      daemon=True)
            self._threads.append(thread)
            thread.start()

    def stop(self) -> None:
        for server in self._servers:
            server.shutdown()
        for server in self._servers:
            server.server_close()
        for thread in self._threads:
            thread.join(timeout=5)
        self._servers.clear()
        self._threads.clear()

    @property
    def bound_ports(self) -> list[int]:
        ports = []
        for server in self._servers:
            try:
                ports.append(server.socket.getsockname()[1])
            except OSError:
                continue
        return ports

    def note_peer(self, peer: DeviceInfo, addr: str = "",
                  verified: bool = False) -> None:
        try:
            self.on_peer(peer, addr, verified)
        except TypeError:
            try:
                self.on_peer(peer, addr)
            except TypeError:
                self.on_peer(peer)

    def register_attempt(self) -> bool:
        now = time.monotonic()
        with self.lock:
            self._prepare_times = [mark for mark in self._prepare_times
                                   if now - mark < self.rate_limit_window]
            if len(self._prepare_times) >= self.rate_limit_max:
                return False
            self._prepare_times.append(now)
            return True

    def has_active_session(self) -> bool:
        with self.lock:
            return any(session.state in (WAITING, ACCEPTED)
                       for session in self.sessions.values())

    def create_session(
        self, sender: DeviceInfo, files: list[UploadFile],
        verified: bool = False,
    ) -> Session:
        session_id = secrets.token_urlsafe(16)
        tokens = {item.id: secrets.token_urlsafe(32) for item in files}
        session = Session(
            session_id=session_id,
            sender=sender,
            files={item.id: item for item in files},
            tokens=tokens,
            verified=bool(verified),
        )
        with self.lock:
            if len(self.sessions) > 64:
                stale = next(iter(self.sessions))
                del self.sessions[stale]
            self.sessions[session_id] = session
        return session

    def announce_session(self, session: Session) -> None:
        self.on_session(session)

    def stage_path(self, session: Session, file_id: str) -> Path:
        digest = hashlib.sha256(file_id.encode("utf-8")).hexdigest()[:16]
        folder = self.staging_root / session.session_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{digest}.part"

    def session_complete(self, session: Session) -> None:
        with self.lock:
            if session.state == ACCEPTED and session.received == set(session.files):
                session.state = FINISHED
                self._deliver_locked(session)
        self.on_complete(session)

    def _deliver_locked(self, session: Session) -> list[Path]:
        """Move staged files to Downloads. Caller must hold the lock."""
        delivered: list[Path] = []
        self.download_dir.mkdir(parents=True, exist_ok=True)
        folder = self.staging_root / session.session_id
        for file_id, meta in session.files.items():
            name = hashlib.sha256(file_id.encode()).hexdigest()[:16]
            staged = folder / f"{name}.part"
            if not staged.exists():
                continue
            target = unique_path(self.download_dir, sanitize_filename(meta.file_name))
            try:
                staged.rename(target)
            except OSError:
                continue
            delivered.append(target)
        session.delivered.extend(delivered)
        return delivered

    def accept_session(
        self, session_id: str, file_ids: list[str] | set[str] | None = None
    ) -> list[Path]:
        """Record Accept for a pending prepare and wake its held request.

        file_ids selects the accepted subset (partial accept); None means
        every file, an empty collection answers the sender with 204. When
        the session already finished uploading, deliver instead. Returns
        delivered paths (usually empty: delivery happens on completion).
        """
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None or session.state in (DECLINED, CANCELLED, FINISHED):
                return []
            if session.state == WAITING:
                wanted = set(session.files) if file_ids is None else set(file_ids)
                session.accepted = wanted & set(session.files)
                session.state = ACCEPTED
                session.decision.set()
                return []
            session.state = ACCEPTED
            if session.received != set(session.files):
                return []
            session.state = FINISHED
            return self._deliver_locked(session)

    def decline_session(self, session_id: str) -> None:
        self.cancel_session(session_id, declined=True)

    def cancel_session(self, session_id: str, declined: bool = False) -> bool:
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                return False
            session.state = DECLINED if declined else CANCELLED
            session.decision.set()
        if self.on_cancel is not None:
            self.on_cancel(session)
        shutil.rmtree(self.staging_root / session.session_id, ignore_errors=True)
        return True
