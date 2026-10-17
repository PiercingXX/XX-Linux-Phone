"""Fake-official-client flow: prepare-upload -> chunked upload -> done.

The official LocalSend sender (Rust core, ``upload_body``) streams file
bytes with ``Transfer-Encoding: chunked`` and no ``Content-Length``.
This runs such a client through the full receive flow against the real
loopback TLS server and asserts the file lands byte-identical.
"""
import hashlib
import http.client
import json
import logging
import ssl
from dataclasses import replace

from certs import cert_fingerprint
from protocol import DeviceInfo, UploadFile
from server import ShareServer


def _ctx():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _post(port, path, body, headers):
    conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=30,
                                       context=_ctx())
    try:
        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _chunked_upload(port, path, chunks):
    """POST with Transfer-Encoding: chunked, no Content-Length."""
    conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=30,
                                       context=_ctx())
    try:
        conn.putrequest("POST", path)
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Transfer-Encoding", "chunked")
        conn.endheaders()
        for piece in chunks:
            conn.send(b"%X\r\n" % len(piece))
            conn.send(piece)
            conn.send(b"\r\n")
        conn.send(b"0\r\n\r\n")
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _localsend_prepare_body(sender, files):
    """Spec-shaped prepare-upload body as the official app sends it."""
    return {"info": sender.to_dict(),
            "files": {item.id: item.to_dict() for item in files}}


def _prepare(port, sender, files):
    raw = json.dumps(_localsend_prepare_body(sender, files)).encode()
    return _post(port, "/api/localsend/v2/prepare-upload", raw,
                 {"Content-Type": "application/json",
                  "Content-Length": str(len(raw))})


def _upload_path(session_id, file_id, token):
    from urllib.parse import quote

    return ("/api/localsend/v2/upload?sessionId=%s&fileId=%s&token=%s"
            % (quote(session_id), quote(file_id), quote(token)))


def _server_fixture(tmp_path, **overrides):
    from certs import ensure_certpair

    cert_path, key_path = ensure_certpair(tmp_path / "certs")
    fingerprint = cert_fingerprint(cert_path)
    info = DeviceInfo(alias="testphone", fingerprint=fingerprint,
                      port=0, device_type="mobile")
    kwargs = dict(
        device_info=info,
        cert_path=cert_path,
        key_path=key_path,
        bind_addrs=["127.0.0.1"],
        port=0,
        staging_root=tmp_path / "staging",
        download_dir=tmp_path / "downloads",
        decide=lambda _session: True,
    )
    kwargs.update(overrides)
    srv = ShareServer(**kwargs)
    srv.start()
    srv.device_info = replace(info, port=srv.bound_ports[0])
    return srv


def test_localsend_chunked_flow_delivers(tmp_path):
    srv = _server_fixture(tmp_path)
    try:
        port = srv.bound_ports[0]
        sender = DeviceInfo(alias="Powerful Mango", fingerprint="aa" * 32)
        data = bytes(index % 251 for index in range(300000))
        digest = hashlib.sha256(data).hexdigest()
        files = [UploadFile(id="some-uuid-1", file_name="IMG_001.jpg",
                            size=len(data), file_type="image/jpeg",
                            sha256=digest)]
        status, raw = _prepare(port, sender, files)
        assert status == 200
        payload = json.loads(raw.decode())
        session_id = payload["sessionId"]
        token = payload["files"]["some-uuid-1"]
        thirds = [data[:100000], data[100000:200000], data[200000:]]
        status, _ = _chunked_upload(
            port, _upload_path(session_id, "some-uuid-1", token), thirds)
        assert status == 200
        assert (srv.download_dir / "IMG_001.jpg").read_bytes() == data
        with srv.lock:
            session = srv.sessions[session_id]
            assert session.state == "finished"
            assert session.received == {"some-uuid-1"}
    finally:
        srv.stop()


def test_localsend_chunked_checksum_mismatch_is_422(tmp_path):
    srv = _server_fixture(tmp_path)
    try:
        port = srv.bound_ports[0]
        sender = DeviceInfo(alias="Powerful Mango", fingerprint="aa" * 32)
        files = [UploadFile(id="f1", file_name="note.txt", size=5,
                            file_type="text/plain",
                            sha256=hashlib.sha256(b"hello").hexdigest())]
        status, raw = _prepare(port, sender, files)
        assert status == 200
        payload = json.loads(raw.decode())
        path = _upload_path(payload["sessionId"], "f1",
                            payload["files"]["f1"])
        status, _ = _chunked_upload(port, path, [b"wor", b"ld"])
        assert status == 422
        assert not (srv.download_dir / "note.txt").exists()
        status, _ = _chunked_upload(port, path, [b"hel", b"lo"])
        assert status == 200
        assert (srv.download_dir / "note.txt").read_bytes() == b"hello"
    finally:
        srv.stop()


def test_localsend_chunked_over_declared_size_is_400(tmp_path):
    srv = _server_fixture(tmp_path)
    try:
        port = srv.bound_ports[0]
        sender = DeviceInfo(alias="Powerful Mango", fingerprint="aa" * 32)
        files = [UploadFile(id="f1", file_name="note.txt", size=3,
                            file_type="text/plain")]
        status, raw = _prepare(port, sender, files)
        assert status == 200
        payload = json.loads(raw.decode())
        path = _upload_path(payload["sessionId"], "f1",
                            payload["files"]["f1"])
        status, _ = _chunked_upload(port, path, [b"four"])
        assert status == 400
        assert not (srv.download_dir / "note.txt").exists()
    finally:
        srv.stop()


def test_access_log_has_no_tokens_or_bodies(tmp_path, caplog):
    srv = _server_fixture(tmp_path)
    try:
        port = srv.bound_ports[0]
        sender = DeviceInfo(alias="Powerful Mango", fingerprint="aa" * 32)
        files = [UploadFile(id="f1", file_name="note.txt", size=5,
                            file_type="text/plain")]
        with caplog.at_level(logging.INFO, logger="server"):
            status, raw = _prepare(port, sender, files)
            assert status == 200
            payload = json.loads(raw.decode())
            token = payload["files"]["f1"]
            path = _upload_path(payload["sessionId"], "f1", token)
            status, _ = _chunked_upload(port, path, [b"hello"])
            assert status == 200
        lines = [record.getMessage() for record in caplog.records
                 if record.name == "server" and record.levelno == logging.INFO]
        assert any("POST /api/localsend/v2/prepare-upload" in line
                   and " 200 " in line for line in lines), lines
        assert any("POST /api/localsend/v2/upload" in line
                   and " 200 " in line for line in lines), lines
        assert token not in caplog.text
        assert "sessionId=" not in caplog.text
        assert "hello" not in caplog.text
    finally:
        srv.stop()
