"""TLS accept-any: xx-share client against xx-share server, real sockets.

The server requests a client cert but accepts any self-signed cert
(pyOpenSSL VERIFY_PEER with an always-true callback), then compares
sha256(peer_der) with the claimed fingerprint. Without pyOpenSSL it
falls back to CERT_NONE and treats every sender as unverified.
"""
import threading
import time

from certs import cert_fingerprint, ensure_certpair
from protocol import DeviceInfo, UploadFile
from server import ShareServer


def _server_pair(tmp_path):
    return ensure_certpair(tmp_path / "server-certs")


def _server_info(cert_path):
    return DeviceInfo(alias="testphone", fingerprint=cert_fingerprint(cert_path),
                      port=0, device_type="mobile")


def test_xxshare_client_verified_send(tmp_path):
    from client import send_files

    srv_cert, srv_key = _server_pair(tmp_path)
    cli_cert, cli_key = ensure_certpair(tmp_path / "client-certs")
    srv_fp = cert_fingerprint(srv_cert)
    cli_fp = cert_fingerprint(cli_cert)
    info = _server_info(srv_cert)
    srv = ShareServer(device_info=info, cert_path=srv_cert, key_path=srv_key,
                      bind_addrs=["127.0.0.1"], port=0,
                      staging_root=tmp_path / "staging",
                      download_dir=tmp_path / "dl",
                      decide=lambda _session: True,
                      decision_timeout=10.0)
    srv.start()
    try:
        port = srv.bound_ports[0]
        peer = DeviceInfo(alias="testphone", fingerprint=srv_fp, port=port)
        sender = DeviceInfo(alias="me", fingerprint=cli_fp)
        payload = tmp_path / "hello.txt"
        payload.write_bytes(b"hello verified xx-share\n")
        send_files(peer, "127.0.0.1", sender, [payload],
                   cert_path=str(cli_cert), key_path=str(cli_key))
        with srv.lock:
            session = next(iter(srv.sessions.values()))
        assert session.verified is True
        assert (tmp_path / "dl" / "hello.txt").read_bytes() == payload.read_bytes()
    finally:
        srv.stop()


def test_spoofed_claim_unverified_never_auto(tmp_path):
    from client import TransferError, prepare_upload
    from config import ShareConfig
    from service import ShareService

    srv_cert, srv_key = _server_pair(tmp_path)
    vic_cert, _vic_key = ensure_certpair(tmp_path / "victim-certs")
    atk_cert, atk_key = ensure_certpair(tmp_path / "attacker-certs")
    srv_fp = cert_fingerprint(srv_cert)
    victim_fp = cert_fingerprint(vic_cert)
    config = ShareConfig(tmp_path / "config")
    config.data["favorites"] = [victim_fp]
    config.data["auto_accept_favorites"] = True
    svc = ShareService(config, tmp_path / "c.pem", tmp_path / "k.pem",
                       "fp", schedule=lambda _s, _cb: None,
                       downloads=tmp_path / "dl",
                       staging=tmp_path / "staging")
    info = _server_info(srv_cert)
    srv = ShareServer(device_info=info, cert_path=srv_cert, key_path=srv_key,
                      bind_addrs=["127.0.0.1"], port=0,
                      staging_root=tmp_path / "staging",
                      download_dir=tmp_path / "dl",
                      decide=svc._decide,
                      decision_timeout=5.0)
    srv.start()
    try:
        port = srv.bound_ports[0]
        claimed = DeviceInfo(alias="victim", fingerprint=victim_fp)
        files = [UploadFile(id="f1", file_name="a.txt", size=1,
                            file_type="text/plain")]
        out = {}

        def run():
            try:
                prepare_upload("127.0.0.1", port, srv_fp, claimed, files,
                               cert_path=str(atk_cert), key_path=str(atk_key),
                               timeout=10.0)
            except TransferError as error:
                out["status"] = error.status

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
        assert session.auto is False
        assert svc._decide(session) is False
        srv.cancel_session(session.session_id, declined=True)
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert out.get("status") == 403
    finally:
        srv.stop()


def test_no_client_cert_unverified(tmp_path):
    from client import send_files

    srv_cert, srv_key = _server_pair(tmp_path)
    srv_fp = cert_fingerprint(srv_cert)
    info = _server_info(srv_cert)
    srv = ShareServer(device_info=info, cert_path=srv_cert, key_path=srv_key,
                      bind_addrs=["127.0.0.1"], port=0,
                      staging_root=tmp_path / "staging",
                      download_dir=tmp_path / "dl",
                      decide=lambda _session: True,
                      decision_timeout=10.0)
    srv.start()
    try:
        port = srv.bound_ports[0]
        peer = DeviceInfo(alias="testphone", fingerprint=srv_fp, port=port)
        sender = DeviceInfo(alias="anon", fingerprint="bb" * 32)
        payload = tmp_path / "plain.txt"
        payload.write_bytes(b"no client cert\n")
        send_files(peer, "127.0.0.1", sender, [payload])
        with srv.lock:
            session = next(iter(srv.sessions.values()))
        assert session.verified is False
        assert (tmp_path / "dl" / "plain.txt").read_bytes() == payload.read_bytes()
    finally:
        srv.stop()


def test_fallback_presenter_connects_unverified(tmp_path, monkeypatch):
    import sys

    from client import send_files

    monkeypatch.setitem(sys.modules, "OpenSSL", None)
    monkeypatch.setitem(sys.modules, "OpenSSL.SSL", None)
    srv_cert, srv_key = _server_pair(tmp_path)
    cli_cert, cli_key = ensure_certpair(tmp_path / "client-certs")
    srv_fp = cert_fingerprint(srv_cert)
    cli_fp = cert_fingerprint(cli_cert)
    info = _server_info(srv_cert)
    srv = ShareServer(device_info=info, cert_path=srv_cert, key_path=srv_key,
                      bind_addrs=["127.0.0.1"], port=0,
                      staging_root=tmp_path / "staging",
                      download_dir=tmp_path / "dl",
                      decide=lambda _session: True,
                      decision_timeout=10.0)
    srv.start()
    try:
        port = srv.bound_ports[0]
        peer = DeviceInfo(alias="testphone", fingerprint=srv_fp, port=port)
        sender = DeviceInfo(alias="me", fingerprint=cli_fp)
        payload = tmp_path / "fallback.txt"
        payload.write_bytes(b"fallback presenter\n")
        send_files(peer, "127.0.0.1", sender, [payload],
                   cert_path=str(cli_cert), key_path=str(cli_key))
        with srv.lock:
            session = next(iter(srv.sessions.values()))
        assert session.verified is False
        assert (tmp_path / "dl" / "fallback.txt").read_bytes() == payload.read_bytes()
    finally:
        srv.stop()
