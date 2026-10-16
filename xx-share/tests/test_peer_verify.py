"""Sender verification + peer-table merging.

Covers the spoof lesson: the server requests the sender certificate
(pyOpenSSL VERIFY_PEER accepting any cert, stdlib CERT_NONE fallback)
and compares sha256(client_cert_der) with the claimed fingerprint.
Mismatch (or no cert) never auto-accepts via favorites and the prompt
shows (unverified); a matching cert does. Also covers HTTP-register
merging into the Discovery peer table.
"""
import json
import threading
import time

import pytest

from certs import cert_fingerprint, ensure_certpair
from config import ShareConfig
from discovery import Discovery
from protocol import DeviceInfo, UploadFile
from server import ShareServer


def _server_info(cert_path, port=0):
    return DeviceInfo(alias="testphone", fingerprint=cert_fingerprint(cert_path),
                      port=port, device_type="mobile")


def _prepare_with_cert(port, sender, files, cert, key, pin=None, timeout=15.0):
    import http.client
    import ssl as _ssl
    from protocol import build_prepare_upload, prepare_upload_path

    body = json.dumps(build_prepare_upload(sender, files)).encode()
    context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = _ssl.CERT_NONE
    if cert is not None and key is not None:
        context.load_cert_chain(str(cert), str(key))
    conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=timeout,
                                       context=context)
    try:
        conn.request("POST", prepare_upload_path(pin), body=body,
                     headers={"Content-Type": "application/json",
                              "Content-Length": str(len(body))})
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _register_with_cert(port, sender, cert, key, timeout=10.0):
    import http.client
    import ssl as _ssl

    body = json.dumps(sender.to_dict()).encode()
    context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = _ssl.CERT_NONE
    if cert is not None and key is not None:
        context.load_cert_chain(str(cert), str(key))
    conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=timeout,
                                       context=context)
    try:
        conn.request("POST", "/api/localsend/v2/register", body=body,
                     headers={"Content-Type": "application/json",
                              "Content-Length": str(len(body))})
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


@pytest.fixture()
def three_certs(tmp_path):
    server = ensure_certpair(tmp_path / "server-certs")
    victim = ensure_certpair(tmp_path / "victim-certs")
    attacker = ensure_certpair(tmp_path / "attacker-certs")
    return server, victim, attacker


def _favorite_service(tmp_path, victim_fp):
    from service import ShareService

    config = ShareConfig(tmp_path / "config")
    config.data["favorites"] = [victim_fp]
    config.data["auto_accept_favorites"] = True
    svc = ShareService(config, tmp_path / "c.pem", tmp_path / "k.pem",
                       "fp",
                       schedule=lambda _s, _cb: None,
                       downloads=tmp_path / "dl",
                       staging=tmp_path / "staging")
    return svc


def test_spoofed_fingerprint_never_auto_accepts(three_certs, tmp_path):
    (srv_cert, srv_key), (vic_cert, _vk), (atk_cert, atk_key) = three_certs
    victim_fp = cert_fingerprint(vic_cert)
    svc = _favorite_service(tmp_path, victim_fp)
    info = _server_info(srv_cert)
    decided = []
    srv = ShareServer(device_info=info, cert_path=srv_cert, key_path=srv_key,
                      bind_addrs=["127.0.0.1"], port=0,
                      staging_root=tmp_path / "staging",
                      download_dir=tmp_path / "dl",
                      decide=lambda session: decided.append(
                          session.session_id) or svc._decide(session),
                      decision_timeout=5.0)
    srv.start()
    try:
        port = srv.bound_ports[0]
        claimed = DeviceInfo(alias="victim", fingerprint=victim_fp)
        files = [UploadFile(id="f1", file_name="a.txt", size=1,
                            file_type="text/plain")]
        out = {}

        def run():
            out["res"] = _prepare_with_cert(port, claimed, files,
                                            atk_cert, atk_key)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        end = time.monotonic() + 10
        session = None
        while time.monotonic() < end:
            with srv.lock:
                if srv.sessions:
                    session = next(iter(srv.sessions.values()))
                    break
            time.sleep(0.01)
        assert session is not None
        assert session.verified is False
        assert decided and svc._decide(session) is False
        assert session.auto is False
        srv.cancel_session(session.session_id, declined=True)
        thread.join(timeout=10)
        assert out["res"][0] == 403
    finally:
        srv.stop()


