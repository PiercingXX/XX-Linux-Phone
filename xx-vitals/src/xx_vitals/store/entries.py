"""Queue payload builders and offline-first repositories.

Ports WeightEntryModel.kt, ActivityEntryModel.kt, DailyEntryModel.kt,
DailyRows.kt and WeightRepository.kt (pure + queue parts, no Android).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from zoneinfo import ZoneInfo

from xx_vitals.core import metrics
from xx_vitals.store.db import Activity, DailyMetric, Database, PendingWrite, WeightSample

KIND_WEIGHT = "weight"
KIND_WEIGHT_UPDATE = "weight.update"
KIND_WEIGHT_DELETE = "weight.delete"
KIND_DAILY = "daily"
KIND_ACTIVITY = "activity"
SOURCE_MANUAL = "manual"
SOURCE_PHONE = "phone"
AMEND_WINDOW_MS = 48 * 60 * 60 * 1000


class WeightUnit(Enum):
    KG = "kg"
    LB = "lb"


def unit_for(units: str) -> WeightUnit:
    return WeightUnit.KG if units == "metric" else WeightUnit.LB


def to_kg(value: float, unit: WeightUnit) -> float:
    if unit == WeightUnit.KG:
        return value
    return metrics.lb_to_kg(value)


def from_kg(kg: float, unit: WeightUnit) -> float:
    if unit == WeightUnit.KG:
        return kg
    return metrics.kg_to_lb(kg)


def round_for_storage(kg: float) -> float:
    return float(Decimal(str(kg)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def weight_payload(source_id: str, measured_at: int, weight_kg: float) -> str:
    rounded = round_for_storage(weight_kg)
    return (
        '{"source_id":"' + _escape(source_id) + '","measured_at":'
        + str(measured_at) + ',"weight_kg":' + f"{rounded:.2f}" + "}"
    )


def weight_key_prefix(source_id: str, measured_at: int) -> str:
    return '{"source_id":"' + _escape(source_id) + '","measured_at":' + str(measured_at) + ","


def can_amend(measured_at: int, now: int) -> bool:
    return now - measured_at <= AMEND_WINDOW_MS


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def format_entry_date(measured_at: int, zone: ZoneInfo | str) -> str:
    resolved = ZoneInfo(zone) if isinstance(zone, str) else zone
    local = datetime.fromtimestamp(measured_at / 1000, tz=timezone.utc).astimezone(resolved)
    return f"{_MONTHS[local.month - 1]} {local.day}, {local.year}"


def measured_at_for(day, zone: ZoneInfo | str, now: int) -> int:
    resolved = ZoneInfo(zone) if isinstance(zone, str) else zone
    today = datetime.fromtimestamp(now / 1000, tz=timezone.utc).astimezone(resolved).date()
    if day == today:
        return now
    noon = datetime(day.year, day.month, day.day, 12, 0, tzinfo=resolved)
    return int(noon.timestamp() * 1000)


class ActivityType(Enum):
    WALK = ("walk", "\u25c8")
    RUN = ("run", "\u25b7")
    CYCLE = ("cycle", "\u25cb")
    OTHER = ("other", "\u00b7")

    def __init__(self, wire: str, glyph: str) -> None:
        self.wire = wire
        self.glyph = glyph


def glyph_for(type_name: str) -> str:
    lowered = type_name.lower()
    if lowered in ("walk", "walking"):
        return "\u25c8"
    if lowered in ("run", "running"):
        return "\u25b7"
    if lowered in ("cycle", "biking", "cycling", "ride"):
        return "\u25cb"
    if lowered == "phone steps":
        return "\u25c6"
    return "\u00b7"


def parse_duration_minutes(raw: str) -> int | None:
    try:
        number = int(raw.strip())
    except (ValueError, AttributeError):
        return None
    return number if 1 <= number <= 24 * 60 else None


def parse_optional_steps(raw: str) -> int | None:
    trimmed = raw.strip()
    if not trimmed:
        return None
    try:
        number = int(trimmed)
    except ValueError:
        return None
    return number if number >= 0 else None


@dataclass(frozen=True)
class DerivedFields:
    distance_m: float | None
    calories_kcal: float | None
    active_min: int
    effort_points: int


def derive_activity(
    steps: int | None, duration_min: int, height_metres: float, weight_kg: float | None
) -> DerivedFields:
    height_cm = height_metres * 100.0
    distance_m = (
        metrics.distance(float(steps), height_cm).value
        if steps is not None and height_cm > 0
        else None
    )
    calories_kcal = (
        metrics.calories(float(steps), weight_kg, height_cm).value
        if steps is not None and height_cm > 0 and weight_kg is not None and weight_kg > 0
        else None
    )
    effort = metrics.effort_from_total(float(steps)).value if steps is not None else duration_min
    return DerivedFields(
        distance_m=distance_m, calories_kcal=calories_kcal,
        active_min=duration_min, effort_points=effort,
    )


def activity_window(now_ms: int, duration_min: int) -> tuple[int, int]:
    return (now_ms - duration_min * 60_000, now_ms)


def _json_int(value: int | None) -> str:
    return str(value) if value is not None else "null"


def _json_double(value: float | None) -> str:
    return f"{value:.4f}" if value is not None else "null"


def activity_payload(
    source_id: str, type_: str, started_at: int, ended_at: int,
    distance_m: float | None, calories_kcal: float | None,
    effort_points: int | None, active_min: int | None, steps: int | None,
) -> str:
    return (
        '{"source_id":"' + _escape(source_id) + '","type":"' + _escape(type_) + '",'
        + '"started_at":' + str(started_at) + ',"ended_at":' + str(ended_at) + ","
        + '"distance_m":' + _json_double(distance_m) + ","
        + '"calories_kcal":' + _json_double(calories_kcal) + ","
        + '"effort_points":' + _json_int(effort_points) + ","
        + '"active_min":' + _json_int(active_min) + ","
        + '"steps":' + _json_int(steps) + "}"
    )


_ACTIVITY_PAYLOAD_RE = re.compile(
    r'\{"source_id":"([^"]*)","type":"([^"]*)",'
    r'"started_at":(-?[0-9]+),"ended_at":(-?[0-9]+),'
    r'"distance_m":(null|[0-9]+(?:\.[0-9]+)?),'
    r'"calories_kcal":(null|[0-9]+(?:\.[0-9]+)?),'
    r'"effort_points":(null|[0-9]+),'
    r'"active_min":(null|[0-9]+),'
    r'"steps":(null|[0-9]+)\}'
)


@dataclass(frozen=True)
class QueuedActivity:
    source_id: str
    type: str
    started_at: int
    ended_at: int
    distance_m: float | None
    calories_kcal: float | None
    effort_points: int | None
    active_min: int | None
    steps: int | None


def parse_activity(payload_json: str) -> QueuedActivity | None:
    match = _ACTIVITY_PAYLOAD_RE.fullmatch(payload_json)
    if match is None:
        return None
    try:
        started = int(match.group(3))
        ended = int(match.group(4))
    except ValueError:
        return None
    return QueuedActivity(
        source_id=match.group(1), type=match.group(2),
        started_at=started, ended_at=ended,
        distance_m=_opt_float(match.group(5)), calories_kcal=_opt_float(match.group(6)),
        effort_points=_opt_int(match.group(7)), active_min=_opt_int(match.group(8)),
        steps=_opt_int(match.group(9)),
    )


def _opt_float(raw: str) -> float | None:
    return float(raw) if raw != "null" else None


def _opt_int(raw: str) -> int | None:
    return int(raw) if raw != "null" else None


def daily_payload(metric: DailyMetric) -> str:
    return (
        '{"source_id":"' + _escape(metric.source_id) + '","date":"' + _escape(metric.date) + '",'
        + '"steps":' + _json_int(metric.steps) + ","
        + '"distance_m":' + _json_double(metric.distance_m) + ","
        + '"active_min":' + _json_int(metric.active_min) + ","
        + '"effort_points":' + _json_int(metric.effort_points) + ","
        + '"calories_kcal":' + _json_double(metric.calories_kcal) + ","
        + '"derived_mask":' + str(metric.derived_mask) + "}"
    )


_DATE_FIELD_RE = re.compile(r'"date":"([^"]+)"')

_DAILY_PAYLOAD_RE = re.compile(
    r'\{"source_id":"([^"]*)","date":"([^"]+)",'
    r'"steps":(null|[0-9]+),'
    r'"distance_m":(null|[0-9]+(?:\.[0-9]+)?),'
    r'"active_min":(null|[0-9]+),'
    r'"effort_points":(null|[0-9]+),'
    r'"calories_kcal":(null|[0-9]+(?:\.[0-9]+)?),'
    r'"derived_mask":([0-9]+)\}'
)


@dataclass(frozen=True)
class QueuedDaily:
    source_id: str
    date: str
    steps: int | None
    distance_m: float | None
    active_min: int | None
    effort_points: int | None
    calories_kcal: float | None
    derived_mask: int


def daily_date_of(payload_json: str) -> str | None:
    match = _DATE_FIELD_RE.search(payload_json)
    return match.group(1) if match else None


def parse_daily(payload_json: str) -> QueuedDaily | None:
    match = _DAILY_PAYLOAD_RE.fullmatch(payload_json)
    if match is None:
        return None
    try:
        derived = int(match.group(8))
    except ValueError:
        return None
    return QueuedDaily(
        source_id=match.group(1), date=match.group(2),
        steps=_opt_int(match.group(3)), distance_m=_opt_float(match.group(4)),
        active_min=_opt_int(match.group(5)), effort_points=_opt_int(match.group(6)),
        calories_kcal=_opt_float(match.group(7)), derived_mask=derived,
    )


def enqueue_daily(db: Database, metric: DailyMetric, now_ms: int) -> int:
    for row in db.pending_next_daily_ops():
        if daily_date_of(row.payload_json) == metric.date:
            db.pending_delete(row.id)
    return db.pending_insert(
        PendingWrite(kind=KIND_DAILY, payload_json=daily_payload(metric), created_at=now_ms)
    )


def prefer_phone(rows: list[DailyMetric]) -> list[DailyMetric]:
    by_date: dict[str, DailyMetric] = {}
    for row in rows:
        existing = by_date.get(row.date)
        if existing is None or (row.source_id == SOURCE_PHONE and existing.source_id != SOURCE_PHONE):
            by_date[row.date] = row
    return list(by_date.values())


class WeightRepository:
    """Offline-first weight writes: Room first, queue in the same step."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add_weight(self, sample: WeightSample, payload: str) -> int:
        self._db.upsert_weight(sample)
        return self._db.pending_insert(
            PendingWrite(kind=KIND_WEIGHT, payload_json=payload, created_at=sample.measured_at)
        )

    def edit(self, sample: WeightSample) -> None:
        self._db.upsert_weight(sample)
        outstanding = self._outstanding(sample.source_id, sample.measured_at)
        insert_still_queued = any(row.kind == KIND_WEIGHT for row in outstanding)
        for row in outstanding:
            self._db.pending_delete(row.id)
        kind = KIND_WEIGHT if insert_still_queued else KIND_WEIGHT_UPDATE
        self._db.pending_insert(
            PendingWrite(
                kind=kind,
                payload_json=weight_payload(sample.source_id, sample.measured_at, sample.weight_kg),
                created_at=sample.measured_at,
            )
        )

    def delete(self, id_: int) -> None:
        sample = self._db.find_weight_by_id(id_)
        if sample is None:
            return
        outstanding = self._outstanding(sample.source_id, sample.measured_at)
        insert_still_queued = any(row.kind == KIND_WEIGHT for row in outstanding)
        for row in outstanding:
            self._db.pending_delete(row.id)
        if not insert_still_queued:
            self._db.pending_insert(
                PendingWrite(
                    kind=KIND_WEIGHT_DELETE,
                    payload_json=weight_payload(
                        sample.source_id, sample.measured_at, sample.weight_kg
                    ),
                    created_at=sample.measured_at,
                )
            )
        self._db.delete_weight(id_)

    def _outstanding(self, source_id: str, measured_at: int) -> list[PendingWrite]:
        prefix = weight_key_prefix(source_id, measured_at)
        return [row for row in self._db.pending_next_weight_ops() if row.payload_json.startswith(prefix)]


