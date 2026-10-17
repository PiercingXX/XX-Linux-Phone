"""SQLite replica mirroring the Room entities 1:1.

Tables and column names match
ref/xx-vitals/app/schemas/.../VitalsDatabase/1.json so the sync payload
builders port verbatim. Connection per call, WAL mode. `sync_state` holds a
single row id=1; its `hc_change_token` column carries the raw server
`ingest_batch.started_at` ISO cursor (never millis).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from xx_vitals.paths import data_dir, ensure_parent_for

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_metric (
  source_id TEXT NOT NULL,
  date TEXT NOT NULL,
  steps INTEGER,
  distance_m REAL,
  active_min INTEGER,
  effort_points INTEGER,
  calories_kcal REAL,
  hr_avg INTEGER,
  hr_min INTEGER,
  hr_max INTEGER,
  hr_sample_count INTEGER,
  weight_avg_kg REAL,
  weight_min_kg REAL,
  weight_max_kg REAL,
  derived_mask INTEGER NOT NULL,
  batch_id INTEGER,
  PRIMARY KEY (source_id, date)
);
CREATE INDEX IF NOT EXISTS index_daily_metric_date ON daily_metric (date);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
  source_id TEXT NOT NULL,
  type TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  ended_at INTEGER NOT NULL,
  distance_m REAL,
  calories_kcal REAL,
  effort_points INTEGER,
  active_min INTEGER,
  hr_avg INTEGER,
  hr_max INTEGER,
  steps INTEGER,
  track_geojson TEXT,
  batch_id INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS
  index_activity_source_id_started_at_ended_at_type
  ON activity (source_id, started_at, ended_at, type);
CREATE TABLE IF NOT EXISTS weight_sample (
  id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
  source_id TEXT NOT NULL,
  measured_at INTEGER NOT NULL,
  weight_kg REAL NOT NULL,
  batch_id INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS
  index_weight_sample_source_id_measured_at
  ON weight_sample (source_id, measured_at);
CREATE TABLE IF NOT EXISTS pending_write (
  id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS sync_state (
  id INTEGER NOT NULL PRIMARY KEY,
  last_pull_at INTEGER,
  last_push_at INTEGER,
  hc_change_token TEXT
);
"""


@dataclass
class DailyMetric:
    source_id: str
    date: str
    steps: int | None = None
    distance_m: float | None = None
    active_min: int | None = None
    effort_points: int | None = None
    calories_kcal: float | None = None
    hr_avg: int | None = None
    hr_min: int | None = None
    hr_max: int | None = None
    hr_sample_count: int | None = None
    weight_avg_kg: float | None = None
    weight_min_kg: float | None = None
    weight_max_kg: float | None = None
    derived_mask: int = 0
    batch_id: int | None = None


@dataclass
class Activity:
    source_id: str
    type: str
    started_at: int
    ended_at: int
    distance_m: float | None = None
    calories_kcal: float | None = None
    effort_points: int | None = None
    active_min: int | None = None
    hr_avg: int | None = None
    hr_max: int | None = None
    steps: int | None = None
    track_geojson: str | None = None
    batch_id: int | None = None
    id: int = 0


@dataclass
class WeightSample:
    source_id: str
    measured_at: int
    weight_kg: float
    batch_id: int | None = None
    id: int = 0


@dataclass
class PendingWrite:
    kind: str
    payload_json: str
    created_at: int
    attempts: int = 0
    last_error: str | None = None
    id: int = 0


@dataclass
class SyncState:
    id: int = 1
    last_pull_at: int | None = None
    last_push_at: int | None = None
    last_pull_iso: str | None = None


def default_db_path() -> Path:
    return data_dir() / "vitals.db"


