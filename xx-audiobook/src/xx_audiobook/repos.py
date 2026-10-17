"""Library, progress and settings repositories (M1).

Mirrors data/repo/LibraryRepository.kt, ProgressRepository.kt (plus
ProgressMerge) and SettingsSync.kt: offline-first library cache,
local-first progress with the pending_sync queue and LWW merge, and the
synced playback settings. Synchronous; callers run them on worker threads.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from xx_audiobook import api as api_module
from xx_audiobook import store as store_module

PROMPT_THRESHOLD_S = 60.0


def seconds_to_ms(value: float) -> int:
    return int(value * 1000.0)


def ms_to_seconds(value: int) -> float:
    return value / 1000.0


def from_ms(value_ms: int) -> str:
    return datetime.fromtimestamp(value_ms / 1000.0,
                                 tz=timezone.utc).isoformat()


def from_seconds(value_s: float | None) -> str | None:
    if value_s is None:
        return None
    return from_ms(seconds_to_ms(value_s))


@dataclass
class LibraryItem:
    id: str
    title: str
    authors: str = ""
    narrators: str = ""
    series: str | None = None
    duration_s: float | None = None
    thumb_url: str | None = None
    position_s: float = 0.0
    is_finished: bool = False
    rating_average: float | None = None
    rating_count: int = 0
    missing: bool = False

    @property
    def fraction(self) -> float:
        if self.is_finished:
            return 1.0
        if not self.duration_s or self.duration_s <= 0:
            return 0.0
        return min(1.0, max(0.0, self.position_s / self.duration_s))


def _summary_to_item(summary, now_ms: int) -> store_module.Item:
    series = summary.series[0] if summary.series else None
    return store_module.Item(
        id=summary.id, title=summary.title,
        subtitle=summary.subtitle or None,
        authors=", ".join(summary.authors),
        narrators=", ".join(summary.narrators),
        series_name=series.name if series else None,
        series_sequence=(series.sequence or None
                         if series and series.sequence.strip() else None),
        duration_s=summary.duration_s,
        cover_url=summary.cover_url, thumb_url=summary.thumb_url,
        added_at=from_seconds(summary.added_at),
        last_played_at=(from_seconds(summary.progress.last_played_at)
                        if summary.progress else None),
        rating_average=summary.rating.average,
        rating_count=summary.rating.count,
        missing=summary.missing, fetched_at_ms=now_ms)


def _detail_to_item(detail, now_ms: int) -> store_module.Item:
    series = detail.series[0] if detail.series else None
    return store_module.Item(
        id=detail.id, title=detail.title,
        subtitle=detail.subtitle or None,
        authors=", ".join(detail.authors),
        narrators=", ".join(detail.narrators),
        series_name=series.name if series else None,
        series_sequence=(series.sequence or None
                         if series and series.sequence.strip() else None),
        duration_s=detail.duration_s,
        cover_url=detail.cover_url, thumb_url=detail.thumb_url,
        added_at=from_seconds(detail.added_at),
        last_played_at=(from_seconds(detail.progress.last_played_at)
                        if detail.progress else None),
        rating_average=detail.rating.average,
        rating_count=detail.rating.count,
        missing=detail.missing, fetched_at_ms=now_ms)


def _progress_to_store(item_id: str,
                       remote: api_module.Progress) -> store_module.Progress:
    return store_module.Progress(
        item_id=remote.item_id or item_id,
        position_s=remote.position_s,
        duration_s=(remote.duration_s
                    if remote.duration_s and remote.duration_s > 0 else None),
        is_finished=remote.is_finished,
        updated_at_ms=(seconds_to_ms(remote.updated_at)
                       if remote.updated_at is not None else 0),
        speed=(remote.speed if remote.updated_at is not None else None))


def _joined_to_library(row: store_module.ItemWithProgress) -> LibraryItem:
    item = row.item
    series = None
    if item.series_name:
        series = (f"{item.series_name} #{item.series_sequence}"
                  if item.series_sequence else item.series_name)
    return LibraryItem(
        id=item.id, title=item.title, authors=item.authors,
        narrators=item.narrators, series=series,
        duration_s=item.duration_s, thumb_url=item.thumb_url,
        position_s=row.position_s or 0.0,
        is_finished=row.is_finished or False,
        rating_average=item.rating_average,
        rating_count=item.rating_count, missing=item.missing)


class LibraryRepository:
    """Offline-first library: screens read the cache; refresh writes it."""

    def __init__(self, api, db: store_module.Database,
                 now_ms=None) -> None:
        self._api = api
        self._db = db
        self._now_ms = now_ms if now_ms is not None else (
            lambda: int(time.time() * 1000))

    def observe(self, query: store_module.LibraryQuery) -> list:
        return [_joined_to_library(r)
                for r in self._db.items().query(query)]

    def observe_continue(self, limit: int = 20) -> list:
        return [_joined_to_library(r)
                for r in self._db.items().continue_listening(limit)]

    def cached_detail(self, item_id: str):
        row = self._db.details().get(item_id)
        if row is None:
            return None
        try:
            return api_module.ItemDetail.from_dict(json.loads(row.json))
        except (ValueError, TypeError, KeyError, AttributeError):
            return None

    def refresh(self, query: store_module.LibraryQuery) -> tuple:
        """Fetch one page; returns (ok, has_more, error)."""
        page = query.offset // query.limit + 1
        sort = {store_module.Sort.TITLE: "title",
                store_module.Sort.AUTHOR: "author",
                store_module.Sort.ADDED: "added",
                store_module.Sort.LAST_PLAYED: "last_played",
                store_module.Sort.RATING: "rating"}[query.sort]
        wire_filter = ({store_module.Filter.IN_PROGRESS: "in_progress",
                        store_module.Filter.FINISHED: "finished",
                        store_module.Filter.NOT_STARTED: "not_started"}
                       .get(query.filter))
        try:
            result = self._api.items(api_module.ItemsQuery(
                page=page, limit=query.limit, q=query.q, sort=sort,
                descending=query.descending, filter=wire_filter))
        except api_module.ApiError as exc:
            return (False, False, exc)
        now = self._now_ms()
        self._db.items().upsert(
            [_summary_to_item(r, now) for r in result.results])
        for row in result.results:
            if row.progress is not None:
                self._db.progress().merge_newer(
                    _progress_to_store(row.id, row.progress))
        return (True, result.page * result.limit < result.total, None)

    def refresh_continue(self) -> tuple:
        try:
            rows = self._api.continue_listening()
        except api_module.ApiError as exc:
            return (False, exc)
        self.cache_summaries(rows)
        return (True, None)

    def cache_summaries(self, rows: list) -> None:
        now = self._now_ms()
        self._db.items().upsert([_summary_to_item(r, now) for r in rows])
        for row in rows:
            if row.progress is not None:
                self._db.progress().merge_newer(
                    _progress_to_store(row.id, row.progress))

    def observe_ids(self, ids: list) -> list:
        return self.observe(store_module.LibraryQuery(
            ids=list(ids), limit=max(len(ids), 1)))

    def refresh_detail(self, item_id: str) -> tuple:
        try:
            detail = self._api.item(item_id)
        except api_module.ApiError as exc:
            return (None, exc)
        now = self._now_ms()
        self._db.details().upsert(store_module.ItemDetail(
            item_id, json.dumps(detail_to_wire(detail)), now))
        self._db.items().upsert([_detail_to_item(detail, now)])
        if detail.progress is not None:
            self._db.progress().merge_newer(
                _progress_to_store(item_id, detail.progress))
        return (detail, None)

    def detail(self, item_id: str) -> tuple:
        """Network first, cache on failure (offline book screen)."""
        detail, error = self.refresh_detail(item_id)
        if error is None:
            return (detail, None)
        cached = self.cached_detail(item_id)
        if cached is not None:
            return (cached, None)
        return (None, error)


def detail_to_wire(detail: api_module.ItemDetail) -> dict:
    return {
        "id": detail.id, "title": detail.title,
        "subtitle": detail.subtitle, "authors": detail.authors,
        "narrators": detail.narrators,
        "series": [{"id": s.id, "name": s.name, "sequence": s.sequence}
                   for s in detail.series],
        "duration_s": detail.duration_s, "status": detail.status,
        "added_at": detail.added_at, "cover_url": detail.cover_url,
        "thumb_url": detail.thumb_url,
        "progress": (None if detail.progress is None else {
            "item_id": detail.progress.item_id,
            "position_s": detail.progress.position_s,
            "duration_s": detail.progress.duration_s,
            "speed": detail.progress.speed,
            "is_finished": detail.progress.is_finished,
            "started_at": detail.progress.started_at,
            "finished_at": detail.progress.finished_at,
            "last_played_at": detail.progress.last_played_at,
            "updated_at": detail.progress.updated_at}),
        "rating": {
            "average": detail.rating.average, "count": detail.rating.count,
            "mine": (None if detail.rating.mine is None else {
                "id": detail.rating.mine.id,
                "stars": detail.rating.mine.stars,
                "body": detail.rating.mine.body,
                "created_at": detail.rating.mine.created_at,
                "updated_at": detail.rating.mine.updated_at})},
        "description": detail.description, "publisher": detail.publisher,
        "published_year": detail.published_year,
        "language": detail.language, "genres": detail.genres,
        "asin": detail.asin, "isbn": detail.isbn,
        "explicit": detail.explicit, "abridged": detail.abridged,
        "size_bytes": detail.size_bytes, "file_count": detail.file_count,
        "updated_at": detail.updated_at,
        "chapters": [{"index": c.index, "title": c.title,
                      "start_s": c.start_s, "end_s": c.end_s}
                     for c in detail.chapters],
        "files": [{"file_id": f.file_id, "index": f.index, "url": f.url,
                   "start_offset_s": f.start_offset_s,
                   "duration_s": f.duration_s, "mime": f.mime,
                   "size": f.size, "sha256": f.sha256}
                  for f in detail.files],
        "author_refs": [{"id": r.id, "name": r.name}
                        for r in detail.author_refs],
        "narrator_refs": [{"id": r.id, "name": r.name}
                          for r in detail.narrator_refs],
        "playable": detail.playable, "issues": detail.issues,
    }


@dataclass
class ProgressConflict:
    item_id: str
    local_s: float
    server_s: float
    duration_s: float | None
    local_updated_ms: int
    server_updated_ms: int

    @property
    def server_is_newer(self) -> bool:
        return self.server_updated_ms > self.local_updated_ms


class ProgressDecision:
    KEEP_LOCAL = "keep_local"
    TAKE_SERVER = "take_server"
    PROMPT = "prompt"


def decide_progress(local: store_module.Progress | None,
                    remote: api_module.Progress | None):
    """LWW merge rules, pure. Prompt only when > 60 s apart, unfinished."""
    if remote is None or remote.updated_at is None:
        return (ProgressDecision.KEEP_LOCAL, None)
    remote_ms = seconds_to_ms(remote.updated_at)
    if local is None or local.updated_at_ms <= 0:
        return (ProgressDecision.TAKE_SERVER, None)
    if local.is_finished != remote.is_finished:
        if remote_ms > local.updated_at_ms:
            return (ProgressDecision.TAKE_SERVER, None)
        return (ProgressDecision.KEEP_LOCAL, None)
    if (abs(local.position_s - remote.position_s) > PROMPT_THRESHOLD_S
            and not local.is_finished):
        duration = local.duration_s
        if duration is None and remote.duration_s and remote.duration_s > 0:
            duration = remote.duration_s
        return (ProgressDecision.PROMPT, ProgressConflict(
            item_id=local.item_id, local_s=local.position_s,
            server_s=remote.position_s, duration_s=duration,
            local_updated_ms=local.updated_at_ms,
            server_updated_ms=remote_ms))
    if remote_ms > local.updated_at_ms:
        return (ProgressDecision.TAKE_SERVER, None)
    return (ProgressDecision.KEEP_LOCAL, None)


@dataclass
class FlushResult:
    sent: int = 0
    remaining: int = 0
    offline: bool = False


class ProgressRepository:
    """Local-first progress; the queue flushes with the client updated_at."""

    def __init__(self, api, db: store_module.Database,
                 now_ms=None) -> None:
        self._api = api
        self._db = db
        self._now_ms = now_ms if now_ms is not None else (
            lambda: int(time.time() * 1000))

    def get(self, item_id: str) -> store_module.Progress | None:
        return self._db.progress().get(item_id)

    def pending_count(self) -> int:
        return self._db.pending().count()

    def record(self, item_id: str, position_s: float,
               duration_s: float | None = None,
               speed: float | None = None) -> int:
        now = self._now_ms()
        local = self._db.progress().get(item_id)
        position = max(0.0, position_s)
        self._db.progress().upsert(store_module.Progress(
            item_id=item_id, position_s=position,
            duration_s=(duration_s if duration_s is not None
                        else (local.duration_s if local else None)),
            is_finished=local.is_finished if local else False,
            updated_at_ms=now,
            speed=speed if speed is not None
            else (local.speed if local else None)))
        queued = self._db.pending().get(item_id)
        self._db.pending().upsert(store_module.PendingSync(
            item_id, position,
            duration_s if duration_s is not None
            else (local.duration_s if local else None),
            queued.is_finished if queued else None, now))
        return now

    def merge_server(self, item_id: str,
                     server: api_module.Progress
                     ) -> store_module.Progress:
        return self._db.progress().merge_newer(
            _progress_to_store(item_id, server))

    def mark_synced(self, item_id: str, updated_at_ms: int) -> None:
        self._db.pending().ack(item_id, updated_at_ms)

    def log_event(self, item_id: str, event: str, position_s: float,
                  speed: float) -> None:
        self._db.history().insert(store_module.HistoryEvent(
            item_id=item_id, event=event, position_s=position_s,
            speed=speed, at_ms=self._now_ms()))

    def set_finished(self, item_id: str, finished: bool) -> None:
        now = self._now_ms()
        local = self._db.progress().get(item_id)
        position = local.position_s if local else 0.0
        self._db.progress().upsert(store_module.Progress(
            item_id, position,
            local.duration_s if local else None, finished, now,
            local.speed if local else None))
        self._db.pending().upsert(store_module.PendingSync(
            item_id, position,
            local.duration_s if local else None, finished, now))
        self._db.history().insert(store_module.HistoryEvent(
            item_id=item_id, event="finish" if finished else "unfinish",
            position_s=position, at_ms=now))

    def speed_for(self, item_id: str) -> float | None:
        row = self._db.progress().get(item_id)
        return row.speed if row else None

    def set_speed(self, item_id: str, speed: float) -> None:
        clamped = min(3.0, max(0.5, speed))
        if self._db.progress().set_speed(item_id, clamped) == 0:
            self._db.progress().upsert(store_module.Progress(
                item_id, 0.0, None, False, 0, clamped))

    def pull(self, item_id: str) -> tuple:
        try:
            remote = self._api.progress(item_id)
        except api_module.ApiError as exc:
            return (None, exc)
        if remote is None:
            return (self._db.progress().get(item_id), None)
        return (self._db.progress().merge_newer(
            _progress_to_store(item_id, remote)), None)

    def pull_all(self) -> tuple:
        try:
            rows = self._api.all_progress()
        except api_module.ApiError as exc:
            return (0, exc)
        for row in rows:
            self._db.progress().merge_newer(
                _progress_to_store(row.item_id, row))
        return (len(rows), None)

    def compare_on_open(self, item_id: str):
        try:
            remote = self._api.progress(item_id)
        except api_module.ApiError:
            return None
        if remote is None:
            return None
        local = self._db.progress().get(item_id)
        decision, conflict = decide_progress(local, remote)
        if decision == ProgressDecision.PROMPT:
            return conflict
        if decision == ProgressDecision.TAKE_SERVER:
            self._db.progress().merge_newer(
                _progress_to_store(item_id, remote))
        return None

    def resolve(self, conflict: ProgressConflict,
                use_server: bool) -> None:
        position = conflict.server_s if use_server else conflict.local_s
        self.record(conflict.item_id, position, conflict.duration_s)

    def flush(self) -> FlushResult:
        sent = 0
        for row in self._db.pending().all():
            try:
                if row.is_finished is True:
                    server = self._api.finish(row.item_id)
                elif row.is_finished is False:
                    server = self._api.unfinish(row.item_id)
                    if row.position_s > 0:
                        server = self._put_row(row)
                else:
                    server = self._put_row(row)
            except (api_module.Unreachable, api_module.NotSignedIn):
                return FlushResult(sent, self._db.pending().count(), True)
            except api_module.Http as exc:
                if (400 <= exc.code <= 499 and exc.code not in
                        (401, 408, 429)):
                    self._db.pending().ack(row.item_id, row.updated_at_ms)
                else:
                    self._db.pending().bump_attempts(row.item_id)
                continue
            self._db.pending().ack(row.item_id, row.updated_at_ms)
            self._db.progress().merge_newer(
                _progress_to_store(row.item_id, server))
            sent += 1
        return FlushResult(sent, self._db.pending().count(), False)

    def _put_row(self, row: store_module.PendingSync):
        local = self._db.progress().get(row.item_id)
        return self._api.put_progress(
            row.item_id,
            api_module.ProgressPut(
                position_s=row.position_s, duration_s=row.duration_s,
                speed=(local.speed if local else None),
                updated_at=ms_to_seconds(row.updated_at_ms)))


class SettingsSync:
    """Playback settings shared with the web, LWW on updated_at."""

    def __init__(self, api, prefs, now_s=None) -> None:
        self._api = api
        self._prefs = prefs
        self._now_s = (now_s if now_s is not None else (lambda: time.time()))
        self._lock = threading.Lock()

    def local_edited(self) -> bool:
        self._prefs.mark_settings_edited(self._now_s())
        return self.push()

    def push(self) -> bool:
        with self._lock:
            return self._push_locked()

    def sync(self) -> bool:
        with self._lock:
            if self._prefs.current().settings_dirty:
                return self._push_locked()
            try:
                server = self._api.settings()
            except api_module.ApiError:
                return False
            if server.updated_at is None:
                return True
            local = self._prefs.current()
            if local.settings_dirty:
                return self._push_locked()
            if stamp_of(local) is None or server.updated_at > stamp_of(local):
                self._apply(server, server.updated_at)
            return True

    def _push_locked(self) -> bool:
        prefs = self._prefs.current()
        if not prefs.settings_dirty:
            return True
        stamp = prefs.settings_updated_at or self._now_s()
        body = api_module.UserSettingsPut(
            skip_back_s=prefs.skip_back_seconds,
            skip_forward_s=prefs.skip_seconds,
            default_speed=prefs.default_speed,
            auto_rewind_s=prefs.auto_rewind_seconds,
            updated_at=stamp)
        try:
            answer = self._api.put_settings(body)
        except api_module.ApiError:
            return False
        if answer.applied is False:
            at = answer.updated_at
            if (at is not None
                    and self._prefs.current().settings_updated_at == stamp):
                self._apply(answer, at)
            else:
                self._prefs.mark_settings_synced(stamp)
        else:
            self._prefs.mark_settings_synced(stamp)
        return True

    def _apply(self, settings: api_module.UserSettings, stamp: float) -> None:
        self._prefs.apply_remote_settings(
            skip_back=settings.skip_back_s,
            skip_forward=settings.skip_forward_s,
            speed=settings.default_speed,
            auto_rewind=settings.auto_rewind_s, at_s=stamp)


def stamp_of(prefs) -> float | None:
    value = prefs.settings_updated_at
    if not value:
        return None
    return value