def test_matching_cert_auto_accepts(three_certs, tmp_path):
    (srv_cert, srv_key), (vic_cert, vic_key) = three_certs[0], three_certs[1]
    victim_fp = cert_fingerprint(vic_cert)
    svc = _favorite_service(tmp_path, victim_fp)
    info = _server_info(srv_cert)
    srv = ShareServer(device_info=info, cert_path=srv_cert, key_path=srv_key,
                      bind_addrs=["127.0.0.1"], port=0,
                      staging_root=tmp_path / "staging",
                      download_dir=tmp_path / "dl",
                      decide=svc._decide,
                      decision_timeout=10.0)
    srv.start()
    try:
        port = srv.bound_ports[0]
        claimed = DeviceInfo(alias="victim", fingerprint=victim_fp)
        files = [UploadFile(id="f1", file_name="a.txt", size=1,
                            file_type="text/plain")]
        out = {}

        def run():
            out["res"] = _prepare_with_cert(port, claimed, files,
                                            vic_cert, vic_key)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert out["res"][0] == 200
        with srv.lock:
            session = next(iter(srv.sessions.values()))
        assert session.verified is True
        assert session.auto is True
    finally:
        srv.stop()


def test_no_client_cert_never_auto_accepts(three_certs, tmp_path):
    (srv_cert, srv_key), (vic_cert, _vk) = three_certs[0], three_certs[1]
    victim_fp = cert_fingerprint(vic_cert)
    svc = _favorite_service(tmp_path, victim_fp)
    info = _server_info(srv_cert)
    srv = ShareServer(device_info=info, cert_path=srv_cert, key_path=srv_key,
                      bind_addrs=["127.0.0.1"], port=0,
                      staging_root=tmp_path / "staging",
                      download_dir=tmp_path / "dl",
                      decide=svc._decide,
                      decision_timeout=2.0)
    srv.start()
    try:
        port = srv.bound_ports[0]
        claimed = DeviceInfo(alias="victim", fingerprint=victim_fp)
        files = [UploadFile(id="f1", file_name="a.txt", size=1,
                            file_type="text/plain")]
        status, _ = _prepare_with_cert(port, claimed, files, None, None,
                                       timeout=10.0)
        assert status == 403
        with srv.lock:
            session = next(iter(srv.sessions.values()))
        assert session.verified is False
        assert svc._decide(session) is False
    finally:
        srv.stop()


def test_unverified_prompt_marked(three_certs, tmp_path):
    (_srv, _sk), (vic_cert, _vk) = three_certs[0], three_certs[1]
    victim_fp = cert_fingerprint(vic_cert)
    svc = _favorite_service(tmp_path, victim_fp)
    seen = {}

    class Prompt:
        def __init__(self, summary, body, _on_action):
            seen["summary"] = summary
            seen["body"] = body

        def show(self):
            return True

        def close(self):
            pass

    svc.prompt_factory = Prompt
    from server import Session

    sender = DeviceInfo(alias="victim", fingerprint=victim_fp)
    files = {"f1": UploadFile(id="f1", file_name="a.txt", size=1,
                              file_type="text/plain")}
    svc._on_session(Session(session_id="s", sender=sender, files=files,
                            tokens={"f1": "t"}, verified=False))
    assert "(unverified)" in seen["summary"]
    svc._on_session(Session(session_id="s2", sender=sender, files=files,
                            tokens={"f1": "t"}, verified=True))
    assert "(unverified)" not in seen["summary"]


def test_register_mismatch_never_auto_accepts(three_certs, tmp_path):
    (srv_cert, srv_key), (vic_cert, _vk), (atk_cert, atk_key) = three_certs
    victim_fp = cert_fingerprint(vic_cert)
    svc = _favorite_service(tmp_path, victim_fp)
    peers = []
    info = _server_info(srv_cert)
    srv = ShareServer(device_info=info, cert_path=srv_cert, key_path=srv_key,
                      bind_addrs=["127.0.0.1"], port=0,
                      staging_root=tmp_path / "staging",
                      download_dir=tmp_path / "dl",
                      decide=svc._decide,
                      on_peer=lambda peer, addr="", verified=False: peers.append(
                          (peer, addr, verified)))
    srv.start()
    try:
        port = srv.bound_ports[0]
        claimed = DeviceInfo(alias="victim", fingerprint=victim_fp)
        status, _ = _register_with_cert(port, claimed, atk_cert, atk_key)
        assert status == 200
        assert peers and peers[0][2] is False
        sender = peers[0][0]
        from server import Session

        spoof = Session(session_id="x", sender=sender,
                        files={"f1": UploadFile(id="f1", file_name="a",
                                                size=1, file_type="x")},
                        tokens={"f1": "t"}, verified=peers[0][2])
        assert svc._decide(spoof) is False
    finally:
        srv.stop()