class Database:
    """SQLite replica. One function per DAO method; connection per call."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = str(path) if path is not None else str(default_db_path())
        self._memory: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory = sqlite3.connect(":memory:")
            self._memory.row_factory = sqlite3.Row
            self._memory.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        if self._memory is not None:
            return self._memory
        ensure_parent_for(self.path)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _close(self, conn: sqlite3.Connection) -> None:
        if conn is not self._memory:
            conn.close()

    def init(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()
        finally:
            self._close(conn)

    # -- daily_metric ----------------------------------------------------

    def upsert_daily(self, metric: DailyMetric) -> None:
        conn = self._connect()
        try:
            _upsert_daily_conn(conn, metric)
            conn.commit()
        finally:
            self._close(conn)

    def upsert_dailies(self, metrics: list[DailyMetric]) -> None:
        for metric in metrics:
            self.upsert_daily(metric)

    upsert_all = upsert_dailies

    def import_batch(
        self,
        dailies: list[DailyMetric] | tuple = (),
        weights: list[tuple] | tuple = (),
        activities: list[tuple] | tuple = (),
        now_ms: int = 0,
    ) -> None:
        """Write one import in a single SQLite transaction.

        `weights`/`activities` are (row, queue-payload-json, queue-created-at)
        triples; `dailies` carry their queue rows implicitly (one pending
        `daily` op per date with `created_at=now_ms`, replacing any queued
        op for that date — the same rule as `enqueue_daily`). Daily queue
        payloads are built here so tables and queue commit atomically.
        Rolls back whole on error.
        """
        from xx_vitals.store import entries as entries_module

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for metric in dailies:
                _upsert_daily_conn(conn, metric)
                conn.execute(
                    "DELETE FROM pending_write WHERE kind = 'daily'"
                    " AND instr(payload_json, ?) > 0",
                    ('"date":"' + metric.date + '"',),
                )
                conn.execute(
                    "INSERT INTO pending_write (kind, payload_json, created_at,"
                    " attempts, last_error) VALUES (?,?,?,?,?)",
                    ("daily", entries_module.daily_payload(metric),
                     now_ms, 0, None),
                )
            for sample, payload, created_at in weights:
                _upsert_weight_conn(conn, sample)
                conn.execute(
                    "INSERT INTO pending_write (kind, payload_json, created_at,"
                    " attempts, last_error) VALUES (?,?,?,?,?)",
                    ("weight", payload, created_at, 0, None),
                )
            for activity, payload, created_at in activities:
                _insert_activity_conn(conn, activity)
                conn.execute(
                    "INSERT INTO pending_write (kind, payload_json, created_at,"
                    " attempts, last_error) VALUES (?,?,?,?,?)",
                    ("activity", payload, created_at, 0, None),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            self._close(conn)

    def get_daily(self, source_id: str, day: str) -> DailyMetric | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM daily_metric WHERE source_id = ? AND date = ?",
                (source_id, day),
            ).fetchone()
        finally:
            self._close(conn)
        return _daily_from_row(row) if row is not None else None

    def count_dailies(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT COUNT(*) FROM daily_metric").fetchone()
            return int(row[0])
        finally:
            self._close(conn)

    def list_dailies(self, limit: int = 365) -> list[DailyMetric]:
        """Newest-first daily rows for the Home rings and Browse charts."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM daily_metric ORDER BY date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            self._close(conn)
        return [_daily_from_row(row) for row in rows]

    # -- activity --------------------------------------------------------

    def insert_activity(self, activity: Activity) -> int:
        conn = self._connect()
        try:
            row_id = _insert_activity_conn(conn, activity)
            conn.commit()
            return row_id
        finally:
            self._close(conn)

    def insert_all(self, activities: list[Activity]) -> None:
        for activity in activities:
            self.insert_activity(activity)

    insert_activities = insert_all

    def find_activity_by_natural_key(
        self, source_id: str, started_at: int, ended_at: int, type_: str
    ) -> Activity | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM activity WHERE source_id = ? AND started_at = ?"
                " AND ended_at = ? AND type = ? LIMIT 1",
                (source_id, started_at, ended_at, type_),
            ).fetchone()
        finally:
            self._close(conn)
        return _activity_from_row(row) if row is not None else None

    def count_activities(self) -> int:
        conn = self._connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0])
        finally:
            self._close(conn)

    def list_activities(self, limit: int = 200) -> list[Activity]:
        """Newest-first activity rows for the Journal list."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM activity ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            self._close(conn)
        return [_activity_from_row(row) for row in rows]

    # -- weight_sample ---------------------------------------------------

    def upsert_weight(self, sample: WeightSample) -> int:
        conn = self._connect()
        try:
            row_id = _upsert_weight_conn(conn, sample)
            conn.commit()
            return row_id
        finally:
            self._close(conn)

    upsert = upsert_weight

    def find_weight_by_natural_key(self, source_id: str, measured_at: int) -> WeightSample | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM weight_sample WHERE source_id = ? AND measured_at = ? LIMIT 1",
                (source_id, measured_at),
            ).fetchone()
        finally:
            self._close(conn)
        return _weight_from_row(row) if row is not None else None

    def find_weight_by_id(self, id_: int) -> WeightSample | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM weight_sample WHERE id = ? LIMIT 1", (id_,)
            ).fetchone()
        finally:
            self._close(conn)
        return _weight_from_row(row) if row is not None else None

    def delete_weight(self, id_: int) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM weight_sample WHERE id = ?", (id_,))
            conn.commit()
        finally:
            self._close(conn)

    delete = delete_weight

    def latest_weight(self) -> WeightSample | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM weight_sample ORDER BY measured_at DESC LIMIT 1"
            ).fetchone()
        finally:
            self._close(conn)
        return _weight_from_row(row) if row is not None else None

    def count_weights(self) -> int:
        conn = self._connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM weight_sample").fetchone()[0])
        finally:
            self._close(conn)

    def list_weights(self, limit: int = 60) -> list[WeightSample]:
        """Newest-first weigh-ins for the Home card and Browse charts."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM weight_sample ORDER BY measured_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            self._close(conn)
        return [_weight_from_row(row) for row in rows]

    # -- pending_write ---------------------------------------------------

    def pending_insert(self, write: PendingWrite) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO pending_write (kind, payload_json, created_at, attempts, last_error)"
                " VALUES (?,?,?,?,?)",
                (write.kind, write.payload_json, write.created_at, write.attempts, write.last_error),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            self._close(conn)

    insert = pending_insert

    def pending_next(self, limit: int, dead_letter_attempts: int) -> list[PendingWrite]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM pending_write WHERE attempts < ?"
                " ORDER BY created_at ASC, id ASC LIMIT ?",
                (dead_letter_attempts, limit),
            ).fetchall()
        finally:
            self._close(conn)
        return [_pending_from_row(r) for r in rows]

    next = pending_next

    def pending_delete(self, id_: int) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM pending_write WHERE id = ?", (id_,))
            conn.commit()
        finally:
            self._close(conn)

    delete_pending = pending_delete

    def pending_record_failure(self, id_: int, attempts: int, last_error: str | None) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE pending_write SET attempts = ?, last_error = ? WHERE id = ?",
                (attempts, last_error, id_),
            )
            conn.commit()
        finally:
            self._close(conn)

    record_failure = pending_record_failure

    def pending_next_weight_ops(self) -> list[PendingWrite]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM pending_write WHERE kind IN ('weight','weight.update','weight.delete')"
                " ORDER BY created_at ASC, id ASC"
            ).fetchall()
        finally:
            self._close(conn)
        return [_pending_from_row(r) for r in rows]

    next_weight_ops = pending_next_weight_ops

    def pending_next_daily_ops(self) -> list[PendingWrite]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM pending_write WHERE kind = 'daily'"
                " ORDER BY created_at ASC, id ASC"
            ).fetchall()
        finally:
            self._close(conn)
        return [_pending_from_row(r) for r in rows]

    next_daily_ops = pending_next_daily_ops

    def pending_count(self) -> int:
        conn = self._connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM pending_write").fetchone()[0])
        finally:
            self._close(conn)

    count = pending_count

    # -- sync_state ------------------------------------------------------

    def sync_get(self) -> SyncState | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM sync_state WHERE id = 1 LIMIT 1").fetchone()
        finally:
            self._close(conn)
        return _sync_from_row(row) if row is not None else None

    get = sync_get

    def sync_upsert(self, state: SyncState) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO sync_state (id, last_pull_at, last_push_at, hc_change_token)"
                " VALUES (?,?,?,?)",
                (state.id, state.last_pull_at, state.last_push_at, state.last_pull_iso),
            )
            conn.commit()
        finally:
            self._close(conn)

    upsert_sync = sync_upsert


