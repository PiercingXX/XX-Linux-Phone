"""A local loopback proxy between the player and an audio source.

The player never plays a raw upstream address directly: some origins only
answer when every fetch stays under a small size, and their addresses stop
working after a while. This proxy owns that: its downstream side gives the
player a seekable ``http://127.0.0.1:<port>/t/<token>`` with Range support,
while its upstream side fetches in slices of at most ``chunk_size`` bytes,
asks the engine for a fresh address on 403/expiry, and tees bytes into the
cache directory (``<id>.part`` while partial, renamed when complete). A
fully cached track is served from disk with no upstream call at all.

Only stdlib here, so the core stays dependency-free. Every method blocks
and is called from worker threads, never from the GTK main loop.
"""

from __future__ import annotations

import http.client
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from xx_music.engine import StreamSource

__all__ = ["StreamProxy"]

_DEFAULT_MIME = "application/octet-stream"
_REDIRECTS = (301, 302, 303, 307, 308)
_UPSTREAM_TIMEOUT_S = 20


def _check_track_id(track_id: str) -> str:
    if (not track_id or not isinstance(track_id, str)
            or "/" in track_id or "\\" in track_id or ".." in track_id
            or "\x00" in track_id or track_id.startswith(".")):
        raise ValueError(f"bad track id: {track_id!r}")
    return track_id


class _UpstreamFailed(Exception):
    pass


class _Entry:
    __slots__ = ("track_id", "resolve", "source")

    def __init__(self, track_id: str,
                 resolve: Callable[[], StreamSource]) -> None:
        self.track_id = track_id
        self.resolve = resolve
        self.source: StreamSource | None = None


def _parse_range(value: str | None
                 ) -> tuple | None:
    """Parse a Range header into ("all",), ("span", first, last|None) or
    ("suffix", count). Returns None when absent or not parseable."""
    if not value or not value.strip().lower().startswith("bytes="):
        return None
    spec = value.strip()[6:].strip()
    if spec.startswith("-"):
        try:
            count = int(spec[1:])
        except ValueError:
            return None
        return ("suffix", count) if count > 0 else None
    first_text, sep, last_text = spec.partition("-")
    if not sep:
        return None
    try:
        first = int(first_text)
    except ValueError:
        return None
    if first < 0:
        return None
    if not last_text:
        return ("span", first, None)
    try:
        last = int(last_text)
    except ValueError:
        return None
    return ("span", first, last) if last >= first else None


def _content_range_total(value: str | None) -> int | None:
    """Total length from a Content-Range value (``bytes 0-9/123``)."""
    if not value or "/" not in value:
        return None
    total_text = value.rsplit("/", 1)[1].strip()
    if total_text == "*":
        return None
    try:
        total = int(total_text)
    except ValueError:
        return None
    return total if total >= 0 else None


def _fetch_range(url: str, headers: dict[str, str], first: int,
                 length: int) -> tuple[int, dict[str, str], bytes]:
    """GET ``url`` with a Range header, following redirects (custom headers
    are dropped when the origin changes). Returns status, headers, body."""
    wanted = dict(headers)
    for _ in range(6):
        parts = urllib.parse.urlparse(url)
        origin = (parts.scheme, parts.hostname or "", parts.port)
        connection: http.client.HTTPConnection = (
            http.client.HTTPSConnection(origin[1], origin[2] or 443,
                                        timeout=_UPSTREAM_TIMEOUT_S)
            if parts.scheme == "https"
            else http.client.HTTPConnection(origin[1], origin[2] or 80,
                                            timeout=_UPSTREAM_TIMEOUT_S))
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        request_headers = dict(wanted)
        request_headers["Range"] = f"bytes={first}-{first + length - 1}"
        request_headers.setdefault("Connection", "close")
        try:
            connection.request("GET", path, headers=request_headers)
            response = connection.getresponse()
            status = response.status
            response_headers = {k.lower(): v
                                for k, v in response.getheaders()}
            body = response.read()
        finally:
            connection.close()
        if status in _REDIRECTS:
            target = response_headers.get("location", "")
            if not target:
                return status, response_headers, body
            url = urllib.parse.urljoin(url, target)
            new = urllib.parse.urlparse(url)
            if (new.scheme, new.hostname or "",
                    new.port) != origin:
                wanted = {}
            continue
        return status, response_headers, body
    raise _UpstreamFailed(f"too many redirects for {url}")


