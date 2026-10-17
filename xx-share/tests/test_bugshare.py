"""Regression tests for the bug_share review findings."""
import errno
import http.client
import json
import ssl
import time
from pathlib import Path
from types import SimpleNamespace

from certs import cert_fingerprint
from config import ShareConfig
from protocol import DeviceInfo


def _make_server(tmp_path, **overrides):
    from server import ShareServer

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
    )
    kwargs.update(overrides)
    srv = ShareServer(**kwargs)
    srv.start()
    return srv


def test_cross_filesystem_delivery_survives_exdev(tmp_path, monkeypatch):
    """Finding 1: staging in /tmp + rename() loses files across filesystems."""
    def fake_rename(self, target):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(Path, "rename", fake_rename)
    srv = _make_server(tmp_path)
    try:
        sender = DeviceInfo(alias="sender", fingerprint="ff")
        from protocol import UploadFile

        session = srv.create_session(
            sender, [UploadFile(id="f1", file_name="photo.jpg", size=5,
                                file_type="image/jpeg")])
        srv.accept_session(session.session_id)
        staged = srv.stage_path(session, "f1")
        staged.write_bytes(b"hello")
        with srv.lock:
            session.received.add("f1")
        srv.session_complete(session)
        target = srv.download_dir / "photo.jpg"
        assert target.read_bytes() == b"hello"
    finally:
        srv.stop()


def _service_with_fakes(tmp_path, monkeypatch):
    import service as service_module
    from service import ShareService

    class FakeServer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            pass

        def stop(self):
            pass

    class FakeDiscovery:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(service_module, "ShareServer", FakeServer)
    monkeypatch.setattr(service_module, "Discovery", FakeDiscovery)
    config = ShareConfig(tmp_path / "config")
    config.save()
    scheduled = []
    svc = ShareService(
        config,
        tmp_path / "cert.pem",
        tmp_path / "key.pem",
        "fp",
        schedule=lambda seconds, cb: scheduled.append((seconds, cb)) or None,
        downloads=tmp_path / "dl",
        staging=tmp_path / "staging",
    )
    return svc


def _gui_rewrite(config_dir, **changes):
    path = Path(config_dir) / "config.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(changes)
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def test_decide_picks_up_gui_favorite_change(tmp_path, monkeypatch):
    """Finding 16: a GUI-added favorite must auto-accept without restart."""
    svc = _service_with_fakes(tmp_path, monkeypatch)
    session = SimpleNamespace(
        sender=SimpleNamespace(fingerprint="fp"), verified=True)
    assert svc._decide(session) is False
    _gui_rewrite(tmp_path / "config",
                 auto_accept_favorites=True, favorites=["fp"])
    assert svc._decide(session) is True


def test_set_visible_keeps_gui_pin(tmp_path, monkeypatch):
    """Finding 16: set_visible must not overwrite a GUI-set PIN."""
    svc = _service_with_fakes(tmp_path, monkeypatch)
    svc.config.data["require_pin"] = ""
    svc.config.save()
    _gui_rewrite(tmp_path / "config", require_pin="2468")
    svc.set_visible(False)
    fresh = ShareConfig(tmp_path / "config")
    assert fresh.data["require_pin"] == "2468"
    assert fresh.data["visible"] is False


def test_service_pin_getter_reloads_file(tmp_path, monkeypatch):
    """Finding 16: the running server must read the GUI-written PIN."""
    import service as service_module

    captured = {}

    class FakeServer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            pass

        def stop(self):
            pass

    class FakeDiscovery:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(service_module, "ShareServer", FakeServer)
    monkeypatch.setattr(service_module, "Discovery", FakeDiscovery)
    from service import ShareService

    config = ShareConfig(tmp_path / "config")
    config.save()
    svc = ShareService(config, tmp_path / "c.pem", tmp_path / "k.pem", "fp",
                       schedule=lambda _s, _cb: None,
                       downloads=tmp_path / "dl", staging=tmp_path / "st")
    svc.start(["127.0.0.1"])
    try:
        assert "require_pin_getter" in captured
        assert captured["require_pin_getter"]() == ""
        _gui_rewrite(tmp_path / "config", require_pin="2468")
        assert captured["require_pin_getter"]() == "2468"
    finally:
        svc.stop()


