"""The process-wide client: one instance, one lock, mapped errors."""

import sys
import threading
import types

import pytest

from xx_music.engine import EngineError, ErrorKind
from xx_radio.youtube import catalog


class StubClient:
    """Mirrors the YTMusic methods the engine uses, with canned answers."""

    def __init__(self):
        self.calls = []
        self.answers = {}
        self.failures = {}

    def _run(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name in self.failures:
            raise self.failures[name]
        return self.answers.get(name)

    def search(self, query, filter=None, scope=None, limit=20,
               ignore_spelling=False):
        return self._run("search", query, filter=filter, limit=limit)

    def get_home(self, limit=3):
        return self._run("get_home")

    def get_watch_playlist(self, videoId=None, playlistId=None, limit=25,
                           radio=False, shuffle=False):
        return self._run("get_watch_playlist", videoId=videoId, radio=radio)


def _catalog(client=None, auth=None, clock=None):
    kw = {}
    if clock is not None:
        kw["clock"] = clock
    return catalog.Catalog(auth=auth, client=client, **kw)


def test_fetch_returns_parsed_value_and_clears_health():
    client = StubClient()
    client.answers["get_home"] = [{"title": "s", "contents": []}]
    cat = _catalog(client)
    cat.health.note_failure("home", ErrorKind.SOURCE_CHANGED)
    assert cat.fetch("home", "get_home", lambda raw: raw) == [
        {"title": "s", "contents": []}]
    assert not cat.health.needs_update("home")
    assert cat.health.counts() == {}


def test_client_key_error_is_source_changed():
    client = StubClient()
    client.failures["get_watch_playlist"] = KeyError("endpoint")
    cat = _catalog(client)
    with pytest.raises(EngineError) as caught:
        cat.fetch("radio", "get_watch_playlist", lambda raw: raw,
                  videoId="x")
    assert caught.value.kind == ErrorKind.SOURCE_CHANGED


def test_engine_errors_pass_through_untouched():
    client = StubClient()
    boom = EngineError(ErrorKind.NOT_FOUND, "gone")
    client.failures["search"] = boom
    cat = _catalog(client)
    with pytest.raises(EngineError) as caught:
        cat.fetch("search", "search", lambda raw: raw, "x")
    assert caught.value is boom


def test_unknown_failures_are_other():
    client = StubClient()
    client.failures["search"] = RuntimeError("weird")
    cat = _catalog(client)
    with pytest.raises(EngineError) as caught:
        cat.fetch("search", "search", lambda raw: raw, "x")
    assert caught.value.kind == ErrorKind.OTHER


def test_broken_parse_counts_source_changed():
    client = StubClient()
    client.answers["search"] = [{"no": "shape"}]

    def parse(raw):
        raise EngineError(ErrorKind.SOURCE_CHANGED, "bad")

    cat = _catalog(client)
    with pytest.raises(EngineError) as caught:
        cat.fetch("search", "search", parse, "x")
    assert caught.value.kind == ErrorKind.SOURCE_CHANGED
    assert cat.health.counts() == {"search": 1}


def test_parse_bug_is_other_not_source_changed():
    client = StubClient()
    client.answers["search"] = []
    cat = _catalog(client)
    with pytest.raises(EngineError) as caught:
        cat.fetch("search", "search", lambda raw: 1 / 0, "x")
    assert caught.value.kind == ErrorKind.OTHER


def _stub_module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _install_requests(monkeypatch):
    class ConnectionError(Exception):
        pass

    class Timeout(Exception):
        pass

    class HTTPError(Exception):
        def __init__(self, status):
            super().__init__(f"http {status}")
            self.response = types.SimpleNamespace(status_code=status)

    exceptions = _stub_module("requests.exceptions", ConnectionError=ConnectionError,
                              Timeout=Timeout, HTTPError=HTTPError)
    package = _stub_module("requests", exceptions=exceptions)
    monkeypatch.setitem(sys.modules, "requests", package)
    monkeypatch.setitem(sys.modules, "requests.exceptions", exceptions)
    return ConnectionError, Timeout, HTTPError


def test_requests_errors_map_to_offline_timeout_limited(monkeypatch):
    conn, timeout, http = _install_requests(monkeypatch)
    cat = _catalog(StubClient())
    for failure, kind in ((conn("down"), ErrorKind.OFFLINE),
                          (timeout("slow"), ErrorKind.TIMEOUT),
                          (http(429), ErrorKind.RATE_LIMITED)):
        cat._client.failures["search"] = failure
        with pytest.raises(EngineError) as caught:
            cat.fetch("search", "search", lambda raw: raw, "x")
        assert caught.value.kind == kind


def test_http_auth_errors_follow_sign_in_state(monkeypatch):
    _, _, http = _install_requests(monkeypatch)
    anon = _catalog(StubClient())
    anon._client.failures["search"] = http(401)
    with pytest.raises(EngineError) as caught:
        anon.fetch("search", "search", lambda raw: raw, "x")
    assert caught.value.kind == ErrorKind.AUTH_REQUIRED
    signed = _catalog(StubClient(), auth={"cookie": "x"})
    signed._client.failures["search"] = http(401)
    with pytest.raises(EngineError) as caught:
        signed.fetch("search", "search", lambda raw: raw, "x")
    assert caught.value.kind == ErrorKind.AUTH_EXPIRED
    assert signed.signed_in and not anon.signed_in


def test_shared_client_is_one_per_process(monkeypatch):
    built = []

    def fake_build(auth):
        built.append(auth)
        return StubClient()

    monkeypatch.setattr(catalog, "_build_client", fake_build)
    catalog.reset_shared_clients()
    try:
        assert catalog.shared_client() is catalog.shared_client()
        assert catalog.shared_client(auth={"cookie": "x"}) is not \
            catalog.shared_client()
        assert len(built) == 2
    finally:
        catalog.reset_shared_clients()


def test_process_lock_serialises_calls():
    import time

    client = StubClient()
    active = 0
    overlap = []

    def slow():
        nonlocal active
        active += 1
        overlap.append(active)
        time.sleep(0.01)
        active -= 1
        return []

    client.get_home = slow
    cat = _catalog(client)
    threads = [threading.Thread(target=cat.fetch,
                                args=("home", "get_home", lambda raw: raw))
               for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert overlap == [1] * 8


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_health_trips_on_three_and_clears_on_success():
    clock = FakeClock()
    health = catalog.Health(clock)
    assert not health.needs_update("search")
    health.note_failure("search", ErrorKind.SOURCE_CHANGED)
    health.note_failure("search", ErrorKind.OTHER)
    assert not health.needs_update("search")
    health.note_failure("search", ErrorKind.SOURCE_CHANGED)
    assert not health.needs_update("search")
    health.note_failure("search", ErrorKind.HELPER_MISSING)
    assert health.needs_update("search")
    assert health.counts() == {"search": 3}
    health.note_success("search")
    assert not health.needs_update("search")
    assert health.counts() == {}


def test_health_window_expires():
    clock = FakeClock()
    health = catalog.Health(clock)
    for _ in range(3):
        health.note_failure("home", ErrorKind.SOURCE_CHANGED)
    assert health.needs_update("home")
    clock.now += catalog.WINDOW_S + 1
    assert not health.needs_update("home")
    assert health.counts() == {}
