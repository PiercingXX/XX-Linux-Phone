"""Accept policy and visibility timers without GTK or network."""
import service as service_module
from config import ShareConfig
from notify import ACTION_ACCEPT, ACTION_DECLINE
from service import ShareService


class FakeServer:
    last_kwargs = None

    def __init__(self, **kwargs):
        FakeServer.last_kwargs = kwargs
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True


class FakeDiscovery:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def _service(tmp_path, monkeypatch, **overrides):
    monkeypatch.setattr(service_module, "ShareServer", FakeServer)
    monkeypatch.setattr(service_module, "Discovery", FakeDiscovery)
    config = ShareConfig(tmp_path / "config")
    for key, value in overrides.items():
        config.data[key] = value
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
    return svc, scheduled


def test_auto_accept_needs_favorite_and_setting(tmp_path, monkeypatch):
    svc, _ = _service(tmp_path, monkeypatch)
    assert svc.should_auto_accept("fp") is False
    svc.config.data["auto_accept_favorites"] = True
    assert svc.should_auto_accept("fp") is False
    svc.config.data["favorites"] = ["fp"]
    assert svc.should_auto_accept("fp") is True
    assert svc.should_auto_accept("other") is False


def test_auto_off_fires_and_stops(tmp_path, monkeypatch):
    svc, scheduled = _service(tmp_path, monkeypatch)
    svc.start(["127.0.0.1"])
    assert len(scheduled) == 1
    seconds, callback = scheduled[0]
    assert seconds == 600
    assert svc.running
    callback()
    assert not svc.running
    assert svc.config.data["visible"] is False


def test_always_mode_skips_timer(tmp_path, monkeypatch):
    svc, scheduled = _service(tmp_path, monkeypatch, visibility_mode="always")
    svc.start(["127.0.0.1"])
    assert scheduled == []
    assert svc.running
    svc.stop()
    assert not svc.running


def test_device_info_matches_protocol_messaging(tmp_path):
    from service import build_device_info

    config = ShareConfig(tmp_path / "config")
    info = build_device_info(config, "ab" * 32)
    assert info.version == "2.2"
    assert info.device_type == "mobile"
    assert info.protocol == "https"
    assert info.fingerprint == "ab" * 32


def test_resolve_accept_and_decline(tmp_path, monkeypatch):
    svc, _ = _service(tmp_path, monkeypatch)
    delivered = []
    declined = []

    class Impl:
        def accept_session(self, sid):
            delivered.append(sid)
            return []

        def decline_session(self, sid):
            declined.append(sid)

    svc.server = Impl()
    svc._resolve("s1", ACTION_ACCEPT)
    svc._resolve("s2", ACTION_DECLINE)
    assert delivered == ["s1"]
    assert declined == ["s2"]