def _idle_freeze_check(tmp_path, monkeypatch=None):
    import socket as std_socket
    import threading as std_threading

    if monkeypatch is not None:
        import server as server_module

        monkeypatch.setattr(server_module, "_load_pyopenssl", lambda: None)
    srv = _make_server(tmp_path)
    try:
        port = srv.bound_ports[0]
        probe = std_socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            time.sleep(0.5)
            conn = http.client.HTTPSConnection(
                "127.0.0.1", port, timeout=10, context=_ctx())
            try:
                conn.request("GET", "/api/localsend/v2/info")
                response = conn.getresponse()
                status, raw = response.status, response.read()
            finally:
                conn.close()
            assert status == 200
            assert json.loads(raw.decode())["alias"] == "testphone"
        finally:
            probe.close()
    finally:
        finished = std_threading.Event()

        def _stop():
            srv.stop()
            finished.set()

        std_threading.Thread(target=_stop, daemon=True).start()
        assert finished.wait(timeout=20), "stop() froze on idle connection"


def test_idle_connection_does_not_freeze_receiver(tmp_path):
    """Finding 23: an idle TCP peer must not block later connections."""
    _idle_freeze_check(tmp_path)


def test_idle_connection_does_not_freeze_stdlib_receiver(tmp_path,
                                                         monkeypatch):
    """Finding 23: same guarantee on the stdlib TLS path."""
    _idle_freeze_check(tmp_path, monkeypatch)


def test_abandoned_session_expires_and_frees_receiver(tmp_path):
    """Finding 24: a dead sender must not block later transfers."""
    from protocol import UploadFile

    srv = _make_server(tmp_path, session_idle_timeout=0.3,
                       decide=lambda _session: True)
    try:
        sender = DeviceInfo(alias="sender", fingerprint="ff")
        session = srv.create_session(
            sender, [UploadFile(id="f1", file_name="hello.txt", size=5,
                                file_type="text/plain")])
        srv.accept_session(session.session_id)
        assert srv.has_active_session() is True
        time.sleep(0.6)
        assert srv.has_active_session() is False
        port = srv.bound_ports[0]
        status, raw = _prepare(port)
        assert status == 200
        srv.cancel_session(json.loads(raw.decode())["sessionId"])
        assert not (srv.staging_root / session.session_id).exists()
    finally:
        srv.stop()


def test_upload_progress_keeps_session_alive(tmp_path):
    """Finding 24: an actively uploading session must not expire."""
    import threading as std_threading

    from protocol import UploadFile

    srv = _make_server(tmp_path, session_idle_timeout=0.4,
                       decide=lambda _session: True)
    try:
        port = srv.bound_ports[0]
        sender = DeviceInfo(alias="sender", fingerprint="ff")
        slow = UploadFile(id="f1", file_name="big.bin", size=200000,
                          file_type="application/octet-stream")
        results = {}

        def _run():
            body = {"info": sender.to_dict(),
                    "files": {slow.id: slow.to_dict()}}
            raw = json.dumps(body).encode()
            results["prepare"] = _post(
                port, "/api/localsend/v2/prepare-upload", raw,
                {"Content-Type": "application/json",
                 "Content-Length": str(len(raw))})

        thread = std_threading.Thread(target=_run, daemon=True)
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
        srv.accept_session(session.session_id)
        thread.join(timeout=10)
        assert thread.is_alive() is False
        payload = json.loads(results["prepare"][1].decode())
        session_id = payload["sessionId"]
        token = payload["files"]["f1"]

        def _trickle():
            import http.client as _http
            import ssl as _ssl

            context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = _ssl.CERT_NONE
            conn = _http.HTTPSConnection("127.0.0.1", port, timeout=10,
                                         context=context)
            try:
                conn.putrequest(
                    "POST", f"/api/localsend/v2/upload?sessionId={session_id}"
                    f"&fileId=f1&token={token}")
                conn.putheader("Content-Type", "application/octet-stream")
                conn.putheader("Content-Length", "200000")
                conn.endheaders()
                chunk = b"x" * 10000
                for _ in range(20):
                    conn.send(chunk)
                    time.sleep(0.1)
                return conn.getresponse().status
            finally:
                conn.close()

        up_thread = std_threading.Thread(
            target=lambda: results.update(upload=_trickle()), daemon=True)
        up_thread.start()
        up_thread.join(timeout=15)
        assert results.get("upload") == 200
        assert (srv.download_dir / "big.bin").stat().st_size == 200000
    finally:
        srv.stop()