class StreamProxy:
    """Serve audio to the player from a local seekable address.

    ``cache_dir`` holds ``<track_id>`` (complete) and ``<track_id>.part``
    (partial) files. ``chunk_size`` caps every upstream fetch.
    """

    def __init__(self, cache_dir: Path,
                 chunk_size: int = 10 * 2**20) -> None:
        if int(chunk_size) <= 0:
            raise ValueError("chunk_size must be positive")
        self._cache_dir = Path(cache_dir)
        self._chunk_size = int(chunk_size)
        self._entries: dict[str, _Entry] = {}
        self._entries_lock = threading.Lock()
        self._track_locks: dict[str, threading.Lock] = {}
        self._mime_cache: dict[str, str] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = 0

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        """Bind 127.0.0.1 on an ephemeral port and serve in the background."""
        if self._server is not None:
            return
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        proxy = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                proxy._serve(self, head_only=False)

            def do_HEAD(self) -> None:  # noqa: N802
                proxy._serve(self, head_only=True)

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        self._server = server
        self._port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05},
                                  daemon=True, name="xx-stream-proxy")
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        self._port = 0
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def url_for(self, track_id: str,
                resolve: Callable[[], StreamSource]) -> str:
        """A local URL playing ``track_id``; ``resolve`` is called for a
        fresh upstream address whenever the cached file is missing and the
        current address fails or expires."""
        _check_track_id(track_id)
        if self._server is None:
            raise RuntimeError("proxy is not started")
        if not callable(resolve):
            raise TypeError("resolve must be callable")
        token = secrets.token_urlsafe(16)
        with self._entries_lock:
            self._entries[token] = _Entry(track_id, resolve)
        return f"http://127.0.0.1:{self._port}/t/{token}"

    def is_cached(self, track_id: str) -> bool:
        return self._complete_path(track_id).is_file()

    # -- internals ------------------------------------------------------

    def _complete_path(self, track_id: str) -> Path:
        return self._cache_dir / _check_track_id(track_id)

    def _part_path(self, track_id: str) -> Path:
        return self._cache_dir / (_check_track_id(track_id) + ".part")

    def _track_lock(self, track_id: str) -> threading.Lock:
        with self._entries_lock:
            lock = self._track_locks.get(track_id)
            if lock is None:
                lock = threading.Lock()
                self._track_locks[track_id] = lock
            return lock

    def _serve(self, handler: BaseHTTPRequestHandler,
               head_only: bool) -> None:
        token = self._token(handler.path)
        with self._entries_lock:
            entry = self._entries.get(token) if token else None
        if entry is None:
            self._send_text(handler, 404, "no such stream")
            return
        track_id = entry.track_id
        complete = self._complete_path(track_id)
        if complete.is_file():
            with self._entries_lock:
                mime = self._mime_cache.get(track_id, _DEFAULT_MIME)
            self._send_path(handler, complete, mime,
                            handler.headers.get("Range"),
                            head_only, complete.stat().st_size)
            return
        lock = self._track_lock(track_id)
        with lock:
            if complete.is_file():
                with self._entries_lock:
                    mime = self._mime_cache.get(track_id, _DEFAULT_MIME)
                self._send_path(handler, complete, mime,
                                handler.headers.get("Range"),
                                head_only, complete.stat().st_size)
                return
            try:
                source = self._fresh_source(entry)
                total = self._fill(entry, source,
                                   handler.headers.get("Range"), head_only)
            except _UpstreamFailed:
                self._send_text(handler, 502, "upstream fetch failed")
                return
            except Exception:
                self._send_text(handler, 502, "upstream fetch failed")
                return
            path = complete if complete.is_file() else self._part_path(
                track_id)
            self._send_path(handler, path, source.mime or _DEFAULT_MIME,
                            handler.headers.get("Range"), head_only, total)

    @staticmethod
    def _token(path: str) -> str:
        target = urllib.parse.urlparse(path).path
        first, _, token = target.partition("/t/")
        if first != "" or not token or "/" in token:
            return ""
        return token

    @staticmethod
    def _send_text(handler: BaseHTTPRequestHandler, code: int,
                   text: str) -> None:
        body = text.encode()
        handler.send_response(code)
        handler.send_header("Content-Type", "text/plain")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def _fresh_source(self, entry: _Entry) -> StreamSource:
        source = entry.source
        if (source is None or (source.expires_at
                               and source.expires_at <= time.time())):
            source = entry.resolve()
            entry.source = source
        return source

    def _fill(self, entry: _Entry, source: StreamSource,
              range_header: str | None, head_only: bool) -> int | None:
        """Fetch until the requested bytes are on disk. Returns the total
        length when known, else None."""
        track_id = entry.track_id
        part = self._part_path(track_id)
        complete = self._complete_path(track_id)
        want = _parse_range(range_header)
        if head_only and want in (None, ("all",)):
            need_end: int | None = 0
        elif want is None or want[0] == "suffix":
            need_end = None
        elif want[2] is None:
            need_end = None
        else:
            need_end = want[2]
        total = source.size_bytes
        if total is not None and total < 0:
            total = None
        may_retry_403 = True
        while True:
            have = part.stat().st_size if part.is_file() else 0
            if total is not None and have >= total:
                break
            if need_end is not None and have > need_end:
                break
            if source.expires_at and source.expires_at <= time.time():
                source = entry.resolve()
                entry.source = source
                may_retry_403 = True
                if total is None and source.size_bytes:
                    total = source.size_bytes
            try:
                status, headers, body = _fetch_range(
                    source.uri, dict(source.headers), have, self._chunk_size)
            except OSError as exc:
                raise _UpstreamFailed(str(exc)) from exc
            if status == 403 and may_retry_403:
                source = entry.resolve()
                entry.source = source
                may_retry_403 = False
                if total is None and source.size_bytes:
                    total = source.size_bytes
                continue
            if status == 403:
                raise _UpstreamFailed("upstream refused the fetch")
            if status == 200:
                part.parent.mkdir(parents=True, exist_ok=True)
                part.write_bytes(body)
                total = len(body)
                break
            if status == 416:
                claimed = _content_range_total(headers.get("content-range"))
                total = claimed if claimed is not None else have
                break
            if status != 206:
                raise _UpstreamFailed(f"upstream status {status}")
            claimed = _content_range_total(headers.get("content-range"))
            if claimed is not None:
                total = claimed
            if body:
                part.parent.mkdir(parents=True, exist_ok=True)
                with part.open("ab") as handle:
                    handle.write(body)
                have += len(body)
            else:
                total = have
                break
            if claimed is None and len(body) < self._chunk_size:
                total = have
            if total is not None and have >= total:
                break
            if need_end is not None and have > need_end:
                break
        if total is not None and total >= 0 and part.is_file():
            if part.stat().st_size >= total:
                with part.open("r+b") as handle:
                    handle.truncate(total)
                os.replace(part, complete)
        with self._entries_lock:
            if source.mime:
                self._mime_cache[track_id] = source.mime
        return total

    @staticmethod
    def _send_path(handler: BaseHTTPRequestHandler, path: Path, mime: str,
                   range_header: str | None, head_only: bool,
                   total: int | None) -> None:
        on_disk = path.stat().st_size if path.is_file() else 0
        known_total = total is not None
        if total is None:
            total = on_disk
        want = _parse_range(range_header)
        first, last = 0, total - 1 if total else -1
        if want is not None and want[0] != "all":
            if want[0] == "suffix":
                count = min(want[1], total)
                first, last = total - count, total - 1
            elif want[2] is None:
                first = want[1]
            else:
                first, last = want[1], min(want[2], total - 1)
            if total == 0 or first >= total:
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{total}")
                handler.send_header("Content-Length", "0")
                handler.send_header("Connection", "close")
                handler.end_headers()
                return
        length = max(0, last - first + 1) if total else 0
        ranged = want is not None and want[0] != "all"
        handler.send_response(206 if ranged else 200)
        handler.send_header("Content-Type", mime or _DEFAULT_MIME)
        handler.send_header("Accept-Ranges", "bytes")
        if ranged:
            shown = f"bytes {first}-{last}/{total}" if known_total \
                else f"bytes {first}-{last}/*"
            handler.send_header("Content-Range", shown)
        handler.send_header("Content-Length", str(length))
        handler.send_header("Connection", "close")
        handler.end_headers()
        if head_only or length == 0:
            return
        with path.open("rb") as handle:
            handle.seek(first)
            remaining = length
            while remaining > 0:
                piece = handle.read(min(65536, remaining))
                if not piece:
                    break
                handler.wfile.write(piece)
                remaining -= len(piece)