def _upsert_daily_conn(conn: sqlite3.Connection, metric: DailyMetric) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO daily_metric "
        "(source_id, date, steps, distance_m, active_min, effort_points,"
        " calories_kcal, hr_avg, hr_min, hr_max, hr_sample_count,"
        " weight_avg_kg, weight_min_kg, weight_max_kg, derived_mask, batch_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            metric.source_id, metric.date, metric.steps, metric.distance_m,
            metric.active_min, metric.effort_points, metric.calories_kcal,
            metric.hr_avg, metric.hr_min, metric.hr_max, metric.hr_sample_count,
            metric.weight_avg_kg, metric.weight_min_kg, metric.weight_max_kg,
            metric.derived_mask, metric.batch_id,
        ),
    )


def _insert_activity_conn(conn: sqlite3.Connection, activity: Activity) -> int:
    existing = conn.execute(
        "SELECT id FROM activity WHERE source_id = ? AND started_at = ?"
        " AND ended_at = ? AND type = ? LIMIT 1",
        (activity.source_id, activity.started_at, activity.ended_at, activity.type),
    ).fetchone()
    if existing is None:
        cur = conn.execute(
            "INSERT INTO activity (source_id, type, started_at, ended_at,"
            " distance_m, calories_kcal, effort_points, active_min,"
            " hr_avg, hr_max, steps, track_geojson, batch_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _activity_columns(activity),
        )
        return int(cur.lastrowid)
    conn.execute(
        "UPDATE activity SET distance_m = ?, calories_kcal = ?,"
        " effort_points = ?, active_min = ?, hr_avg = ?, hr_max = ?,"
        " steps = ?, track_geojson = ?, batch_id = ? WHERE id = ?",
        (
            activity.distance_m, activity.calories_kcal, activity.effort_points,
            activity.active_min, activity.hr_avg, activity.hr_max,
            activity.steps, activity.track_geojson, activity.batch_id,
            existing["id"],
        ),
    )
    return int(existing["id"])