def test_send_skips_files_without_tokens(tmp_path, monkeypatch):
    """Finding 25: a partial accept must not crash the sender."""
    import threading as std_threading
    from dataclasses import replace as _replace

    import client as client_module
    from client import describe_files, prepare_upload, send_files

    srv = _make_server(tmp_path)
    try:
        blk0 = tmp_path / "part0.txt"
        blk1 = tmp_path / "part1.txt"
        blk0.write_bytes(b"zero")
        blk1.write_bytes(b"one!")
        port = srv.bound_ports[0]
        peer = _replace(srv.device_info, port=port)
        sender = DeviceInfo(alias="sender", fingerprint="ff")
        files = describe_files([blk0, blk1])
        out = {}
        asked = std_threading.Thread(
            target=lambda: out.update(res=prepare_upload(
                "127.0.0.1", port, peer.fingerprint, sender, files)),
            daemon=True)
        asked.start()
        end = time.monotonic() + 10
        session = None
        while time.monotonic() < end:
            with srv.lock:
                if srv.sessions:
                    session = next(iter(srv.sessions.values()))
                    break
            time.sleep(0.01)
        assert session is not None
        srv.accept_session(session.session_id, ["file-0"])
        asked.join(timeout=10)
        assert asked.is_alive() is False
        session_id, tokens = out["res"]
        assert set(tokens) == {"file-0"}

        def _fake_prepare(*_args, **_kwargs):
            return session_id, tokens

        monkeypatch.setattr(client_module, "prepare_upload", _fake_prepare)
        send_files(peer, "127.0.0.1", sender, [blk0, blk1])
        assert (srv.download_dir / "part0.txt").read_bytes() == b"zero"
        assert not (srv.download_dir / "part1.txt").exists()
    finally:
        srv.stop()


def test_wrong_pins_are_rate_limited(tmp_path):
    """Finding 32: PIN guessing must count against the rate limit."""
    srv = _make_server(tmp_path)
    srv.require_pin = "2468"
    srv.rate_limit_max = 3
    try:
        port = srv.bound_ports[0]
        for _ in range(3):
            status, _ = _prepare(port, pin="0000")
            assert status == 401
        status, _ = _prepare(port, pin="0000")
        assert status == 429
    finally:
        srv.stop()


def test_non_ascii_pin_gets_401_not_dropped(tmp_path):
    """Finding 32: a non-ASCII PIN must be rejected, not hang up."""
    import http.client as _http

    srv = _make_server(tmp_path)
    srv.require_pin = "2468"
    try:
        port = srv.bound_ports[0]
        try:
            status, _ = _prepare(port, pin="é")
        except (_http.RemoteDisconnected, ConnectionResetError):
            raise AssertionError("connection dropped on non-ASCII PIN")
        assert status == 401
    finally:
        srv.stop()


def test_restart_cancels_old_auto_off(tmp_path, monkeypatch):
    """Finding 32: stop/start must not let the old timer kill the session."""
    class FakeTimer:
        def __init__(self, callback):
            self.callback = callback
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

        def fire(self):
            if not self.cancelled:
                self.callback()

    timers = []
    svc = _service_with_fakes(tmp_path, monkeypatch)
    svc.schedule = lambda _s, cb: timers.append(FakeTimer(cb)) or timers[-1]
    svc.start(["127.0.0.1"])
    assert svc.running
    first = timers[-1]
    svc.stop()
    svc.start(["127.0.0.1"])
    assert svc.running
    first.fire()
    assert svc.running
    timers[-1].fire()
    assert not svc.running


