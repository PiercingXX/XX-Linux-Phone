"""Sending client: fingerprint-pinned HTTPS against a peer's server.

Every TLS connection verifies the peer certificate fingerprint BEFORE
any request bytes go out (manual handshake on our own socket, then the
request runs over the verified socket). Raises FingerprintMismatch on
pin failure so a rogue same-LAN server can never harvest filenames.
"""
from __future__ import annotations

import http.client
import json
import logging
import mimetypes
import socket
import ssl
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from protocol import (
    PATH_CANCEL,
    PATH_INFO,
    PATH_REGISTER,
    PATH_UPLOAD,
    DeviceInfo,
    UploadFile,
    build_prepare_upload,
    parse_prepare_response,
    prepare_upload_path,
)
from sanitize import sanitize_filename


logger = logging.getLogger(__name__)


class FingerprintMismatch(Exception):
    pass


class TransferError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class PinRequired(TransferError):
    pass


def _client_context(cert_path: str | Path | None = None,
                    key_path: str | Path | None = None) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    if cert_path is not None and key_path is not None:
        context.load_cert_chain(str(cert_path), str(key_path))
    return context


def verified_request(
    host: str,
    port: int,
    fingerprint: str,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict | None = None,
    timeout: float = 15.0,
    cert_path: str | Path | None = None,
    key_path: str | Path | None = None,
) -> tuple[int, bytes]:
    raw = socket.create_connection((host, port), timeout=timeout)
    try:
        tls = _client_context(cert_path, key_path).wrap_socket(
            raw, server_hostname=host)
    except (OSError, ssl.SSLError):
        raw.close()
        raise
    with tls:
        der = tls.getpeercert(binary_form=True)
        import hashlib

        actual = hashlib.sha256(der).hexdigest() if der else ""
        if actual.lower() != fingerprint.lower():
            raise FingerprintMismatch(f"peer at {host} presented an unknown certificate")
        conn = http.client.HTTPSConnection(
            host, port, context=_client_context(cert_path, key_path))
        conn.sock = tls
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()


def fetch_info(host: str, port: int, timeout: float = 5.0,
               cert_path: str | Path | None = None,
               key_path: str | Path | None = None) -> bytes:
    conn = http.client.HTTPSConnection(host, port, timeout=timeout,
                                       context=_client_context(cert_path,
                                                               key_path))
    try:
        conn.request("GET", PATH_INFO)
        response = conn.getresponse()
        if response.status != 200:
            raise TransferError(response.status, "info failed")
        return response.read()
    finally:
        conn.close()


def register_with(host: str, port: int, own: DeviceInfo,
                  timeout: float = 10.0,
                  cert_path: str | Path | None = None,
                  key_path: str | Path | None = None) -> None:
    body = json.dumps(own.to_dict()).encode("utf-8")
    conn = http.client.HTTPSConnection(host, port, timeout=timeout,
                                       context=_client_context(cert_path,
                                                               key_path))
    try:
        conn.request("POST", PATH_REGISTER, body=body,
                     headers={"Content-Type": "application/json",
                              "Content-Length": str(len(body))})
        response = conn.getresponse()
        response.read()
        if response.status != 200:
            raise TransferError(response.status, "register failed")
    finally:
        conn.close()


def prepare_upload(
    host: str,
    port: int,
    fingerprint: str,
    sender: DeviceInfo,
    files: list[UploadFile],
    pin: str | None = None,
    timeout: float = 150.0,
    cert_path: str | Path | None = None,
    key_path: str | Path | None = None,
) -> tuple[str, dict[str, str]]:
    body = json.dumps(build_prepare_upload(sender, files)).encode("utf-8")
    status, raw = verified_request(host, port, fingerprint, "POST",
                                   prepare_upload_path(pin), body=body,
                                   headers={"Content-Type": "application/json",
                                            "Content-Length": str(len(body))},
                                   timeout=timeout,
                                   cert_path=cert_path, key_path=key_path)
    if status == 401:
        raise PinRequired(status, "PIN required")
    if status == 204:
        raise TransferError(status, "receiver has nothing to transfer")
    if status == 403:
        raise TransferError(status, "receiver declined the transfer")
    if status == 409:
        raise TransferError(status, "receiver is busy with another session")
    if status == 413:
        raise TransferError(status, "receiver size limit exceeded")
    if status == 429:
        raise TransferError(status, "receiver rate limit exceeded")
    if status != 200:
        raise TransferError(status, _message(raw))
    try:
        return parse_prepare_response(json.loads(raw.decode("utf-8")))
    except ValueError as error:
        raise TransferError(status, str(error)) from error