def test_server_requests_client_cert(tmp_path):
    import ssl as _ssl

    from server import _load_pyopenssl

    cert_pair = ensure_certpair(tmp_path / "certs")
    cert_path, key_path = cert_pair
    info = _server_info(cert_path)
    srv = ShareServer(device_info=info, cert_path=cert_path,
                      key_path=key_path, bind_addrs=["127.0.0.1"], port=0,
                      staging_root=tmp_path / "staging",
                      download_dir=tmp_path / "dl")
    server = srv._make_server("127.0.0.1")
    try:
        pyssl = _load_pyopenssl()
        if pyssl is not None:
            assert type(server).__name__ == "_PyOpenSSLHTTPServer"
            assert server._tls_context.get_verify_mode() == pyssl.VERIFY_PEER
        else:
            assert server.socket.context.verify_mode == _ssl.CERT_NONE
    finally:
        server.server_close()


def test_server_fallback_without_pyopenssl(tmp_path, monkeypatch):
    import ssl as _ssl
    import sys

    monkeypatch.setitem(sys.modules, "OpenSSL", None)
    monkeypatch.setitem(sys.modules, "OpenSSL.SSL", None)
    cert_pair = ensure_certpair(tmp_path / "certs")
    cert_path, key_path = cert_pair
    info = _server_info(cert_path)
    srv = ShareServer(device_info=info, cert_path=cert_path,
                      key_path=key_path, bind_addrs=["127.0.0.1"], port=0,
                      staging_root=tmp_path / "staging",
                      download_dir=tmp_path / "dl")
    server = srv._make_server("127.0.0.1")
    try:
        assert type(server).__name__ == "ThreadingHTTPServer"
        assert server.socket.context.verify_mode == _ssl.CERT_NONE
    finally:
        server.server_close()


def test_http_register_merges_into_peer_table(tmp_path):
    local = DeviceInfo(alias="me", fingerprint="aa" * 32)
    disc = Discovery(local, bind_addrs=[])
    remote = DeviceInfo(alias="you", fingerprint="bb" * 32)
    assert disc.live_peers() == []
    disc.note_http_peer(remote, "10.0.0.5")
    peers = disc.live_peers()
    assert len(peers) == 1
    assert peers[0].addr == "10.0.0.5"
    assert peers[0].info.alias == "you"


def test_http_register_peers_expire_like_multicast(tmp_path):
    now = [1000.0]
    local = DeviceInfo(alias="me", fingerprint="aa" * 32)
    disc = Discovery(local, bind_addrs=[], clock=lambda: now[0])
    disc.note_http_peer(DeviceInfo(alias="you", fingerprint="bb" * 32),
                        "10.0.0.5")
    assert len(disc.live_peers()) == 1
    now[0] += 600.0
    assert disc.live_peers() == []


def test_register_response_merges_into_peer_table():
    from protocol import encode_announce

    local = DeviceInfo(alias="me", fingerprint="aa" * 32)
    remote = DeviceInfo(alias="you", fingerprint="bb" * 32, port=54321)
    answered = DeviceInfo(alias="you-renamed", fingerprint="bb" * 32,
                          port=54321)
    disc = Discovery(local, bind_addrs=[],
                     register_fn=lambda *args: answered)

    class FakeSock:
        def sendto(self, *a):
            pass

    disc._sock = FakeSock()
    disc.handle_datagram(encode_announce(remote, True), ("10.0.0.9", 53317))
    assert disc.peers["bb" * 32].info.alias == "you-renamed"
    assert disc.peers["bb" * 32].addr == "10.0.0.9"


def test_server_registration_reaches_service_peer_list(three_certs, tmp_path):
    (srv_cert, srv_key), (vic_cert, _vk), (_atk, _ak) = three_certs
    victim_fp = cert_fingerprint(vic_cert)
    config = ShareConfig(tmp_path / "config")
    config.data["port"] = 0
    from service import ShareService

    svc = ShareService(config, srv_cert, srv_key,
                       cert_fingerprint(srv_cert),
                       schedule=lambda _s, _cb: None,
                       downloads=tmp_path / "dl",
                       staging=tmp_path / "staging")
    svc.start(["127.0.0.1"])
    try:
        port = svc.server.bound_ports[0]
        claimed = DeviceInfo(alias="late-joiner", fingerprint=victim_fp,
                             port=port)
        status, _ = _register_with_cert(port, claimed, None, None)
        assert status == 200
        end = time.monotonic() + 5
        found = []
        while time.monotonic() < end:
            found = [peer for peer in svc.peers()
                     if peer.info.fingerprint == victim_fp]
            if found:
                break
            time.sleep(0.05)
        assert found, "HTTP register never reached the peer list"
        assert found[0].addr in ("127.0.0.1", "::ffff:127.0.0.1")
        listed = svc.list_peers()
        assert any(item["fingerprint"] == victim_fp for item in listed)
    finally:
        svc.stop()