def _upsert_weight_conn(conn: sqlite3.Connection, sample: WeightSample) -> int:
    existing = conn.execute(
        "SELECT id FROM weight_sample WHERE source_id = ? AND measured_at = ? LIMIT 1",
        (sample.source_id, sample.measured_at),
    ).fetchone()
    if existing is None:
        cur = conn.execute(
            "INSERT INTO weight_sample (source_id, measured_at, weight_kg, batch_id)"
            " VALUES (?,?,?,?)",
            (sample.source_id, sample.measured_at, sample.weight_kg, sample.batch_id),
        )
        return int(cur.lastrowid)
    conn.execute(
        "UPDATE weight_sample SET weight_kg = ?, batch_id = ? WHERE id = ?",
        (sample.weight_kg, sample.batch_id, existing["id"]),
    )
    return int(existing["id"])


def _daily_from_row(row: sqlite3.Row) -> DailyMetric:
    return DailyMetric(
        source_id=row["source_id"], date=row["date"], steps=row["steps"],
        distance_m=row["distance_m"], active_min=row["active_min"],
        effort_points=row["effort_points"], calories_kcal=row["calories_kcal"],
        hr_avg=row["hr_avg"], hr_min=row["hr_min"], hr_max=row["hr_max"],
        hr_sample_count=row["hr_sample_count"], weight_avg_kg=row["weight_avg_kg"],
        weight_min_kg=row["weight_min_kg"], weight_max_kg=row["weight_max_kg"],
        derived_mask=row["derived_mask"], batch_id=row["batch_id"],
    )


def _activity_from_row(row: sqlite3.Row) -> Activity:
    return Activity(
        id=row["id"], source_id=row["source_id"], type=row["type"],
        started_at=row["started_at"], ended_at=row["ended_at"],
        distance_m=row["distance_m"], calories_kcal=row["calories_kcal"],
        effort_points=row["effort_points"], active_min=row["active_min"],
        hr_avg=row["hr_avg"], hr_max=row["hr_max"], steps=row["steps"],
        track_geojson=row["track_geojson"], batch_id=row["batch_id"],
    )


def _activity_columns(activity: Activity) -> tuple:
    return (
        activity.source_id, activity.type, activity.started_at, activity.ended_at,
        activity.distance_m, activity.calories_kcal, activity.effort_points,
        activity.active_min, activity.hr_avg, activity.hr_max, activity.steps,
        activity.track_geojson, activity.batch_id,
    )


def _weight_from_row(row: sqlite3.Row) -> WeightSample:
    return WeightSample(
        id=row["id"], source_id=row["source_id"], measured_at=row["measured_at"],
        weight_kg=row["weight_kg"], batch_id=row["batch_id"],
    )


def _pending_from_row(row: sqlite3.Row) -> PendingWrite:
    return PendingWrite(
        id=row["id"], kind=row["kind"], payload_json=row["payload_json"],
        created_at=row["created_at"], attempts=row["attempts"], last_error=row["last_error"],
    )


def _sync_from_row(row: sqlite3.Row) -> SyncState:
    return SyncState(
        id=row["id"], last_pull_at=row["last_pull_at"],
        last_push_at=row["last_push_at"], last_pull_iso=row["hc_change_token"],
    )
