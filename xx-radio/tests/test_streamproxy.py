"""The local stream proxy against a fake googlevideo-like upstream."""

import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from xx_music.engine import StreamSource
from xx_music.streamproxy import StreamProxy

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch):
    """Loopback HTTP is local plumbing, not the network: allow it here."""

    def guard(sock, *args):
        target = args[0] if args else None
        host = target[0] if isinstance(target, tuple) else ""
        if host in ("127.0.0.1", "::1", "localhost"):
            return _REAL_CONNECT(sock, *args)
        raise AssertionError("tests must not use the network")

    def guard_ex(sock, *args):
        target = args[0] if args else None
        host = target[0] if isinstance(target, tuple) else ""
        if host in ("127.0.0.1", "::1", "localhost"):
            return _REAL_CONNECT_EX(sock, *args)
        raise AssertionError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard_ex)


DATA = bytes(i % 251 for i in range(100))


class FakeUpstream:
    """Serves DATA with googlevideo-like rules: only small ranged fetches,
    403 otherwise, plus an expirable token."""

    def __init__(self, chunk_limit=16):
        self.chunk_limit = chunk_limit
        self.allowed = {"good"}
        self.required_headers = {}
        self.calls = []
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02},
                                       daemon=True)
        self.thread.start()

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                query = urllib.parse.parse_qs(
                    urllib.parse.urlparse(self.path).query)
                token = query.get("t", [""])[0]
                seen = {key.lower(): value
                        for key, value in self.headers.items()}
                outer.calls.append(
                    (self.command, token, self.headers.get("Range"),
                     dict(seen)))
                for key, value in outer.required_headers.items():
                    if seen.get(key.lower(), "") != value:
                        self._fail(400, "missing required header")
                        return
                if token not in outer.allowed:
                    self._fail(403, "forbidden")
                    return
                raw = self.headers.get("Range", "")
                if not raw or not raw.startswith("bytes="):
                    self._fail(403, "range required")
                    return
                try:
                    first_text, _, last_text = raw[6:].partition("-")
                    first = int(first_text)
                    last = int(last_text) if last_text else len(DATA) - 1
                except ValueError:
                    self._fail(400, "bad range")
                    return
                if last < first or first >= len(DATA):
                    self._fail(416, "out of range")
                    return
                last = min(last, len(DATA) - 1)
                if last - first + 1 > outer.chunk_limit:
                    self._fail(403, "fetch too large")
                    return
                body = DATA[first:last + 1]
                self.send_response(206)
                self.send_header("Content-Type", "audio/webm")
                self.send_header(
                    "Content-Range",
                    f"bytes {first}-{last}/{len(DATA)}")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def _fail(self, code, text):
                body = text.encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def url(self, token="good"):
        return f"http://127.0.0.1:{self.port}/audio?t={token}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture()
def upstream():
    server = FakeUpstream()
    yield server
    server.close()


@pytest.fixture()
def proxy(tmp_path):
    proxy = StreamProxy(tmp_path / "cache", chunk_size=16)
    proxy.start()
    yield proxy
    proxy.stop()


def _source(upstream, overallow=True, **overrides):
    fields = dict(uri=upstream.url("good" if overallow else "bad"),
                  headers={"X-Auth": "s3cret"},
                  mime="audio/webm",
                  expires_at=time.time() + 3600,
                  size_bytes=len(DATA))
    fields.update(overrides)
    return StreamSource(**fields)


def _get(url, start=None, end=None, method="GET"):
    request = urllib.request.Request(url, method=method)
    if start is not None:
        request.add_header(
            "Range", f"bytes={start}-{'' if end is None else end}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, dict(response.headers), response.read()


def test_full_fetch_tees_cache_and_renames(proxy, upstream, tmp_path):
    upstream.required_headers = {"X-Auth": "s3cret"}
    local = proxy.url_for("tr-001", lambda: _source(upstream))
    status, headers, body = _get(local)
    assert status == 200
    assert body == DATA
    assert headers.get("Accept-Ranges") == "bytes"
    assert upstream.calls
    for _, _, raw, _ in upstream.calls:
        first_text, _, last_text = raw[6:].partition("-")
        assert int(last_text) - int(first_text) + 1 <= 16
    assert (tmp_path / "cache" / "tr-001").read_bytes() == DATA
    assert not (tmp_path / "cache" / "tr-001.part").exists()
    assert proxy.is_cached("tr-001")
    assert not proxy.is_cached("tr-xxx")


def test_range_seeks(proxy, upstream):
    local = proxy.url_for("tr-002", lambda: _source(upstream))
    status, headers, body = _get(local, 10, 29)
    assert status == 206
    assert body == DATA[10:30]
    assert headers.get("Content-Range") == f"bytes 10-29/{len(DATA)}"
    status, _, body = _get(local, 90, None)
    assert status == 206
    assert body == DATA[90:]
    status, _, body = _get(local, 0, None)
    assert status == 206
    assert body == DATA


def test_cached_file_served_with_no_upstream_call(proxy, upstream):
    calls = []

    def resolve():
        calls.append(1)
        return _source(upstream)

    local = proxy.url_for("tr-003", resolve)
    assert _get(local)[2] == DATA
    assert calls
    upstream.calls.clear()
    calls.clear()
    upstream.close()  # the origin is gone; disk must be enough
    status, _, body = _get(local)
    assert status == 200
    assert body == DATA
    assert calls == []


def test_reresolve_on_403(proxy, upstream):
    seen = []

    def resolve():
        token = "good" if seen else "bad"
        seen.append(token)
        return _source(upstream, overallow=(token == "good"))

    local = proxy.url_for("tr-004", resolve)
    assert _get(local)[2] == DATA
    assert seen == ["bad", "good"]
    tokens = [call[1] for call in upstream.calls]
    assert "bad" in tokens and "good" in tokens


def test_expired_source_reresolves_before_fetching(proxy, upstream):
    seen = []

    def resolve():
        seen.append(1)
        if len(seen) == 1:
            return _source(upstream, expires_at=time.time() - 10)
        return _source(upstream)

    local = proxy.url_for("tr-005", resolve)
    assert _get(local)[2] == DATA
    assert len(seen) == 2
    assert {call[1] for call in upstream.calls} == {"good"}


def test_tokens_are_unique_and_unknown_is_404(proxy, upstream):
    first = proxy.url_for("tr-006", lambda: _source(upstream))
    second = proxy.url_for("tr-006", lambda: _source(upstream))
    assert first != second
    assert first.startswith("http://127.0.0.1:")
    assert _get(first)[2] == DATA
    assert _get(second)[2] == DATA
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(first + "bogus")
    assert caught.value.code == 404


def test_head_and_unsatisfiable_range(proxy, upstream):
    local = proxy.url_for("tr-007", lambda: _source(upstream))
    status, headers, body = _get(local, 0, 9, method="HEAD")
    assert status == 206
    assert body == b""
    assert headers.get("Content-Range") == f"bytes 0-9/{len(DATA)}"
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(local, len(DATA) + 50, None)
    assert caught.value.code == 416


def test_bad_track_ids_rejected(proxy):
    with pytest.raises(ValueError):
        proxy.url_for("../escape", lambda: _source(upstream))
    with pytest.raises(ValueError):
        proxy.url_for("", lambda: _source(upstream))
    with pytest.raises(RuntimeError):
        StreamProxy(proxy._cache_dir).url_for(
            "tr-008", lambda: _source(upstream))
