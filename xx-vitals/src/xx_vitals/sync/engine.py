"""Sync engine: ordered push drain + delta pull cursor state machine.

Port of sync/SyncEngine.kt to synchronous Python. Push drains the
pending_write queue oldest-first, deleting rows only on 2xx; 507 pauses;
401 aborts to re-login; permanent 4xx / unparseable / unknown kinds
dead-letter; transient backs off then raises retryable. Pull always runs;
the since cursor is the raw server ISO, and the cursor advances only to a
server-vouched stamp.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from xx_vitals.store import entries
from xx_vitals.store.db import SyncState
from xx_vitals.sync.api import (
    EPOCH_ISO,
    AuthExpired,
    BatchStamp,
    ChangesPage,
    Ok,
    Rejected,
    Transient,
    VolumeFull,
    from_iso,
    to_iso,
)

PAGE_SIZE = 50
DEAD_LETTER_ATTEMPTS = 2147483646
BACKOFF_MILLIS = [1000, 2000, 4000, 8000, 16000, 32000, 60000]


class SyncPermanentException(Exception):
    pass


class SyncAuthException(SyncPermanentException):
    pass


class SyncConfigException(SyncPermanentException):
    pass


class SyncRetryableException(Exception):
    pass


@dataclass(frozen=True)
class _Done:
    pass


@dataclass(frozen=True)
class _Permanent:
    reason: str


@dataclass(frozen=True)
class _AuthRequired:
    pass


@dataclass(frozen=True)
class _VolumeFull:
    pass


@dataclass(frozen=True)
class _Flaky:
    reason: str | None


_WEIGHT_PAYLOAD_RE = re.compile(
    r'\{"source_id":"([^"]*)","measured_at":([0-9]+),"weight_kg":([0-9]+(?:\.[0-9]+)?)\}'
)


@dataclass(frozen=True)
class QueuedWeight:
    source_id: str
    measured_at_ms: int
    weight_kg: float


def parse_weight_payload(payload_json: str) -> QueuedWeight | None:
    match = _WEIGHT_PAYLOAD_RE.fullmatch(payload_json)
    if match is None:
        return None
    try:
        measured = int(match.group(2))
        weight = float(match.group(3))
    except ValueError:
        return None
    return QueuedWeight(source_id=match.group(1), measured_at_ms=measured, weight_kg=weight)


class SyncEngine:
    def __init__(
        self, pending_dao, sync_dao, weight_dao, activity_dao, daily_dao, api,
        now=None, backoff_millis=None, delay=None,
    ) -> None:
        self._pending = pending_dao
        self._sync = sync_dao
        self._weights = weight_dao
        self._activities = activity_dao
        self._dailies = daily_dao
        self._api = api
        self._now = now if now is not None else (lambda: int(time.time() * 1000))
        self._backoff = list(backoff_millis) if backoff_millis is not None else list(BACKOFF_MILLIS)
        self._delay = delay if delay is not None else (lambda ms: None)

    def pending_change_count(self) -> int:
        return self._pending.pending_count()

    def push(self) -> bool:
        """Drain the queue oldest-first. Returns True when a 507 paused the drain."""
        sent_any = False
        while True:
            writes = self._pending.pending_next(PAGE_SIZE, DEAD_LETTER_ATTEMPTS)
            if not writes:
                break
            paused = False
            for write in writes:
                outcome = self._resolve_with_backoff(write)
                if isinstance(outcome, _Done):
                    self._pending.pending_delete(write.id)
                    sent_any = True
                elif isinstance(outcome, _Permanent):
                    self._pending.pending_record_failure(
                        write.id, DEAD_LETTER_ATTEMPTS, f"permanent: {outcome.reason}"
                    )
                elif isinstance(outcome, _AuthRequired):
                    raise SyncAuthException("NAS rejected the session token — sign in again")
                elif isinstance(outcome, _VolumeFull):
                    self._pending.pending_record_failure(
                        write.id, write.attempts + 1, "volume full (507)"
                    )
                    self._delay(self._backoff_first())
                    paused = True
                elif isinstance(outcome, _Flaky):
                    raise SyncRetryableException(
                        f"push still failing after backoff ({outcome.reason})"
                    )
                if paused:
                    break
            if paused:
                return True
        if sent_any:
            current = self._sync.sync_get()
            if current is None:
                self._sync.sync_upsert(SyncState(
                    id=1, last_pull_at=None, last_push_at=self._now(), last_pull_iso=None
                ))
            elif current.last_push_at is None:
                self._sync.sync_upsert(SyncState(
                    id=current.id, last_pull_at=current.last_pull_at,
                    last_push_at=self._now(), last_pull_iso=current.last_pull_iso,
                ))
        return False

    def _resolve_with_backoff(self, write):
        attempt = 0
        while True:
            outcome = self._dispatch(write)
            if isinstance(outcome, _Flaky):
                if attempt >= len(self._backoff):
                    self._pending.pending_record_failure(
                        write.id, write.attempts + attempt,
                        f"transient: gave up after {attempt} attempts ({outcome.reason})",
                    )
                    return outcome
                self._delay(self._backoff[min(attempt, len(self._backoff) - 1)])
                attempt += 1
            else:
                return outcome

    def _dispatch(self, write):
        kind = write.kind
        if kind in (entries.KIND_WEIGHT, entries.KIND_WEIGHT_UPDATE, entries.KIND_WEIGHT_DELETE):
            op = parse_weight_payload(write.payload_json)
            if op is None:
                return _Permanent("unparseable payload")
            if kind == entries.KIND_WEIGHT:
                return self._api_outcome(self._api.post_weight(to_iso(op.measured_at_ms), op.weight_kg))
            found = self._api.find_weight_id(op.measured_at_ms)
            if kind == entries.KIND_WEIGHT_UPDATE:
                if isinstance(found, Ok):
                    if found.value is None:
                        return _Permanent(f"stale edit: no server sample at {op.measured_at_ms}")
                    return self._api_outcome(self._api.patch_weight(
                        found.value, to_iso(op.measured_at_ms), op.weight_kg))
                return self._lookup_outcome(found)
            if isinstance(found, Ok):
                if found.value is None:
                    return _Done()
                return self._api_outcome(self._api.delete_weight(found.value))
            return self._lookup_outcome(found)
        if kind == entries.KIND_DAILY:
            op = entries.parse_daily(write.payload_json)
            if op is None:
                return _Permanent("unparseable payload")
            return self._api_outcome(self._api.post_daily(
                date=op.date, steps=op.steps, distance_m=op.distance_m,
                active_min=op.active_min, effort_points=op.effort_points,
                calories_kcal=op.calories_kcal, derived_mask=op.derived_mask,
            ))
        if kind == entries.KIND_ACTIVITY:
            op = entries.parse_activity(write.payload_json)
            if op is None:
                return _Permanent("unparseable payload")
            return self._api_outcome(self._api.post_activity(
                type=op.type, started_at_iso=to_iso(op.started_at),
                ended_at_iso=to_iso(op.ended_at), distance_m=op.distance_m,
                calories_kcal=op.calories_kcal, effort_points=op.effort_points,
                active_min=op.active_min, steps=op.steps,
            ))
        return _Permanent(f"unknown kind '{kind}'")

    def _api_outcome(self, result):
        if isinstance(result, Ok):
            return _Done()
        if isinstance(result, AuthExpired):
            return _AuthRequired()
        if isinstance(result, VolumeFull):
            return _VolumeFull()
        if isinstance(result, Rejected):
            detail = f": {result.detail[:120]}" if result.detail else ""
            return _Permanent(f"HTTP {result.code}{detail}")
        if isinstance(result, Transient):
            return _Flaky(result.reason)
        return _Flaky("unknown result")

    def _lookup_outcome(self, result):
        if isinstance(result, Ok):
            return _Flaky("unexpected lookup payload")
        if isinstance(result, AuthExpired):
            return _AuthRequired()
        if isinstance(result, VolumeFull):
            return _VolumeFull()
        if isinstance(result, Rejected):
            return _Permanent(f"lookup rejected: HTTP {result.code}")
        if isinstance(result, Transient):
            return _Flaky(result.reason)
        return _Flaky("unknown result")

    def _backoff_first(self) -> int:
        return self._backoff[0] if self._backoff else 0

    def pull(self) -> None:
        current = self._sync.sync_get()
        if current is not None and current.last_pull_iso is not None:
            since_iso = current.last_pull_iso
        elif current is not None and current.last_pull_at is not None:
            since_iso = to_iso(current.last_pull_at)
        else:
            since_iso = EPOCH_ISO
        result = self._api.pull_changes(since_iso)
        if isinstance(result, Ok):
            self._merge(result.value, previous=current)
            return
        if isinstance(result, AuthExpired):
            raise SyncAuthException("NAS rejected the session token — sign in again")
        if isinstance(result, VolumeFull):
            raise SyncRetryableException("changes pull answered 507")
        if isinstance(result, Rejected):
            raise SyncPermanentException(f"changes pull rejected: HTTP {result.code}")
        if isinstance(result, Transient):
            raise SyncRetryableException(f"changes pull failed: {result.reason}")
        raise SyncRetryableException("changes pull failed")

    def _merge(self, page: ChangesPage, previous: SyncState | None) -> None:
        for sample in page.weight:
            self._weights.upsert_weight(sample)
        if page.activities:
            self._activities.insert_all(page.activities)
        if page.dailies:
            self._dailies.upsert_all(page.dailies)
        new_cursor = self._watermark(page.max_batch_id)
        previous_iso = previous.last_pull_iso if previous is not None else None
        if new_cursor is not None and new_cursor != previous_iso:
            if previous is not None:
                self._sync.sync_upsert(SyncState(
                    id=previous.id, last_pull_at=from_iso(new_cursor),
                    last_push_at=previous.last_push_at, last_pull_iso=new_cursor,
                ))
            else:
                self._sync.sync_upsert(SyncState(
                    id=1, last_pull_at=from_iso(new_cursor),
                    last_push_at=None, last_pull_iso=new_cursor,
                ))

    def _watermark(self, max_batch_id: int) -> str | None:
        if max_batch_id <= 0:
            return None
        stamps = self._api.fetch_batch_stamps()
        if isinstance(stamps, Ok):
            for stamp in stamps.value:
                stamp_id = stamp.id if isinstance(stamp, BatchStamp) else stamp[0]
                stamp_iso = (
                    stamp.started_at_iso if isinstance(stamp, BatchStamp) else stamp[1]
                )
                if stamp_id == max_batch_id:
                    return stamp_iso
            return None
        if isinstance(stamps, AuthExpired):
            raise SyncAuthException("NAS rejected the session token — sign in again")
        return None
