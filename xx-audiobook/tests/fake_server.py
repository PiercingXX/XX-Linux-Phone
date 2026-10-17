"""Loopback fake server for M1 tests (Linux mirror of FakeAudiobookApi).

Threaded http.server on 127.0.0.1 with an ephemeral port, serving canned
openapi-shaped JSON for the used subset, estate /auth/login|refresh, hub
/hub/discovery, and Range-capable media files with real sha256 manifests.
Tests inject faults (401-reason / 403 / 409 / 422 / delay / truncated
body) or queue one-shot scripted responses. No test touches the real
network: the conftest guard allows loopback only.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

SUMMARY_1 = {
    "id": "it-1", "title": "The Long Way", "subtitle": "",
    "authors": ["Becky Chambers"], "narrators": ["Rachel Dulude"],
    "series": [{"id": 7, "name": "Wayfarers", "sequence": "1"}],
    "duration_s": 7200.0, "status": "ok", "added_at": 1790000000.0,
    "cover_url": "api/items/it-1/cover?size=thumb",
    "thumb_url": "api/items/it-1/cover?size=thumb",
    "progress": {"item_id": "it-1", "position_s": 1200.0,
                 "duration_s": 7200.0, "speed": 1.0, "is_finished": False,
                 "started_at": 1790000000.0, "finished_at": None,
                 "last_played_at": 1790000600.0,
                 "updated_at": 1790000600.0},
    "rating": {"average": 4.5, "count": 2, "mine": None},
    "author_refs": [{"id": 11, "name": "Becky Chambers"}],
    "narrator_refs": [{"id": 12, "name": "Rachel Dulude"}],
}

SUMMARY_2 = {
    "id": "it-2", "title": "Project Hail Mary", "subtitle": "",
    "authors": ["Andy Weir"], "narrators": ["Ray Porter"],
    "series": [], "duration_s": 9000.0, "status": "ok",
    "added_at": 1790000100.0, "cover_url": None, "thumb_url": None,
    "progress": None, "rating": {"average": None, "count": 0, "mine": None},
    "author_refs": [{"id": 13, "name": "Andy Weir"}],
    "narrator_refs": [],
}

DETAIL_1 = dict(SUMMARY_1, **{
    "description": "A cozy space opera.", "publisher": "Harper",
    "published_year": "2014", "language": "en", "genres": ["sci-fi"],
    "asin": "", "isbn": "", "explicit": False, "abridged": False,
    "size_bytes": 3000000, "file_count": 2, "updated_at": 1790000000.0,
    "chapters": [
        {"index": 0, "title": "One", "start_s": 0.0, "end_s": 3600.0},
        {"index": 1, "title": "Two", "start_s": 3600.0, "end_s": 7200.0}],
    "files": [
        {"file_id": "f-a", "index": 0, "url": "api/items/it-1/files/f-a",
         "start_offset_s": 0.0, "duration_s": 3600.0,
         "mime": "audio/mp4", "size": 4096, "sha256": ""},
        {"file_id": "f-b", "index": 1, "url": "api/items/it-1/files/f-b",
         "start_offset_s": 3600.0, "duration_s": 3600.0,
         "mime": "audio/mp4", "size": 8192, "sha256": ""}],
    "playable": True, "issues": [],
})

PLAY_1 = {
    "session_id": "ps-1", "item_id": "it-1", "resume_position_s": 1200.0,
    "duration_s": 7200.0, "speed": 1.0,
    "tracks": [
        {"file_id": "f-a", "url": "api/items/it-1/files/f-a",
         "start_offset_s": 0.0, "duration_s": 3600.0,
         "mime": "audio/mp4", "size": 4096},
        {"file_id": "f-b", "url": "api/items/it-1/files/f-b",
         "start_offset_s": 3600.0, "duration_s": 3600.0,
         "mime": "audio/mp4", "size": 8192}],
    "chapters": DETAIL_1["chapters"],
}

SETTINGS_DEFAULT = {
    "skip_back_s": 15, "skip_forward_s": 30, "default_speed": 1.0,
    "auto_rewind_s": 2, "sleep_default_min": None,
    "sleep_end_of_chapter": False, "theme_preset": "default",
    "library_view": "grid", "updated_at": 1790000000.0, "applied": True,
}


def _media_bytes(file_id: str, size: int) -> bytes:
    seed = sum(file_id.encode("utf-8")) % 251 + 1
    return bytes((seed * (i + 1)) % 256 for i in range(size))


def _progress_row(item_id: str, position_s: float, is_finished: bool,
                  updated_at: float) -> dict:
    return {"item_id": item_id, "position_s": position_s,
            "duration_s": 7200.0, "speed": 1.0,
            "is_finished": is_finished, "started_at": 1790000000.0,
            "finished_at": (updated_at if is_finished else None),
            "last_played_at": updated_at, "updated_at": updated_at,
            "applied": True}


class FakeServer:
    """Canned audiobook + estate + hub endpoints with fault injection."""

    def __init__(self) -> None:
        self.calls: list = []
        self.faults: dict = {}
        self.script: list = []
        self.progress: dict = {
            "it-1": _progress_row("it-1", 4100.0, False, 1790003600.0)}
        self.settings: dict = dict(SETTINGS_DEFAULT)
        self.bookmarks: dict = {
            "bm-1": {"id": "bm-1", "item_id": "it-1",
                     "position_s": 200.0, "note": "great line",
                     "created_at": 1790000000.0,
                     "updated_at": 1790000000.0}}
        self.grant = {"token": "tok-1", "user_id": "u-1", "role": "",
                      "display_name": "Alice", "expires_at": 9999999999,
                      "grants": ["com.piercingxx.audiobook"]}
        self.hub_token = "hub-token"
        self.hub_origin = ""
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.server_address[1]

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def api_base(self) -> str:
        return self.origin + "/audiobook/"

    def start(self) -> FakeServer:
        handler = self._handler()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fake-audiobook",
            daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def add_fault(self, method: str, path: str, code: int, body="",
                  delay: float = 0.0) -> None:
        self.faults[(method.upper(), path)] = (code, body, delay)

    def queue(self, method: str, path: str, code: int, body="") -> None:
        self.script.append((method.upper(), path, code, body))

    def last_call(self, method: str, path: str):
        for call in reversed(self.calls):
            if call[0] == method.upper() and call[1] == path:
                return call
        return None

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "FakeAudiobook/1"

            def log_message(self, *args) -> None:
                pass

            def _read_body(self) -> bytes:
                try:
                    length = int(self.headers.get("Content-Length", 0))
                except (TypeError, ValueError):
                    length = 0
                return self.rfile.read(length) if length else b""

            def _reply(self, code: int, payload, content_type: str
                       = "application/json") -> None:
                if isinstance(payload, (dict, list)):
                    raw = json.dumps(payload).encode("utf-8")
                elif isinstance(payload, str):
                    raw = payload.encode("utf-8")
                else:
                    raw = bytes(payload)
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(raw)

            def _handle(self) -> None:
                split = urlsplit(self.path)
                path = split.path
                query = parse_qs(split.query)
                body = self._read_body()
                headers = {k.lower(): v for k, v in self.headers.items()}
                outer.calls.append(
                    (self.command, path, headers, body, query))
                for index, (method, script_path, code,
                            script_body) in enumerate(outer.script):
                    if (method == self.command and script_path == path):
                        del outer.script[index]
                        self._reply(code, script_body)
                        return
                fault = outer.faults.get((self.command, path))
                if fault is not None:
                    code, fault_body, delay = fault
                    if delay:
                        import time
                        time.sleep(delay)
                    if fault_body == "<truncate>":
                        self.send_response(code)
                        self.send_header("Content-Type",
                                         "application/json")
                        self.send_header("Content-Length", "100")
                        self.end_headers()
                        self.wfile.write(b'{"results": [')
                        return
                    self._reply(code, fault_body)
                    return
                try:
                    self._route(path, query, body, headers)
                except BrokenPipeError:
                    pass

            def _route(self, path: str, query: dict, body: bytes,
                       headers: dict) -> None:
                try:
                    doc = json.loads(body.decode("utf-8")) if body else {}
                except ValueError:
                    doc = {}
                if not isinstance(doc, dict):
                    doc = {}
                parts = path.strip("/").split("/")

                if path == "/hub/discovery":
                    from xx_audiobook import auth as auth_module
                    envelope = auth_module.hub_sign(
                        {"session": outer.hub_token,
                         "listen": "127.0.0.1", "exp": 9999999999,
                         "origin": outer.hub_origin, "hub_id": "hub-1"})
                    self._reply(200, {"discovery": envelope,
                                      "origin": outer.hub_origin})
                    return
                if path == "/auth/login":
                    if doc.get("client") != "xx-audiobook":
                        self._reply(400, {"detail": "bad client"})
                        return
                    self._reply(200, dict(outer.grant))
                    return
                if path == "/auth/refresh":
                    if headers.get("authorization") != "Bearer stale-tok":
                        self._reply(401, {"reason": "revoked"})
                        return
                    grant = dict(outer.grant, token="tok-2")
                    self._reply(200, grant)
                    return
                if parts[:1] != ["audiobook"]:
                    self._reply(404, {"detail": "no such path"})
                    return
                rest = parts[1:]
                if rest[:2] == ["api", "me"] and len(rest) == 2:
                    self._reply(200, {"user_id": "u-1", "username": "alice",
                                      "display_name": "Alice",
                                      "is_admin": False})
                    return
                routed = self._route_api(rest, query, doc, headers)
                if not routed:
                    self._reply(404, {"detail": "no such item"})

            def _route_api(self, rest: list, query: dict, doc: dict,
                           headers: dict) -> bool:
                method = self.command
                outer_calls = outer
                if rest == ["api", "status"] and method == "GET":
                    self._reply(200, {
                        "version": "1", "service": "audiobook",
                        "items": 2, "items_missing": 0,
                        "pending_matches": 0, "media_roots": [],
                        "subsystems": {"ha": {"state": "not_configured",
                                              "detail": "", "hint": ""}}})
                    return True
                if rest == ["api", "items"] and method == "GET":
                    page = int(query.get("page", ["1"])[0])
                    limit = int(query.get("limit", ["50"])[0])
                    self._reply(200, {"total": 2, "page": page,
                                      "limit": limit,
                                      "results": [SUMMARY_1, SUMMARY_2]})
                    return True
                if (len(rest) == 3 and rest[:2] == ["api", "items"]
                        and method == "GET"):
                    if rest[2] == "it-1":
                        self._reply(200, DETAIL_1)
                    else:
                        self._reply(404, {"detail": "no such item"})
                    return True
                if (len(rest) == 4 and rest[:2] == ["api", "items"]
                        and rest[3] == "cover" and method == "GET"):
                    self._reply(200, b"\xff\xd8fakejpeg",
                                "image/jpeg")
                    return True
                if (len(rest) == 4 and rest[:2] == ["api", "items"]
                        and rest[3] == "manifest" and method == "GET"):
                    files = []
                    for row in DETAIL_1["files"]:
                        content = _media_bytes(
                            row["file_id"], row["size"])
                        files.append(dict(
                            row,
                            filename=f"{row['file_id']}.m4b",
                            sha256=hashlib.sha256(content).hexdigest()))
                    self._reply(200, {"item_id": rest[2],
                                      "total_size": sum(
                                          f["size"] for f in files),
                                      "files": files})
                    return True
                if (len(rest) == 5 and rest[:2] == ["api", "items"]
                        and rest[3] == "files"
                        and method in ("GET", "HEAD")):
                    return self._serve_file(rest[2], rest[4], headers)
                if rest == ["api", "library", "facets"] and method == "GET":
                    self._reply(200, {"genres": ["sci-fi"],
                                      "languages": ["en"], "authors": [],
                                      "narrators": [], "series": [],
                                      "counts": {}})
                    return True
                if rest == ["api", "search"] and method == "GET":
                    self._reply(200, {
                        "books": [SUMMARY_1], "series": [], "authors": [],
                        "narrators": [],
                        "genres": [{"name": "sci-fi", "count": 1}]})
                    return True
                if rest == ["api", "series"] and method == "GET":
                    self._reply(200, [])
                    return True
                if (len(rest) == 3 and rest[:2] == ["api", "series"]
                        and method == "GET"):
                    self._reply(200, {
                        "id": 7, "name": "Wayfarers", "book_count": 1,
                        "duration_s": 7200.0,
                        "progress": {"finished": 0, "in_progress": 1},
                        "books": [dict(SUMMARY_1, sequence="1")]})
                    return True
                if rest == ["api", "authors"] and method == "GET":
                    self._reply(200, [])
                    return True
                if (len(rest) == 3 and rest[:2] == ["api", "authors"]
                        and method == "GET"):
                    self._reply(200, {
                        "id": 11, "name": "Becky Chambers", "asin": "",
                        "description": "", "image_url": None,
                        "books": [SUMMARY_1], "series": []})
                    return True
                if rest == ["api", "narrators"] and method == "GET":
                    self._reply(200, [])
                    return True
                if (len(rest) == 3 and rest[:2] == ["api", "narrators"]
                        and method == "GET"):
                    self._reply(200, {
                        "id": 12, "name": "Rachel Dulude", "book_count": 1,
                        "books": [SUMMARY_1], "series": []})
                    return True
                if rest == ["api", "collections"] and method == "GET":
                    self._reply(200, {"results": [{
                        "id": "col-1", "name": "Staff picks",
                        "description": "", "book_count": 2,
                        "thumb_urls": [], "updated_at": 1790000000.0}]})
                    return True
                if (len(rest) == 3 and rest[:2] == ["api", "collections"]
                        and method == "GET"):
                    self._reply(200, {
                        "id": "col-1", "name": "Staff picks",
                        "description": "", "book_count": 2, "thumb_urls": [],
                        "updated_at": 1790000000.0,
                        "books": [SUMMARY_1, SUMMARY_2]})
                    return True
                if (len(rest) == 4 and rest[:2] == ["api", "items"]
                        and rest[3] == "play" and method == "POST"):
                    self._reply(200, PLAY_1)
                    return True
                if (len(rest) == 4 and rest[:2] == ["api", "sessions"]
                        and rest[3] == "sync" and method == "POST"):
                    self._reply(200, {
                        "session_id": rest[2], "item_id": "it-1",
                        "position_s": doc.get("position_s", 0.0),
                        "listened_s": doc.get("listened_s", 0.0),
                        "speed": 1.0, "closed_at": None,
                        "progress_applied": True, "progress": None})
                    return True
                if (len(rest) == 4 and rest[:2] == ["api", "sessions"]
                        and rest[3] == "close" and method == "POST"):
                    self._reply(200, {})
                    return True
                if rest == ["api", "me", "continue"] and method == "GET":
                    self._reply(200, {"results": [SUMMARY_1]})
                    return True
                if rest == ["api", "me", "home"] and method == "GET":
                    self._reply(200, {"shelves": [
                        {"id": "continue", "label": "Continue",
                         "kind": "items", "results": [SUMMARY_1]},
                        {"id": "continue_series",
                         "label": "More", "kind": "items",
                         "results": [SUMMARY_2]},
                        {"id": "listen_again", "label": "Again",
                         "kind": "items", "results": []},
                        {"id": "discover", "label": "Discover",
                         "kind": "items", "results": [SUMMARY_2]}]})
                    return True
                if rest == ["api", "me", "progress"] and method == "GET":
                    self._reply(200, {"results": list(
                        outer_calls.progress.values())})
                    return True
                if rest == ["api", "me", "progress", "batch"]:
                    updated = []
                    for item_id in doc.get("item_ids", []):
                        outer_calls.progress[str(item_id)] = _progress_row(
                            str(item_id), 0.0,
                            bool(doc.get("finished", True)),
                            1790003600.0)
                        updated.append(str(item_id))
                    self._reply(200, {"updated": len(updated),
                                      "item_ids": updated})
                    return True
                if (len(rest) == 4 and rest[:3] == ["api", "me", "progress"]
                        and method == "GET"):
                    row = outer_calls.progress.get(rest[3])
                    if row is None:
                        self._reply(404, {"detail": "no progress"})
                    else:
                        self._reply(200, row)
                    return True
                if (len(rest) == 4 and rest[:3] == ["api", "me", "progress"]
                        and method == "PUT"):
                    return self._put_progress(rest[3], doc)
                if (len(rest) == 4 and rest[:3] == ["api", "me", "progress"]
                        and method == "DELETE"):
                    outer_calls.progress.pop(rest[3], None)
                    self._reply(200, {})
                    return True
                if (len(rest) == 5 and rest[:3] == ["api", "me", "progress"]
                        and rest[4] in ("finish", "unfinish")
                        and method == "POST"):
                    row = _progress_row(
                        rest[3],
                        outer_calls.progress.get(
                            rest[3], {}).get("position_s", 0.0),
                        rest[4] == "finish", 1790003600.0)
                    outer_calls.progress[rest[3]] = row
                    self._reply(200, row)
                    return True
                if rest == ["api", "me", "stats"] and method == "GET":
                    self._reply(200, {
                        "year": 2026, "total_listened_s": 7200.0,
                        "sessions": 2, "items_played": 1,
                        "items_finished": 0, "days_listened": 1,
                        "by_month": [{"month": m, "listened_s": 0.0,
                                      "sessions": 0} for m in range(1, 13)],
                        "top_items": [{"item_id": "it-1",
                                       "title": "The Long Way",
                                       "listened_s": 7200.0,
                                       "thumb_url": None}],
                        "streak": {"current_days": 1, "longest_days": 2},
                        "first_year": 2026,
                        "by_day": [{"date": "2026-09-25",
                                    "listened_s": 100.0}],
                        "top_authors": [{"id": 11,
                                         "name": "Becky Chambers",
                                         "listened_s": 7200.0}]})
                    return True
                if rest == ["api", "me", "sessions"] and method == "GET":
                    self._reply(200, {"total": 1, "page": 1, "limit": 50,
                                      "results": [{
                                          "session_id": "ps-1",
                                          "item_id": "it-1",
                                          "title": "The Long Way",
                                          "device": "Pixel 9",
                                          "start_position_s": 0.0,
                                          "position_s": 61.0,
                                          "listened_s": 60.0, "speed": 1.0,
                                          "started_at": 1790000000.0,
                                          "updated_at": 1790000060.0,
                                          "closed_at": None}]})
                    return True
                if (len(rest) == 4 and rest[:3] == ["api", "me", "sessions"]
                        and method == "DELETE"):
                    self._reply(204, {})
                    return True
                if rest == ["api", "me", "settings"] and method == "GET":
                    self._reply(200, dict(outer_calls.settings,
                                          applied=True))
                    return True
                if rest == ["api", "me", "settings"] and method == "PUT":
                    stored = outer_calls.settings
                    incoming = doc.get("updated_at")
                    if (incoming is not None and stored.get("updated_at")
                            is not None
                            and incoming < stored["updated_at"]):
                        self._reply(200, dict(stored, applied=False))
                        return True
                    for key in ("skip_back_s", "skip_forward_s",
                                "default_speed", "auto_rewind_s",
                                "sleep_default_min",
                                "sleep_end_of_chapter", "theme_preset",
                                "library_view", "updated_at"):
                        if key in doc:
                            stored[key] = doc[key]
                    stored["applied"] = True
                    self._reply(200, dict(stored))
                    return True
                if rest == ["api", "me", "playlists"] and method == "GET":
                    self._reply(200, {"results": []})
                    return True
                if rest == ["api", "me", "playlists"] and method == "POST":
                    self._reply(200, {
                        "id": "pl-1", "name": doc.get("name", ""),
                        "description": doc.get("description", ""),
                        "book_count": 0, "thumb_urls": [],
                        "updated_at": 1790000000.0})
                    return True
                if (len(rest) == 4
                        and rest[:3] == ["api", "me", "playlists"]
                        and method in ("GET", "PATCH", "DELETE")):
                    if method == "DELETE":
                        self._reply(204, {})
                    else:
                        self._reply(200, {
                            "id": rest[3], "name": "pl",
                            "description": "", "book_count": 1,
                            "thumb_urls": [],
                            "updated_at": 1790000000.0,
                            "books": ([SUMMARY_1] if method == "GET"
                                      else [])})
                    return True
                if (len(rest) == 5
                        and rest[:3] == ["api", "me", "playlists"]
                        and rest[4] == "items"
                        and method in ("POST", "PUT")):
                    self._reply(200, {
                        "id": rest[3], "name": "pl", "description": "",
                        "book_count": 1, "thumb_urls": [],
                        "updated_at": 1790000000.0, "books": [SUMMARY_1]})
                    return True
                if (len(rest) == 6
                        and rest[:3] == ["api", "me", "playlists"]
                        and rest[4] == "items" and method == "DELETE"):
                    self._reply(204, {})
                    return True
                if rest == ["api", "me", "bookmarks"] and method == "GET":
                    rows = list(outer_calls.bookmarks.values())
                    wanted = query.get("item_id", [None])[0]
                    if wanted:
                        rows = [r for r in rows
                                if r["item_id"] == wanted]
                    self._reply(200, {"results": rows})
                    return True
                if rest == ["api", "me", "bookmarks"] and method == "POST":
                    row = {"id": "bm-2", "item_id": doc.get("item_id", ""),
                           "position_s": doc.get("position_s", 0.0),
                           "note": doc.get("note", ""),
                           "created_at": 1790000000.0,
                           "updated_at": 1790000000.0}
                    outer_calls.bookmarks["bm-2"] = row
                    self._reply(200, row)
                    return True
                if (len(rest) == 4
                        and rest[:3] == ["api", "me", "bookmarks"]
                        and method in ("GET", "PATCH", "DELETE")):
                    row = outer_calls.bookmarks.get(rest[3])
                    if method == "DELETE":
                        outer_calls.bookmarks.pop(rest[3], None)
                        self._reply(204, {})
                        return True
                    if row is None:
                        self._reply(404, {"detail": "no such bookmark"})
                        return True
                    if method == "PATCH":
                        row = dict(row, **{k: v for k, v in doc.items()
                                           if k in ("position_s", "note")})
                        outer_calls.bookmarks[rest[3]] = row
                    self._reply(200, row)
                    return True
                if (len(rest) == 4 and rest[:2] == ["api", "items"]
                        and rest[3] == "reviews" and method == "GET"):
                    self._reply(200, {
                        "average": 4.0, "count": 2, "page": 1, "limit": 20,
                        "results": [
                            {"id": "rv-1", "item_id": rest[2],
                             "user": {"id": "u-1",
                                      "display_name": "Alice"},
                             "stars": 5, "body": "Loved it",
                             "created_at": 1790000000.0,
                             "updated_at": 1790000000.0},
                            {"id": "rv-2", "item_id": rest[2],
                             "user": {"id": "u-2", "display_name": "Bob"},
                             "stars": 4, "body": "",
                             "created_at": 1790000000.0,
                             "updated_at": 1790000000.0}]})
                    return True
                if (len(rest) == 4 and rest[:2] == ["api", "items"]
                        and rest[3] == "review" and method == "PUT"):
                    self._reply(200, {
                        "id": "rv-1", "item_id": rest[2],
                        "user": {"id": "u-1", "display_name": "Alice"},
                        "stars": doc.get("stars", 5),
                        "body": doc.get("body", ""),
                        "created_at": 1790000000.0,
                        "updated_at": 1790000000.0})
                    return True
                if (len(rest) == 4 and rest[:2] == ["api", "items"]
                        and rest[3] == "review" and method == "DELETE"):
                    self._reply(204, {})
                    return True
                if (len(rest) == 4 and rest[:2] == ["api", "admin"]
                        and rest[2] == "reviews" and method == "DELETE"):
                    self._reply(204, {})
                    return True
                if rest == ["api", "reviews", "recent"] and method == "GET":
                    self._reply(200, [])
                    return True
                if rest == ["api", "locations"] and method == "GET":
                    self._reply(200, {"results": [{
                        "id": "loc-home", "name": "Home",
                        "configured": True}]})
                    return True
                if rest == ["api", "cast", "targets"] and method == "GET":
                    self._reply(200, {
                        "location_id": query.get("location_id", [""])[0],
                        "targets": [{
                            "entity_id": "media_player.kitchen",
                            "name": "Kitchen", "state": "idle",
                            "volume_level": 0.5, "media_title": None,
                            "media_position_s": None}]})
                    return True
                if rest == ["api", "cast", "play"] and method == "POST":
                    self._reply(200, {
                        "location_id": doc.get("location_id", "loc-home"),
                        "entity_id": doc.get("entity_id", ""),
                        "item_id": doc.get("item_id", ""),
                        "file_id": "f-a", "position_s": 12.0,
                        "offset_s": 0.0, "expires_at": 9999999999,
                        "queued": 1, "seeked": False})
                    return True
                if rest == ["api", "cast", "transport"] and method == "POST":
                    self._reply(200, {"ok": True,
                                      "action": doc.get("action", ""),
                                      "entity_id": doc.get(
                                          "entity_id", "")})
                    return True
                return False

            def _put_progress(self, item_id: str, doc: dict) -> bool:
                stored = outer.progress.get(item_id)
                incoming = doc.get("updated_at")
                if (stored is not None and incoming is not None
                        and stored.get("updated_at") is not None
                        and incoming < stored["updated_at"]):
                    self._reply(200, dict(stored, applied=False))
                    return True
                row = _progress_row(item_id, doc.get("position_s", 0.0),
                                    bool(doc.get("is_finished", False)),
                                    (incoming if incoming is not None
                                     else 1790003600.0))
                if doc.get("duration_s") is not None:
                    row["duration_s"] = doc["duration_s"]
                outer.progress[item_id] = row
                self._reply(200, dict(row, applied=True))
                return True

            def _serve_file(self, item_id: str, file_id: str,
                            headers: dict) -> bool:
                known = {f["file_id"]: f["size"]
                         for f in DETAIL_1["files"]}
                if item_id != "it-1" or file_id not in known:
                    self._reply(404, {"detail": "no such file"})
                    return True
                content = _media_bytes(file_id, known[file_id])
                etag = hashlib.sha256(content).hexdigest()
                conditions = headers.get("if-range")
                if conditions and conditions.strip('"') != etag:
                    self._reply(200, content, "audio/mp4")
                    return True
                ranges = headers.get("range", "")
                start = 0
                if ranges.startswith("bytes="):
                    try:
                        start = int(ranges[len("bytes="):].split("-")[0])
                    except ValueError:
                        start = 0
                if start >= len(content):
                    self.send_response(416)
                    self.send_header("Content-Range",
                                     f"bytes */{len(content)}")
                    self.end_headers()
                    return True
                chunk = content[start:]
                if ranges.startswith("bytes="):
                    self.send_response(206)
                    self.send_header("Content-Type", "audio/mp4")
                    self.send_header("ETag", f'"{etag}"')
                    self.send_header(
                        "Content-Range",
                        f"bytes {start}-{len(content) - 1}/{len(content)}")
                    self.send_header("Content-Length", str(len(chunk)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(chunk)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/mp4")
                    self.send_header("ETag", f'"{etag}"')
                    self.send_header("Content-Length", str(len(chunk)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(chunk)
                return True

            def do_GET(self) -> None:
                self._handle()

            def do_POST(self) -> None:
                self._handle()

            def do_PUT(self) -> None:
                self._handle()

            def do_PATCH(self) -> None:
                self._handle()

            def do_DELETE(self) -> None:
                self._handle()

            def do_HEAD(self) -> None:
                self._handle()

        return Handler


@pytest.fixture
def fake_server():
    server = FakeServer().start()
    yield server
    server.stop()