def test_sanitize_truncates_by_bytes():
    """Finding 32: 255 characters of non-ASCII must fit in 255 bytes."""
    from sanitize import sanitize_filename

    out = sanitize_filename("é" * 200 + ".txt")
    assert len(out.encode("utf-8")) <= 255
    assert out.endswith(".txt")
    out = sanitize_filename("ß" * 300)
    assert len(out.encode("utf-8")) <= 255


def test_send_thread_always_reports_done(tmp_path, monkeypatch):
    """Finding 25: TransferDone fires even on an unexpected send error."""
    import threading as std_threading

    import client as client_module
    import service as service_module
    from service import ShareService

    class FakeDiscovery:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(service_module, "Discovery", FakeDiscovery)

    def _boom(*_args, **_kwargs):
        raise KeyError("file-1")

    monkeypatch.setattr(client_module, "send_files", _boom)
    from config import ShareConfig as _ShareConfig

    config = _ShareConfig(tmp_path / "config")
    svc = ShareService(config, tmp_path / "c.pem", tmp_path / "k.pem", "fp",
                       schedule=lambda _s, _cb: None,
                       downloads=tmp_path / "dl", staging=tmp_path / "st")
    peer = SimpleNamespace(info=SimpleNamespace(fingerprint="fp",
                                                alias="peer",
                                                port=53317),
                           addr="127.0.0.1")
    svc.discovery = SimpleNamespace(live_peers=lambda: [peer])
    seen = {}
    done = std_threading.Event()
    svc.on_signal = lambda name, args: seen.update({name: args})
    finished = {}

    def _on_done(ok, message):
        finished["ok"] = ok
        finished["message"] = message
        done.set()

    transfer_id = svc.start_send("fp", ["a.txt"], on_done=_on_done)
    assert done.wait(timeout=10), "TransferDone was never emitted"
    assert seen.get("TransferDone", (None, None, None))[1] is False
    assert finished["ok"] is False
    assert svc.outgoing.describe(transfer_id) is None
    with svc._lock:
        assert transfer_id not in svc._send_threads


def _ctx():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _post(port, path, body=b"", headers=None):
    conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=10,
                                       context=_ctx())
    try:
        conn.request("POST", path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _prepare(port, pin=None):
    from protocol import UploadFile

    sender = DeviceInfo(alias="sender", fingerprint="ff")
    item = UploadFile(id="f1", file_name="hello.txt", size=5,
                      file_type="text/plain")
    body = {"info": sender.to_dict(), "files": {item.id: item.to_dict()}}
    from urllib.parse import quote as _quote

    raw = json.dumps(body).encode()
    path = "/api/localsend/v2/prepare-upload"
    if pin is not None:
        path += f"?pin={_quote(pin, safe='')}"
    return _post(port, path, raw, {"Content-Type": "application/json",
                                   "Content-Length": str(len(raw))})


def test_prepare_enforces_gui_pin_without_restart(tmp_path):
    """Finding 16: a GUI-set PIN must apply to the running receiver."""
    from server import ShareServer

    from certs import ensure_certpair

    cert_path, key_path = ensure_certpair(tmp_path / "certs")
    fingerprint = cert_fingerprint(cert_path)
    info = DeviceInfo(alias="testphone", fingerprint=fingerprint,
                      port=0, device_type="mobile")
    config = ShareConfig(tmp_path / "config")
    config.save()

    def pin_now():
        config.load()
        return str(config.data.get("require_pin", ""))

    srv = ShareServer(
        device_info=info, cert_path=cert_path, key_path=key_path,
        bind_addrs=["127.0.0.1"], port=0,
        staging_root=tmp_path / "staging",
        download_dir=tmp_path / "downloads",
        decide=lambda _session: True,
        require_pin_getter=pin_now,
    )
    srv.start()
    try:
        port = srv.bound_ports[0]
        status, raw = _prepare(port)
        assert status == 200
        first_id = json.loads(raw.decode())["sessionId"]
        srv.cancel_session(first_id)
        _gui_rewrite(tmp_path / "config", require_pin="2468")
        status, _ = _prepare(port)
        assert status == 401
        status, raw = _prepare(port, pin="2468")
        assert status == 200
        srv.cancel_session(json.loads(raw.decode())["sessionId"])
    finally:
        srv.stop()
