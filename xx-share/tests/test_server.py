"""HTTPS endpoints against a real loopback TLS server in a thread."""
import hashlib
import http.client
import json
import ssl
import threading
import time
from dataclasses import replace
from urllib.parse import quote

import pytest

from certs import cert_fingerprint
from client import (
    FingerprintMismatch,
    PinRequired,
    TransferError,
    prepare_upload,
    send_files,
    verified_request,
)
from protocol import DeviceInfo, UploadFile
from server import ShareServer


def _ctx():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _post(port, path, body=b"", headers=None):
    conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=30,
                                       context=_ctx())
    try:
        conn.request("POST", path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _json_body(payload):
    raw = json.dumps(payload).encode()
    return raw, {"Content-Type": "application/json",
                 "Content-Length": str(len(raw))}


@pytest.fixture()
def server(cert_pair, tmp_path):
    cert_path, key_path = cert_pair
    fingerprint = cert_fingerprint(cert_path)
    info = DeviceInfo(alias="testphone", fingerprint=fingerprint,
                      port=0, device_type="mobile")
    decided = []
    srv = ShareServer(
        device_info=info,
        cert_path=cert_path,
        key_path=key_path,
        bind_addrs=["127.0.0.1"],
        port=0,
        staging_root=tmp_path / "staging",
        download_dir=tmp_path / "downloads",
        decide=lambda session: decided.append(session.session_id) or False,
    )
    srv.start()
    srv.device_info = replace(info, port=srv.bound_ports[0])
    yield srv
    srv.stop()


def _prepare(port, sender_fp="ff", files=None, pin=None, body_pin=None):
    sender = DeviceInfo(alias="sender", fingerprint=sender_fp)
    items = files or [UploadFile(id="f1", file_name="hello.txt", size=5,
                                 file_type="text/plain")]
    body = {"info": sender.to_dict(),
            "files": {item.id: item.to_dict() for item in items}}
    if body_pin is not None:
        body["pin"] = body_pin
    path = "/api/localsend/v2/prepare-upload"
    if pin is not None:
        path += f"?pin={quote(pin)}"
    raw, headers = _json_body(body)
    return _post(port, path, raw, headers)


def _prepare_async(port, **kwargs):
    out = {}

    def run():
        try:
            out["result"] = _prepare(port, **kwargs)
        except Exception as error:  # noqa: BLE001
            out["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return out, thread


def _wait_session(server, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with server.lock:
            if server.sessions:
                return next(iter(server.sessions.values()))
        time.sleep(0.01)
    raise AssertionError("no session was created")


def _upload(port, session_id, file_id, token, data):
    return _post(port, f"/api/localsend/v2/upload?sessionId={session_id}"
                       f"&fileId={file_id}&token={token}", data,
                 {"Content-Type": "application/octet-stream",
                  "Content-Length": str(len(data))})


def test_info_and_register(server):
    port = server.bound_ports[0]
    conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=10,
                                       context=_ctx())
    try:
        conn.request("GET", "/api/localsend/v2/info")
        response = conn.getresponse()
        payload = json.loads(response.read().decode())
    finally:
        conn.close()
    assert response.status == 200
    assert payload["alias"] == "testphone"

    peers = []
    server.on_peer = peers.append
    raw, headers = _json_body(DeviceInfo(alias="s", fingerprint="ab").to_dict())
    status, body = _post(port, "/api/localsend/v2/register", raw, headers)
    assert status == 200
    answered = json.loads(body.decode())
    assert answered["alias"] == "testphone"
    assert set(answered) == {"alias", "version", "deviceModel", "deviceType",
                             "fingerprint", "download"}
    assert peers and peers[0].alias == "s"


def test_held_prepare_released_by_accept(server):
    port = server.bound_ports[0]
    out, thread = _prepare_async(port)
    session = _wait_session(server)
    assert session.state == "waiting"
    assert server.accept_session(session.session_id) == []
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert out["result"][0] == 200
    payload = json.loads(out["result"][1].decode())
    session_id = payload["sessionId"]
    token = payload["files"]["f1"]

    data = b"hello"
    status, _ = _upload(port, session_id, "f1", token, data)
    assert status == 200
    assert (server.download_dir / "hello.txt").read_bytes() == data


def test_held_prepare_released_by_decline(server):
    port = server.bound_ports[0]
    out, thread = _prepare_async(port)
    session = _wait_session(server)
    server.decline_session(session.session_id)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert out["result"][0] == 403
    status, _ = _upload(port, session.session_id, "f1", "whatever", b"hello")
    assert status in (403, 404)


def test_held_prepare_timeout_counts_as_decline(server):
    server.decision_timeout = 0.2
    port = server.bound_ports[0]
    started = time.monotonic()
    status, _ = _prepare(port)
    elapsed = time.monotonic() - started
    assert status == 403
    assert elapsed < 10


def test_partial_accept_returns_only_accepted_tokens(server):
    port = server.bound_ports[0]
    files = [UploadFile(id="f1", file_name="one.txt", size=3,
                        file_type="text/plain"),
             UploadFile(id="f2", file_name="two.txt", size=3,
                        file_type="text/plain")]
    out, thread = _prepare_async(port, files=files)
    session = _wait_session(server)
    server.accept_session(session.session_id, ["f1"])
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert out["result"][0] == 200
    payload = json.loads(out["result"][1].decode())
    assert set(payload["files"]) == {"f1"}
    session_id = payload["sessionId"]
    status, _ = _upload(port, session_id, "f1", payload["files"]["f1"], b"one")
    assert status == 200
    status, _ = _upload(port, session_id, "f2", "no-token", b"two")
    assert status == 404
    assert (server.download_dir / "one.txt").read_bytes() == b"one"
    assert not (server.download_dir / "two.txt").exists()


def test_accepting_nothing_answers_204(server):
    port = server.bound_ports[0]
    out, thread = _prepare_async(port)
    session = _wait_session(server)
    server.accept_session(session.session_id, [])
    thread.join(timeout=10)
    assert not thread.is_alive()
    status, body = out["result"]
    assert status == 204
    assert body == b""


def test_upload_before_decision_is_rejected(server):
    port = server.bound_ports[0]
    out, thread = _prepare_async(port)
    session = _wait_session(server)
    with server.lock:
        token = session.tokens["f1"]
    status, _ = _upload(port, session.session_id, "f1", token, b"hello")
    assert status == 403
    server.accept_session(session.session_id)
    thread.join(timeout=10)
    assert out["result"][0] == 200
    payload = json.loads(out["result"][1].decode())
    status, _ = _upload(port, payload["sessionId"], "f1",
                        payload["files"]["f1"], b"hello")
    assert status == 200


def test_second_session_blocked_while_one_is_active(server):
    port = server.bound_ports[0]
    out, thread = _prepare_async(port)
    first = _wait_session(server)
    status, _ = _prepare(port)
    assert status == 409
    server.decline_session(first.session_id)
    thread.join(timeout=10)
    assert out["result"][0] == 403
    server.decide = lambda _session: True
    status, raw = _prepare(port)
    assert status == 200
    assert json.loads(raw.decode())["sessionId"]


def test_rate_limit_returns_429(server):
    server.decide = lambda _session: True
    server.rate_limit_max = 1
    port = server.bound_ports[0]
    status, _ = _prepare(port)
    assert status == 200
    status, _ = _prepare(port)
    assert status == 429


def test_client_send_files_end_to_end(server, tmp_path):
    accepted = []
    server.decide = lambda session: True
    server.on_session = lambda session: accepted.append(session.session_id)
    payload = tmp_path / "photo.jpg"
    payload.write_bytes(b"0123456789")
    progress = []
    send_files(server.device_info, "127.0.0.1", server.device_info,
               [payload],
               progress=lambda fid, sent, total: progress.append((sent, total)))
    assert accepted
    assert progress and progress[-1] == (10, 10)
    assert (server.download_dir / "photo.jpg").read_bytes() == b"0123456789"


def test_cancel_closes_session(server):
    server.decide = lambda _session: True
    port = server.bound_ports[0]
    status, raw = _prepare(port)
    assert status == 200
    payload = json.loads(raw.decode())
    session_id = payload["sessionId"]
    token = payload["files"]["f1"]
    status, _ = _post(port, f"/api/localsend/v2/cancel?sessionId={session_id}")
    assert status == 200
    status, _ = _post(port, "/api/localsend/v2/cancel?sessionId=nope")
    assert status == 404
    status, _ = _upload(port, session_id, "f1", token, b"hello")
    assert status == 403
    status, raw = _prepare(port)
    assert status == 200
    assert json.loads(raw.decode())["sessionId"] != session_id


def test_wrong_token_rejected(server):
    server.decide = lambda _session: True
    port = server.bound_ports[0]
    status, raw = _prepare(port)
    session_id = json.loads(raw.decode())["sessionId"]
    status, _ = _post(port, f"/api/localsend/v2/upload?sessionId={session_id}"
                            "&fileId=f1&token=wrong", b"hello",
                      {"Content-Length": "5"})
    assert status == 403
    status, _ = _post(port, "/api/localsend/v2/upload?sessionId=nope"
                            "&fileId=f1&token=x", b"hello",
                      {"Content-Length": "5"})
    assert status == 404


def test_pin_comes_from_query_string(server):
    server.decide = lambda _session: True
    port = server.bound_ports[0]
    server.require_pin = "2468"
    status, _ = _prepare(port)
    assert status == 401
    status, _ = _prepare(port, pin="0000")
    assert status == 401
    status, raw = _prepare(port, pin="2468")
    assert status == 200
    assert json.loads(raw.decode())["sessionId"]


def test_pin_in_body_accepted_for_leniency(server):
    server.decide = lambda _session: True
    port = server.bound_ports[0]
    server.require_pin = "2468"
    status, raw = _prepare(port, body_pin="2468")
    assert status == 200
    assert json.loads(raw.decode())["sessionId"]


def test_prepare_rejects_bad_pin_via_client(server):
    port = server.bound_ports[0]
    server.decide = lambda _session: True
    server.require_pin = "2468"
    sender = DeviceInfo(alias="s", fingerprint="ff")
    files = [UploadFile(id="f1", file_name="a", size=1, file_type="x")]
    with pytest.raises(PinRequired):
        prepare_upload("127.0.0.1", port, server.device_info.fingerprint,
                       sender, files)
    session_id, tokens = prepare_upload(
        "127.0.0.1", port, server.device_info.fingerprint,
        sender, files, pin="2468")
    assert session_id and tokens["f1"]


def test_client_maps_busy_and_decline(server):
    port = server.bound_ports[0]
    sender = DeviceInfo(alias="s", fingerprint="ff")
    files = [UploadFile(id="f1", file_name="a", size=1, file_type="x")]
    fingerprint = server.device_info.fingerprint
    server.decide = lambda _session: True
    status, raw = _prepare(port)
    assert status == 200
    first_id = json.loads(raw.decode())["sessionId"]
    with pytest.raises(TransferError) as busy:
        prepare_upload("127.0.0.1", port, fingerprint, sender, files)
    assert busy.value.status == 409
    assert server.cancel_session(first_id) is True

    server.decide = lambda _session: False
    out = {}

    def run() -> None:
        try:
            prepare_upload("127.0.0.1", port, fingerprint, sender, files)
        except TransferError as error:
            out["status"] = error.status

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    session = _wait_session(server)
    if session.session_id == first_id:
        end = time.monotonic() + 10
        while time.monotonic() < end:
            with server.lock:
                fresh = [item for item in server.sessions.values()
                         if item.session_id != first_id]
                if fresh:
                    session = fresh[-1]
                    break
            time.sleep(0.01)
    server.decline_session(session.session_id)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert out.get("status") == 403


def test_sha256_mismatch_returns_422(server):
    server.decide = lambda _session: True
    port = server.bound_ports[0]
    digest = hashlib.sha256(b"hello").hexdigest()
    files = [UploadFile(id="f1", file_name="hello.txt", size=5,
                        file_type="text/plain", sha256=digest)]
    status, raw = _prepare(port, files=files)
    assert status == 200
    payload = json.loads(raw.decode())
    session_id = payload["sessionId"]
    token = payload["files"]["f1"]
    status, _ = _upload(port, session_id, "f1", token, b"world")
    assert status == 422
    status, _ = _upload(port, session_id, "f1", token, b"hello")
    assert status == 200
    assert (server.download_dir / "hello.txt").read_bytes() == b"hello"


def test_describe_files_falls_back_to_octet_stream(tmp_path):
    from client import describe_files

    payload = tmp_path / "noext-blob"
    payload.write_bytes(b"data")
    (meta,) = describe_files([payload])
    assert meta.file_type == "application/octet-stream"


def test_size_limit(server):
    port = server.bound_ports[0]
    server.max_transfer_bytes = 4
    big = [UploadFile(id="f1", file_name="big.bin", size=5,
                      file_type="application/octet-stream")]
    status, _ = _prepare(port, files=big)
    assert status == 413


def test_fingerprint_mismatch(server):
    port = server.bound_ports[0]
    with pytest.raises(FingerprintMismatch):
        verified_request("127.0.0.1", port, "00" * 32, "GET",
                         "/api/localsend/v2/info", timeout=5)
    status, raw = verified_request("127.0.0.1", port,
                                   server.device_info.fingerprint, "GET",
                                   "/api/localsend/v2/info", timeout=5)
    assert status == 200
    assert json.loads(raw.decode())["alias"] == "testphone"


def test_prepare_rejects_rogue_certificate(server):
    server.decide = lambda _session: True
    sender = DeviceInfo(alias="s", fingerprint="ff")
    files = [UploadFile(id="f1", file_name="a", size=1, file_type="x")]
    port = server.bound_ports[0]
    with pytest.raises(FingerprintMismatch):
        prepare_upload("127.0.0.1", port, "00" * 32, sender, files)