def activity_to_row(source_id: str, queued: QueuedActivity, batch_id: int | None = None) -> Activity:
    return Activity(
        source_id=source_id, type=queued.type,
        started_at=queued.started_at, ended_at=queued.ended_at,
        distance_m=queued.distance_m, calories_kcal=queued.calories_kcal,
        effort_points=queued.effort_points, active_min=queued.active_min,
        steps=queued.steps, batch_id=batch_id,
    )


class ActivityRepository:
    """Offline-first manual activity writes: Room first, queue in the same step."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def log_activity(
        self, type_: str, started_at: int, ended_at: int,
        distance_m: float | None, calories_kcal: float | None,
        effort_points: int | None, active_min: int | None,
        steps: int | None, source_id: str = SOURCE_MANUAL,
    ) -> int:
        row = Activity(
            source_id=source_id, type=type_,
            started_at=started_at, ended_at=ended_at,
            distance_m=distance_m, calories_kcal=calories_kcal,
            effort_points=effort_points, active_min=active_min, steps=steps,
        )
        self._db.insert_activity(row)
        return self._db.pending_insert(
            PendingWrite(
                kind=KIND_ACTIVITY,
                payload_json=activity_payload(
                    source_id, type_, started_at, ended_at,
                    distance_m, calories_kcal, effort_points, active_min, steps,
                ),
                created_at=ended_at,
            )
        )
