"""The process-wide YouTube Music catalog client.

ytmusicapi is plain ``requests`` in-process. One ``YTMusic`` client serves
the whole process (window and player service each build their own), and a
single lock serialises every call: the session is also used from worker
threads and must never see two requests at once. The library is imported
lazily, so constructing a ``Catalog`` (or the engine) does no I/O and
needs no dependency installed; tests inject a fake client instead.

``Catalog.fetch`` runs one client method under the lock, maps transport
and library failures to ``EngineError`` kinds, parses the response, and
feeds the per-capability health counters the update banner (PORT_SPEC
section 4.1) reads: three ``SOURCE_CHANGED`` (or ``HELPER_MISSING``)
within ten minutes means YouTube broke something, and one success clears
the capability again.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, TypeVar

from xx_music.engine import EngineError, ErrorKind

T = TypeVar("T")

#: Failures counted per capability for the update banner.
COUNTED = frozenset({ErrorKind.SOURCE_CHANGED, ErrorKind.HELPER_MISSING})
#: Three counted failures within this window trip the banner.
TRIP_AFTER = 3
WINDOW_S = 10 * 60


class Health:
    """Per-capability failure counters with an injectable clock."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    def note_success(self, capability: str) -> None:
        with self._lock:
            self._failures.pop(capability, None)

    def note_failure(self, capability: str, kind: ErrorKind) -> None:
        if kind not in COUNTED:
            return
        with self._lock:
            self._failures.setdefault(capability, []).append(self._clock())

    def needs_update(self, capability: str) -> bool:
        with self._lock:
            recent = [t for t in self._failures.get(capability, [])
                      if self._clock() - t < WINDOW_S]
            if recent:
                self._failures[capability] = recent
            else:
                self._failures.pop(capability, None)
            return len(recent) >= TRIP_AFTER

    def counts(self) -> dict[str, int]:
        with self._lock:
            now = self._clock()
            return {cap: sum(1 for t in stamps if now - t < WINDOW_S)
                    for cap, stamps in self._failures.items()
                    if any(now - t < WINDOW_S for t in stamps)}


_LOCK = threading.RLock()
_CLIENTS: dict[str, Any] = {}


def _build_client(auth: Any) -> Any:
    from ytmusicapi import YTMusic
    return YTMusic(auth=auth) if auth is not None else YTMusic()


def shared_client(auth: Any = None) -> Any:
    """The process-wide client for this sign-in state (one at most each)."""
    key = "authed" if auth is not None else "anon"
    with _LOCK:
        client = _CLIENTS.get(key)
        if client is None:
            client = _build_client(auth)
            _CLIENTS[key] = client
        return client


def reset_shared_clients() -> None:
    """Drop cached clients. Tests only; the app never calls this."""
    with _LOCK:
        _CLIENTS.clear()


def _map_error(exc: BaseException, signed_in: bool) -> EngineError:
    """Client-side failures to EngineError kinds (pass-through first)."""
    if isinstance(exc, EngineError):
        return exc
    if isinstance(exc, (KeyError, IndexError, AttributeError, TypeError,
                        ValueError)):
        # An older (or newer) ytmusicapi tripping over a changed response,
        # e.g. the M0 KeyError: 'endpoint' in get_watch_playlist.
        return EngineError(ErrorKind.SOURCE_CHANGED, f"youtube: {exc!r}")
    try:
        from requests.exceptions import (ConnectionError as _Conn,
                                         HTTPError as _Http, Timeout as _Time)
    except ImportError:
        _Conn = _Http = _Time = ()  # type: ignore[assignment]
    if _Conn and isinstance(exc, _Conn):
        return EngineError(ErrorKind.OFFLINE, f"youtube: {exc!r}")
    if _Time and isinstance(exc, _Time):
        return EngineError(ErrorKind.TIMEOUT, f"youtube: {exc!r}")
    if _Http and isinstance(exc, _Http):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            return EngineError(ErrorKind.RATE_LIMITED, f"youtube: {exc!r}")
        if status in (401, 403):
            kind = (ErrorKind.AUTH_EXPIRED if signed_in
                    else ErrorKind.AUTH_REQUIRED)
            return EngineError(kind, f"youtube: {exc!r}")
    try:
        from ytmusicapi.exceptions import YTMusicError as _ApiError
    except ImportError:
        _ApiError = ()  # type: ignore[assignment]
    if _ApiError and isinstance(exc, _ApiError):
        text = str(exc).casefold()
        if any(word in text for word in ("auth", "login", "sign in",
                                         "permission", "forbidden")):
            kind = (ErrorKind.AUTH_EXPIRED if signed_in
                    else ErrorKind.AUTH_REQUIRED)
            return EngineError(kind, f"youtube: {exc!r}")
        return EngineError(ErrorKind.OTHER, f"youtube: {exc!r}")
    return EngineError(ErrorKind.OTHER, f"youtube: {exc!r}")


class Catalog:
    """One engine's view of the catalog: client, health, error mapping."""

    def __init__(self, auth: Any = None, client: Any = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._auth = auth
        self._client = client
        self.health = Health(clock)

    @property
    def signed_in(self) -> bool:
        return self._auth is not None

    def fetch(self, capability: str, method: str,
              parse: Callable[[Any], T], *args: Any, **kwargs: Any) -> T:
        """Call ``method`` under the process lock and parse its response."""
        with _LOCK:
            client = (self._client if self._client is not None
                      else shared_client(self._auth))
            try:
                raw = getattr(client, method)(*args, **kwargs)
            except Exception as exc:
                raise _map_error(exc, self.signed_in) from exc
        try:
            result = parse(raw)
        except EngineError as exc:
            self.health.note_failure(capability, exc.kind)
            raise
        except Exception as exc:
            self.health.note_failure(capability, ErrorKind.OTHER)
            raise EngineError(ErrorKind.OTHER,
                              f"youtube: {capability}: {exc!r}") from exc
        self.health.note_success(capability)
        return result
