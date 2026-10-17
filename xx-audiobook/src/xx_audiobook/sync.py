"""Session sync timing and writes (M2).

Mirrors playback/SyncScheduler.kt (15 s cadence while playing with a
60 s screen-off/power-save backoff, immediate writes on pause/seek/
chapter/stop, monotonic listened_s with a 5 s max tick gap, per-book
reset) and playback/ProgressSync.kt (local-first write through M1's
ProgressRepository, session sync/close, gone-session reopen with
old-to-new remap, progress_applied merge).

Synchronous: the owner calls tick() every second (the service wires a
GLib timeout; tests drive it with a fake clock) and performs the
blocking sink on a worker thread. No threads or sleeps here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from xx_audiobook import api as api_module

INTERVAL_MS = 15000
BACKOFF_INTERVAL_MS = 60000
TICK_MS = 1000
MAX_TICK_GAP_MS = 5000
MAX_HOPS = 16


class Reason(Enum):
    INTERVAL = "interval"
    PAUSE = "pause"
    SEEK = "seek"
    CHAPTER = "chapter"
    STOP = "stop"


@dataclass
class Tick:
    reason: Reason
    item_id: str
    position_ms: int
    listened_ms: int


class SyncEnv:
    """Null item id when nothing is loaded."""

    def item_id(self) -> str | None:
        raise NotImplementedError

    def is_playing(self) -> bool:
        raise NotImplementedError

    def position_ms(self) -> int:
        raise NotImplementedError

    def chapter_index(self) -> int:
        raise NotImplementedError

    def screen_on(self) -> bool:
        raise NotImplementedError

    def power_save(self) -> bool:
        raise NotImplementedError


class SyncScheduler:
    def __init__(self, env: SyncEnv, clock) -> None:
        self._env = env
        self._clock = clock
        self._last_write_at = 0
        self._last_tick_at = 0
        self._last_chapter = -1
        self._last_item: str | None = None
        self._listened_ms = 0
        self._started = False

    @property
    def listened_ms(self) -> int:
        return self._listened_ms

    def start(self) -> None:
        now = self._clock()
        self._last_tick_at = now
        self._last_write_at = now
        self._last_item = self._env.item_id()
        self._started = True

    def stop(self) -> None:
        self._started = False

    def reset(self) -> None:
        self._listened_ms = 0
        self._last_chapter = -1
        self._last_item = self._env.item_id()
        now = self._clock()
        self._last_tick_at = now
        self._last_write_at = now

    def listened_now(self) -> int:
        self._account()
        return self._listened_ms

    def on_pause(self) -> Tick | None:
        return self._fire(Reason.PAUSE)

    def on_seek(self) -> Tick | None:
        return self._fire(Reason.SEEK)

    def on_stop(self) -> Tick | None:
        self._account()
        return self._write(Reason.STOP)

    def interval_ms(self) -> int:
        if not self._env.screen_on() or self._env.power_save():
            return BACKOFF_INTERVAL_MS
        return INTERVAL_MS

    def tick(self) -> Tick | None:
        item = self._env.item_id()
        if item is None:
            return None
        if item != self._last_item:
            self.reset()
        if not self._account():
            return None
        chapter = self._env.chapter_index()
        if self._last_chapter >= 0 and chapter != self._last_chapter:
            self._last_chapter = chapter
            return self._write(Reason.CHAPTER)
        self._last_chapter = chapter
        if self._clock() - self._last_write_at >= self.interval_ms():
            return self._write(Reason.INTERVAL)
        return None

    def _account(self) -> bool:
        now = self._clock()
        playing = self._env.is_playing()
        if playing:
            self._listened_ms += min(max(now - self._last_tick_at, 0),
                                     MAX_TICK_GAP_MS)
        self._last_tick_at = now
        return playing

    def _fire(self, reason: Reason) -> Tick | None:
        self._account()
        return self._write(reason)

    def _write(self, reason: Reason) -> Tick | None:
        item = self._env.item_id()
        if item is None:
            return None
        self._last_write_at = self._clock()
        return Tick(reason, item, self._env.position_ms(),
                    self._listened_ms)


def is_gone(exc: api_module.Http) -> bool:
    """The server no longer takes syncs on this session."""
    return exc.code == 404 or (
        exc.code == 409 and exc.reason == "session_closed")


class ProgressSync:
    """The write behind every Tick: local first, then the server session.

    api is an AudiobookApi bound to the estate base with the in-memory
    bearer; progress is M1's ProgressRepository; set_last_played(item,
    position_s) keeps the cold-start pointer; request_flush() asks for a
    background flush of pending_sync; on_session_changed(item, old, new)
    reports old-to-new id remaps (new None when dropped).
    """

    def __init__(self, api, progress, set_last_played=None,
                 request_flush=None, device_label: str = "",
                 on_session_changed=None) -> None:
        self._api = api
        self._progress = progress
        self._set_last_played = (
            set_last_played if set_last_played is not None
            else lambda _item, _pos: None)
        self._request_flush = (
            request_flush if request_flush is not None else lambda: None)
        self._device_label = device_label
        self._on_session_changed = on_session_changed
        self._listened: dict[str, int] = {}
        self._remapped: dict[str, str | None] = {}

    def last_listened_ms(self, session_id: str | None) -> int:
        if session_id is None:
            return 0
        live = self.live_session(session_id)
        own = self._listened.get(session_id, 0)
        if live is None or live == session_id:
            return own
        return max(own, self._listened.get(live, 0))

    def live_session(self, session_id: str | None) -> str | None:
        current = session_id
        if current is None:
            return None
        for _ in range(MAX_HOPS):
            if current not in self._remapped:
                return current
            replacement = self._remapped[current]
            if replacement is None:
                return None
            current = replacement
        return current

    def is_closed(self, session_id: str | None) -> bool:
        return session_id is not None and (
            self.live_session(session_id) is None)

    def mark_closed(self, item_id: str, session_id: str | None) -> None:
        live = self.live_session(session_id)
        if live is None:
            return
        self._remap(item_id, live, None)

    def write(self, tick: Tick, session_id: str | None,
              duration_ms: int, speed: float) -> None:
        position_s = tick.position_ms / 1000.0
        duration_s = duration_ms / 1000.0 if duration_ms > 0 else None
        stamp = self._progress.record(tick.item_id, position_s,
                                      duration_s, speed)
        self._set_last_played(tick.item_id, position_s)
        if tick.reason in (Reason.PAUSE, Reason.SEEK, Reason.STOP):
            self._progress.log_event(tick.item_id,
                                     tick.reason.value, position_s, speed)
        live = self.live_session(session_id)
        if live is None:
            self._request_flush()
            return
        self._listened[live] = max(self._listened.get(live, 0),
                                   tick.listened_ms)
        body = api_module.SyncRequest(
            position_s=position_s, listened_s=tick.listened_ms / 1000.0,
            speed=speed, updated_at=stamp / 1000.0)
        try:
            self._send(tick, live, body)
            if tick.reason == Reason.STOP:
                self._remap(tick.item_id, live, None)
            self._progress.mark_synced(tick.item_id, stamp)
        except api_module.Http as exc:
            if not is_gone(exc):
                self._request_flush()
                return
            if tick.reason == Reason.STOP:
                self._remap(tick.item_id, live, None)
                self._request_flush()
                return
            fresh = self._reopen(tick.item_id)
            self._remap(tick.item_id, live, fresh)
            if fresh is None:
                self._request_flush()
                return
            try:
                retry = api_module.SyncRequest(
                    position_s=body.position_s, listened_s=0.0,
                    speed=body.speed, updated_at=body.updated_at)
                self._send(tick, fresh, retry)
                self._progress.mark_synced(tick.item_id, stamp)
            except api_module.ApiError:
                self._request_flush()
        except api_module.ApiError:
            self._request_flush()

    def _send(self, tick: Tick, session_id: str,
              body: api_module.SyncRequest) -> None:
        if tick.reason == Reason.STOP:
            self._api.close_session(
                session_id,
                api_module.CloseRequest(
                    position_s=body.position_s,
                    listened_s=body.listened_s, speed=body.speed,
                    updated_at=body.updated_at))
            return
        answer = self._api.sync(session_id, body)
        if not answer.progress_applied and answer.progress is not None:
            self._progress.merge_server(tick.item_id, answer.progress)

    def _reopen(self, item_id: str) -> str | None:
        try:
            play = self._api.play(
                item_id, api_module.PlayRequest(device=self._device_label))
        except api_module.ApiError:
            return None
        return play.session_id or None

    def _remap(self, item_id: str, old: str, new: str | None) -> None:
        if old in self._remapped and self._remapped[old] == new:
            return
        self._remapped[old] = new
        if self._on_session_changed is not None:
            try:
                self._on_session_changed(item_id, old, new)
            except Exception:
                pass
