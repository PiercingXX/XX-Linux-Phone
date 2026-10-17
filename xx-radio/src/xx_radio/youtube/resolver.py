"""Resolve track ids to fetchable audio locations, via a child process.

yt-dlp runs as ``python3 -m xx_radio.youtube.resolve_worker`` (JSON over
stdin/stdout, one run can cover several ids to amortise start-up), never
in-process: its challenge work is heavy and a hang must be killable. This
module owns the expiry cache, batching, the per-resolve options (low-data
formats, cookie jar, player clients) and the trip across the process
boundary. The error table itself lives in resolve_worker.classify_failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from xx_music.engine import EngineError, ErrorKind, StreamSource
from xx_radio.youtube import resolve_worker

WORKER_MODULE = "xx_radio.youtube.resolve_worker"
#: A hung resolve must never stall playback for long.
RESOLVE_TIMEOUT_S = 45.0
#: Fresh entries stay valid until this close to their expiry.
EXPIRY_MARGIN_S = 60.0
#: yt-dlp's chunked requests use this size; the stream proxy matches it.
CHUNK_BYTES = 10 * 2**20
#: Signed-in resolves that unlock age-restricted songs and Premium audio.
SIGNED_IN_CLIENTS = ("tv", "web_music")
COOKIE_FILENAME = "cookies.txt"
LOGIN_COOKIE = "LOGIN_INFO"


def audio_format(low_data: bool) -> str:
    return resolve_worker.audio_format(low_data)


def player_clients(signed_in: bool,
                   override: list[str] | None = None) -> list[str] | None:
    """Which player clients yt-dlp should try: the signed-in pair, yt-dlp's
    own default order when anonymous, or an explicit field-fix override."""
    if override is not None:
        return list(override)
    if signed_in:
        return list(SIGNED_IN_CLIENTS)
    return None


def worker_python(engine_dir: Path) -> str:
    """The promoted engine venv's python when there is one, else this one."""
    candidate = Path(engine_dir) / "venv" / "bin" / "python"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return sys.executable


def plugin_dirs(engine_dir: Path) -> list[str]:
    folder = Path(engine_dir) / "plugins"
    return [str(folder)] if folder.is_dir() else []


def cookie_file(runtime_dir: Path | None) -> str | None:
    if runtime_dir is None:
        return None
    jar = Path(runtime_dir) / COOKIE_FILENAME
    return str(jar) if jar.is_file() else None


def write_cookie_jar(path: Path | str, netscape_text: str) -> Path:
    """Write a Netscape cookie jar without the LOGIN_INFO cookie.

    The jar lives on tmpfs (the runtime dir), is readable only by the
    owner, and is removed on sign-out. Returns the jar path.
    """
    jar = Path(path)
    jar.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    for line in (netscape_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            kept.append(line.rstrip("\r\n"))
            continue
        fields = stripped.split("\t")
        if len(fields) >= 7 and fields[5] == LOGIN_COOKIE:
            continue
        kept.append("\t".join(fields))
    jar.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.chmod(jar, 0o600)
    return jar


def clear_cookie_jar(path: Path | str) -> None:
    Path(path).unlink(missing_ok=True)


def _be_nice() -> None:
    try:
        os.nice(10)
    except OSError:
        pass


def _text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _result_to_source(item: dict[str, Any]) -> StreamSource:
    headers = {str(key): str(value)
               for key, value in dict(item.get("headers") or {}).items()}
    size = item.get("size_bytes")
    gain = item.get("gain_db")
    try:
        size_bytes = int(size) if size is not None else None
    except (TypeError, ValueError):
        size_bytes = None
    try:
        gain_db = float(gain) if gain is not None else None
    except (TypeError, ValueError):
        gain_db = None
    try:
        expires_at = float(item.get("expires_at") or 0.0)
    except (TypeError, ValueError):
        expires_at = 0.0
    return StreamSource(uri=str(item.get("uri", "")), headers=headers,
                        mime=str(item.get("mime") or ""),
                        expires_at=expires_at, size_bytes=size_bytes,
                        gain_db=gain_db)


def _result_to_error(track_id: str, item: dict[str, Any]) -> EngineError:
    try:
        kind = ErrorKind(str(item.get("kind") or "other"))
    except ValueError:
        kind = ErrorKind.OTHER
    retry = item.get("retry_after_s")
    try:
        retry_after = float(retry) if retry is not None else None
    except (TypeError, ValueError):
        retry_after = None
    detail = str(item.get("error") or track_id)[:500]
    return EngineError(kind, detail, retry_after)


def _worker_env() -> dict[str, str]:
    """The child may be the engine venv's python, which knows nothing of
    where this app is installed: put our package root on its path."""
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (root, env.get("PYTHONPATH", "")) if p)
    return env


