"""Sync wire contract: SyncApi interface, ApiResult taxonomy, ISO cursors.

Port of sync/SyncApi.kt + sync/Wire.kt (pure part). No network here: the
production urllib implementation lives in http_api.py; tests drive SyncEngine
with the FakeSyncApi below (no sockets).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from xx_vitals.store.db import Activity, DailyMetric, WeightSample

EPOCH_ISO = "1970-01-01T00:00:00+00:00"
LOCAL_SOURCE_ID = "manual"


def to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def from_iso(iso: str) -> int | None:
    try:
        if len(iso) <= 10:
            day = datetime.strptime(iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return int(day.timestamp() * 1000)
        return int(
            datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000
        )
    except (ValueError, OverflowError):
        return None


def to_date_iso(ms: int) -> str:
    return to_iso(ms)[:10]


class ApiResult:
    pass


@dataclass(frozen=True)
class Ok(ApiResult):
    value: object


class AuthExpired(ApiResult):
    def __eq__(self, other: object) -> bool:
        return isinstance(other, AuthExpired)


class VolumeFull(ApiResult):
    def __eq__(self, other: object) -> bool:
        return isinstance(other, VolumeFull)


@dataclass(frozen=True)
class Rejected(ApiResult):
    code: int
    detail: str | None = None


@dataclass(frozen=True)
class Transient(ApiResult):
    reason: str | None = None


@dataclass(frozen=True)
class ChangesPage:
    weight: list
    activities: list
    dailies: list
    max_batch_id: int = 0


@dataclass(frozen=True)
class BatchStamp:
    id: int
    started_at_iso: str | None = None

    @property
    def started_at_ms(self) -> int | None:
        if self.started_at_iso is None:
            return None
        return from_iso(self.started_at_iso)


class SyncApi(Protocol):
    def post_weight(self, measured_at_iso: str, weight_kg: float) -> ApiResult:
        ...

    def patch_weight(
        self, server_id: int, measured_at_iso: str | None, weight_kg: float | None
    ) -> ApiResult:
        ...

    def delete_weight(self, server_id: int) -> ApiResult:
        ...

    def post_daily(
        self, date: str, steps: int | None, distance_m: float | None,
        active_min: int | None, effort_points: int | None,
        calories_kcal: float | None, derived_mask: int,
    ) -> ApiResult:
        ...

    def post_activity(
        self, type: str, started_at_iso: str, ended_at_iso: str,
        distance_m: float | None, calories_kcal: float | None,
        effort_points: int | None, active_min: int | None, steps: int | None,
    ) -> ApiResult:
        ...

    def find_weight_id(self, measured_at_ms: int) -> ApiResult:
        ...

    def pull_changes(self, since_iso: str) -> ApiResult:
        ...

    def fetch_batch_stamps(self) -> ApiResult:
        ...

    def fetch_batches(self) -> ApiResult:
        ...

    def fetch_daily(self, from_iso_date: str, to_iso_date: str) -> ApiResult:
        ...

    def fetch_activities(self, from_iso_date: str, to_iso_date: str) -> ApiResult:
        ...


class FakeSyncApi:
    """In-memory SyncApi for tests: script per-verb answers, record calls."""

    def __init__(self) -> None:
        self.posted_iso: list[str] = []
        self.posted_kg: list[float] = []
        self.patched: list[tuple] = []
        self.deleted_ids: list[int] = []
        self.find_calls = 0
        self.find_result: ApiResult = Ok(None)
        self.post_results: list[ApiResult] = []
        self.patch_results: list[ApiResult] = []
        self.delete_results: list[ApiResult] = []
        self.pull_result: ApiResult = Transient("not scripted")
        self.batch_stamps: list[BatchStamp] = []
        self.batch_result: ApiResult | None = None
        self.batches_result: ApiResult | None = None
        self.since_calls: list[str] = []

    def _next_post(self) -> ApiResult:
        return self.post_results.pop(0) if self.post_results else Ok(None)

    def post_weight(self, measured_at_iso: str, weight_kg: float) -> ApiResult:
        self.posted_iso.append(measured_at_iso)
        self.posted_kg.append(weight_kg)
        return self._next_post()

    def patch_weight(self, server_id: int, measured_at_iso, weight_kg) -> ApiResult:
        self.patched.append((server_id, measured_at_iso, weight_kg))
        return self.patch_results.pop(0) if self.patch_results else Ok(None)

    def delete_weight(self, server_id: int) -> ApiResult:
        self.deleted_ids.append(server_id)
        return self.delete_results.pop(0) if self.delete_results else Ok(None)

    def find_weight_id(self, measured_at_ms: int) -> ApiResult:
        self.find_calls += 1
        return self.find_result

    def pull_changes(self, since_iso: str) -> ApiResult:
        self.since_calls.append(since_iso)
        return self.pull_result

    def fetch_batch_stamps(self) -> ApiResult:
        if self.batch_result is not None:
            return self.batch_result
        return Ok(list(self.batch_stamps))

    def fetch_batches(self) -> ApiResult:
        if self.batches_result is not None:
            return self.batches_result
        return Ok([])

    def fetch_daily(self, from_iso_date: str, to_iso_date: str) -> ApiResult:
        raise NotImplementedError

    def fetch_activities(self, from_iso_date: str, to_iso_date: str) -> ApiResult:
        raise NotImplementedError

    def post_daily(self, date, steps, distance_m, active_min,
                   effort_points, calories_kcal, derived_mask) -> ApiResult:
        return self._next_post()

    def post_activity(self, type, started_at_iso, ended_at_iso, distance_m,
                      calories_kcal, effort_points, active_min, steps) -> ApiResult:
        return self._next_post()


def map_weight_row(
    measured_at_iso: str, weight_kg: float, batch_id: int | None = None,
    source_id: str = LOCAL_SOURCE_ID,
) -> WeightSample | None:
    ms = from_iso(measured_at_iso)
    if ms is None:
        return None
    return WeightSample(source_id=source_id, measured_at=ms, weight_kg=weight_kg, batch_id=batch_id)


def map_activity_row(
    type_: str, started_at_iso: str, ended_at_iso: str,
    distance_m: float | None = None, calories_kcal: float | None = None,
    effort_points: float | int | None = None, active_min: int | None = None,
    hr_avg: int | None = None, hr_max: int | None = None, steps: int | None = None,
    track_geojson: str | None = None, batch_id: int | None = None,
    source_id: str = LOCAL_SOURCE_ID,
) -> Activity:
    start = from_iso(started_at_iso) or 0
    end = from_iso(ended_at_iso) or 0
    points = None if effort_points is None else int(round(float(effort_points)))
    return Activity(
        source_id=source_id, type=type_, started_at=start, ended_at=end,
        distance_m=distance_m, calories_kcal=calories_kcal, effort_points=points,
        active_min=active_min, hr_avg=hr_avg, hr_max=hr_max, steps=steps,
        track_geojson=track_geojson, batch_id=batch_id,
    )


def map_daily_row(
    day: str, steps: int | None = None, distance_m: float | None = None,
    active_min: int | None = None, effort_points: float | int | None = None,
    calories_kcal: float | None = None, hr_avg: int | None = None,
    hr_min: int | None = None, hr_max: int | None = None,
    hr_sample_count: int | None = None, weight_avg_kg: float | None = None,
    weight_min_kg: float | None = None, weight_max_kg: float | None = None,
    derived_mask: int | None = None, batch_id: int | None = None,
    source_id: str = LOCAL_SOURCE_ID,
) -> DailyMetric:
    points = None if effort_points is None else int(round(float(effort_points)))
    return DailyMetric(
        source_id=source_id, date=day[:10], steps=steps, distance_m=distance_m,
        active_min=active_min, effort_points=points, calories_kcal=calories_kcal,
        hr_avg=hr_avg, hr_min=hr_min, hr_max=hr_max, hr_sample_count=hr_sample_count,
        weight_avg_kg=weight_avg_kg, weight_min_kg=weight_min_kg,
        weight_max_kg=weight_max_kg, derived_mask=derived_mask or 0, batch_id=batch_id,
    )