def _message(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("message"), str):
            return payload["message"]
    except ValueError:
        pass
    return "request failed"


class _ProgressReader:
    def __init__(self, path: Path, size: int, callback: Callable[[int, int], None],
                 is_cancelled: Callable[[], bool] | None = None) -> None:
        self._handle = open(path, "rb")
        self._size = size
        self._sent = 0
        self._callback = callback
        self._is_cancelled = is_cancelled

    def read(self, amount: int = 65536) -> bytes:
        if self._is_cancelled is not None and self._is_cancelled():
            raise TransferError(0, "cancelled")
        chunk = self._handle.read(amount)
        if chunk:
            self._sent += len(chunk)
            self._callback(self._sent, self._size)
        else:
            self._handle.close()
        return chunk

    def __len__(self) -> int:
        return self._size


def describe_files(paths: list[str | Path]) -> list[UploadFile]:
    files = []
    for index, raw in enumerate(paths):
        path = Path(raw)
        size = path.stat().st_size
        mime, _ = mimetypes.guess_type(path.name)
        files.append(UploadFile(id=f"file-{index}", file_name=sanitize_filename(path.name),
                                size=size,
                                file_type=mime or "application/octet-stream"))
    return files


def send_files(
    peer: DeviceInfo,
    addr: str,
    sender: DeviceInfo,
    paths: list[str | Path],
    pin: str | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    timeout: float = 150.0,
    is_cancelled: Callable[[], bool] | None = None,
    cert_path: str | Path | None = None,
    key_path: str | Path | None = None,
) -> None:
    """Full send flow: prepare, then one upload POST per file."""
    def _cancelled() -> bool:
        try:
            return bool(is_cancelled()) if is_cancelled is not None else False
        except Exception as error:
            logger.warning("cancel check failed: %s", error)
            return False

    real = [Path(item) for item in paths]
    for path in real:
        if not path.is_file():
            raise TransferError(0, f"not a file: {path}")
    files = describe_files(real)
    session_id, tokens = prepare_upload(addr, peer.port, peer.fingerprint,
                                        sender, files, pin, timeout,
                                        cert_path=cert_path,
                                        key_path=key_path)
    for meta, path in zip(files, real):
        if _cancelled():
            try:
                cancel_session(addr, peer.port, session_id, timeout=5.0,
                               cert_path=cert_path, key_path=key_path)
            except (OSError, TransferError):
                pass
            raise TransferError(0, "cancelled")
        token = tokens.get(meta.id)
        if token is None:
            continue
        target = (f"{PATH_UPLOAD}?sessionId={quote(session_id)}"
                  f"&fileId={quote(meta.id)}&token={quote(token)}")
        size = meta.size
        report = progress or (lambda _fid, _sent, _total: None)

        def file_progress(sent: int, total: int, fid: str = meta.id) -> None:
            report(fid, sent, total)

        raw = socket.create_connection((addr, peer.port), timeout=timeout)
        try:
            tls = _client_context(cert_path, key_path).wrap_socket(
                raw, server_hostname=addr)
        except (OSError, ssl.SSLError):
            raw.close()
            raise
        with tls:
            import hashlib

            der = tls.getpeercert(binary_form=True)
            actual = hashlib.sha256(der).hexdigest() if der else ""
            if actual.lower() != peer.fingerprint.lower():
                raise FingerprintMismatch("peer certificate changed mid-transfer")
            conn = http.client.HTTPSConnection(
                addr, peer.port,
                context=_client_context(cert_path, key_path))
            conn.sock = tls
            try:
                reader = _ProgressReader(path, size, file_progress,
                                           is_cancelled=_cancelled if is_cancelled else None)
                conn.request("POST", target, body=reader,
                             headers={"Content-Type": "application/octet-stream",
                                      "Content-Length": str(size)})
                response = conn.getresponse()
                status, raw_body = response.status, response.read()
            finally:
                conn.close()
        if status == 403:
            raise TransferError(status, "receiver declined the transfer")
        if status != 200:
            raise TransferError(status, _message(raw_body))


def cancel_session(host: str, port: int, session_id: str,
                   timeout: float = 10.0,
                   cert_path: str | Path | None = None,
                   key_path: str | Path | None = None) -> None:
    conn = http.client.HTTPSConnection(host, port, timeout=timeout,
                                       context=_client_context(cert_path,
                                                               key_path))
    try:
        conn.request("POST", f"{PATH_CANCEL}?sessionId={quote(session_id)}")
        conn.getresponse().read()
    finally:
        conn.close()