class Resolver:
    """Expiry-caching, batching front end to the resolve worker.

    ``run`` is a test seam: ``run(payload)`` replaces the child process.
    ``clients_override`` and ``runtimes``/``extractor_args`` are field-fix
    knobs (config ``player_clients``); ``helper_ready``/``signed_in``
    disambiguate the error table.
    """

    def __init__(self, engine_dir: Path | str,
                 runtime_dir: Path | str | None = None, *,
                 python: str | None = None,
                 timeout_s: float = RESOLVE_TIMEOUT_S,
                 signed_in: bool = False,
                 helper_ready: bool = True,
                 clients_override: list[str] | None = None,
                 runtimes: dict[str, str] | None = None,
                 extractor_args: dict[str, Any] | None = None,
                 remote_components: list[str] | None = None,
                 run: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                 ) -> None:
        self.engine_dir = Path(engine_dir)
        self.runtime_dir = Path(runtime_dir) if runtime_dir else None
        self.python = python
        self.timeout_s = timeout_s
        self.signed_in = signed_in
        self.helper_ready = helper_ready
        self.clients_override = (list(clients_override)
                                 if clients_override is not None else None)
        self.runtimes = dict(runtimes) if runtimes else None
        self.extractor_args = dict(extractor_args) if extractor_args else None
        self.remote_components = (list(remote_components)
                                  if remote_components else None)
        self._run = run
        self._cache: dict[str, StreamSource] = {}

    # -- cache ----------------------------------------------------------
    def cached(self, track_id: str) -> StreamSource | None:
        source = self._cache.get(track_id)
        if source is None:
            return None
        if source.expires_at and source.expires_at <= time.time() \
                + EXPIRY_MARGIN_S:
            self._cache.pop(track_id, None)
            return None
        return source

    def invalidate(self, track_id: str) -> None:
        self._cache.pop(track_id, None)

    def clear(self) -> None:
        self._cache.clear()

    # -- cookies ----------------------------------------------------------
    def set_cookies(self, netscape_text: str) -> Path | None:
        if self.runtime_dir is None:
            return None
        jar = self.runtime_dir / COOKIE_FILENAME
        jar.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(jar.parent, 0o700)
        return write_cookie_jar(jar, netscape_text)

    def clear_cookies(self) -> None:
        if self.runtime_dir is not None:
            clear_cookie_jar(self.runtime_dir / COOKIE_FILENAME)

    # -- resolving --------------------------------------------------------
    def resolve(self, track_id: str, *, low_data: bool = False
                ) -> StreamSource:
        if not track_id:
            raise EngineError(ErrorKind.NOT_FOUND, "empty track id")
        hit = self.cached(track_id)
        if hit is not None:
            return hit
        found = self.resolve_many([track_id], low_data=low_data)
        return found[track_id]

    def resolve_many(self, track_ids: list[str], *,
                     low_data: bool = False) -> dict[str, StreamSource]:
        wanted = [track_id for track_id in dict.fromkeys(track_ids)
                  if track_id]
        missing = [track_id for track_id in wanted
                   if self.cached(track_id) is None]
        if missing:
            payload = {
                "items": [{"id": track_id, "low_data": low_data}
                          for track_id in missing],
                "cookie_file": (cookie_file(self.runtime_dir)
                                if self.signed_in else None),
                "player_clients": player_clients(
                    self.signed_in, self.clients_override),
                "plugin_dirs": plugin_dirs(self.engine_dir),
                "js_runtimes": self.runtimes,
                "extractor_args": self.extractor_args,
                "remote_components": self.remote_components,
                "signed_in": self.signed_in,
                "helper_ready": self.helper_ready,
            }
            response = self._run_payload(payload)
            by_id = {str(item.get("id")): item
                     for item in response.get("results", [])
                     if isinstance(item, dict)}
            first_error: EngineError | None = None
            for track_id in missing:
                item = by_id.get(track_id)
                if item is None:
                    error = EngineError(ErrorKind.OTHER,
                                        f"no result for {track_id}")
                    if first_error is None:
                        first_error = error
                    continue
                if item.get("ok"):
                    self._cache[track_id] = _result_to_source(item)
                elif first_error is None:
                    first_error = _result_to_error(track_id, item)
            if first_error is not None:
                raise first_error
        return {track_id: self._cache[track_id] for track_id in wanted
                if track_id in self._cache}

    def _run_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._run is not None:
            response = self._run(payload)
            if not isinstance(response, dict):
                raise EngineError(ErrorKind.OTHER, "bad worker response")
            return response
        python = self.python or worker_python(self.engine_dir)
        try:
            done = subprocess.run(
                [python, "-m", WORKER_MODULE],
                input=json.dumps(payload).encode(), env=_worker_env(),
                capture_output=True, timeout=self.timeout_s, check=False,
                preexec_fn=_be_nice if os.name == "posix" else None)
        except subprocess.TimeoutExpired as exc:
            raise EngineError(ErrorKind.TIMEOUT,
                              f"resolve timed out: {exc}") from exc
        except OSError as exc:
            raise EngineError(ErrorKind.OTHER,
                              f"resolve worker failed: {exc}") from exc
        if done.returncode != 0:
            detail = _text(done.stderr).strip()[-300:]
            raise EngineError(ErrorKind.OTHER,
                              detail or "resolve worker failed")
        try:
            response = json.loads(_text(done.stdout) or "{}")
        except json.JSONDecodeError as exc:
            raise EngineError(ErrorKind.OTHER,
                              f"bad worker response: {exc}") from exc
        if not isinstance(response, dict):
            raise EngineError(ErrorKind.OTHER, "bad worker response")
        return response
