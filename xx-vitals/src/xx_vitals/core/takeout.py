"""Takeout import parsing: Daily activity metrics.csv, TCX, weight JSON.

Header-driven like the server importer (`ref/xx-vitals/server/importer`),
so unknown columns are ignored and a malformed row is skipped and counted,
never fatal. TCX handling ports `ingest.parse_tcx` (lenient, namespace
agnostic). Storage stays with the manual-entry repositories (never raw
SQL); see ui/profile import_takeout.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.etree import ElementTree

COLUMN_MAP = {
    "Start time": "start_time",
    "Move Minutes count": "active_min",
    "Calories (kcal)": "calories_kcal",
    "Distance (m)": "distance_m",
    "Heart Points": "effort_points",
    "Average heart rate (bpm)": "hr_avg",
    "Max heart rate (bpm)": "hr_max",
    "Min heart rate (bpm)": "hr_min",
    "Step count": "steps",
    "Average weight (kg)": "weight_avg_kg",
    "Max weight (kg)": "weight_max_kg",
    "Min weight (kg)": "weight_min_kg",
}

_NUMERIC_FIELDS = (
    "active_min", "calories_kcal", "distance_m", "effort_points",
    "hr_avg", "hr_max", "hr_min", "steps",
    "weight_avg_kg", "weight_max_kg", "weight_min_kg",
)

SOURCE_TAKEOUT = "takeout"


@dataclass
class TakeoutDay:
    date: str
    steps: int | None = None
    distance_m: float | None = None
    active_min: int | None = None
    effort_points: int | None = None
    calories_kcal: float | None = None
    hr_avg: int | None = None
    hr_min: int | None = None
    hr_max: int | None = None

    def has_metrics(self) -> bool:
        return any(
            value is not None for value in (
                self.steps, self.distance_m, self.active_min,
                self.effort_points, self.calories_kcal,
                self.hr_avg, self.hr_min, self.hr_max,
            )
        )


@dataclass
class TakeoutWeight:
    measured_at_ms: int
    weight_kg: float


@dataclass
class TcxActivity:
    type: str
    started_at_ms: int
    ended_at_ms: int
    distance_m: float | None = None
    calories_kcal: float | None = None
    hr_avg: int | None = None
    hr_max: int | None = None
    track_geojson: str | None = None


@dataclass
class TakeoutFile:
    days: list[TakeoutDay] = field(default_factory=list)
    weights: list[TakeoutWeight] = field(default_factory=list)
    activities: list[TcxActivity] = field(default_factory=list)
    rows_skipped: int = 0


def detect_kind(filename: str, text: str) -> str | None:
    lowered = filename.lower()
    if lowered.endswith(".zip"):
        return "takeout_zip"
    if lowered.endswith(".tcx") or "<TrainingCenterDatabase" in text[:2000]:
        return "tcx"
    if lowered.endswith(".csv"):
        return "daily_csv"
    if lowered.endswith(".json"):
        if "weight" in lowered:
            try:
                doc = json.loads(text)
            except ValueError:
                return None
            if isinstance(doc, dict) and isinstance(doc.get("point"), list):
                return "weight_json"
            return None
        try:
            doc = json.loads(text)
        except ValueError:
            return None
        if isinstance(doc, dict) and isinstance(doc.get("records"), list):
            return "healthconnect_json"
        return None
    return None


def parse_daily_csv(text: str) -> TakeoutFile:
    return parse_daily_csv_stream(text.splitlines())


def parse_daily_csv_stream(lines: Iterable[str], on_progress=None) -> TakeoutFile:
    """Header-driven parse over a line iterable; constant memory for big files."""
    outcome = TakeoutFile()
    iterator = iter(lines)
    try:
        header_line = next(iterator)
    except StopIteration:
        return outcome
    header = [cell.strip() for cell in header_line.lstrip("﻿").split(",")]
    positions = {
        field_name: index
        for index, name in enumerate(header)
        if (field_name := COLUMN_MAP.get(name)) is not None
    }
    if not positions:
        return outcome
    seen = 0
    for raw_line in iterator:
        if not raw_line.strip():
            continue
        try:
            cells = next(iter(csv.reader([raw_line])))
        except (csv.Error, StopIteration):
            outcome.rows_skipped += 1
            continue
        day = _parse_csv_row(cells, positions)
        if day is None:
            outcome.rows_skipped += 1
        else:
            outcome.days.append(day)
        seen += 1
        if on_progress is not None and seen % 2000 == 0:
            on_progress(seen, None)
    if on_progress is not None:
        on_progress(seen, None)
    return outcome


def _parse_csv_row(cells: list[str], positions: dict[str, int]) -> TakeoutDay | None:
    values: dict[str, int | float | None] = {}
    for field_name, index in positions.items():
        if index >= len(cells):
            continue
        cell = cells[index].strip()
        if field_name == "start_time":
            continue
        if cell == "":
            continue
        try:
            values[field_name] = float(cell) if "." in cell else int(cell)
        except ValueError:
            return None
    date = _parse_date(_cell(cells, positions.get("start_time", -1)))
    if date is None:
        return None
    day = TakeoutDay(
        date=date,
        steps=_as_int(values.get("steps")),
        distance_m=_as_float(values.get("distance_m")),
        active_min=_as_int(values.get("active_min")),
        effort_points=_as_int(values.get("effort_points")),
        calories_kcal=_as_float(values.get("calories_kcal")),
        hr_avg=_as_int(values.get("hr_avg")),
        hr_min=_as_int(values.get("hr_min")),
        hr_max=_as_int(values.get("hr_max")),
    )
    if not day.has_metrics():
        return None
    return day


def _cell(cells: list[str], index: int) -> str:
    if index < 0 or index >= len(cells):
        return ""
    return cells[index].strip()


def _parse_date(cell: str) -> str | None:
    if not cell:
        return None
    head = cell[:10]
    if len(head) != 10 or head[4] != "-" or head[7] != "-":
        return None
    try:
        year, month, day = int(head[:4]), int(head[5:7]), int(head[8:10])
    except ValueError:
        return None
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _as_int(value: int | float | None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _as_float(value: int | float | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def parse_weight_json(text: str) -> TakeoutFile:
    outcome = TakeoutFile()
    try:
        doc = json.loads(text)
    except ValueError:
        return outcome
    points = doc.get("point", []) if isinstance(doc, dict) else []
    if not isinstance(points, list):
        return outcome
    for point in points:
        if not isinstance(point, dict):
            outcome.rows_skipped += 1
            continue
        measured_at = _nanos_to_ms(point.get("startTimeNanos"))
        weight_kg = _point_weight(point.get("value"))
        if measured_at is None or weight_kg is None:
            outcome.rows_skipped += 1
            continue
        outcome.weights.append(TakeoutWeight(measured_at_ms=measured_at, weight_kg=weight_kg))
    return outcome


def _nanos_to_ms(raw) -> int | None:
    try:
        nanos = int(str(raw))
    except (ValueError, TypeError):
        return None
    if nanos <= 0:
        return None
    return nanos // 1_000_000


def _point_weight(values) -> float | None:
    if not isinstance(values, list) or not values:
        return None
    first = values[0]
    if not isinstance(first, dict):
        return None
    try:
        weight = float(first.get("fpVal"))
    except (ValueError, TypeError):
        return None
    return weight


_TCX_SPORT_MAP = {
    "running": "run",
    "walking": "walk",
    "biking": "cycle",
    "cycling": "cycle",
}


def parse_tcx(xml_text: str) -> TakeoutFile:
    """Parse a TCX document into activity rows. Ports ingest.parse_tcx.

    Namespace-agnostic (Garmin's namespace is stripped per tag); a
    malformed activity is skipped, never fatal. Times become epoch
    milliseconds; skipped rows are counted.
    """
    outcome = TakeoutFile()
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return outcome
    for activity_el in root.iter():
        if _local_name(activity_el.tag) != "Activity":
            continue
        parsed = _parse_tcx_activity(activity_el)
        if parsed is None:
            outcome.rows_skipped += 1
        else:
            outcome.activities.append(parsed)
    return outcome


def _parse_tcx_activity(activity_el: ElementTree.Element) -> TcxActivity | None:
    sport = (activity_el.get("Sport") or "").strip()
    if not sport:
        return None
    started_at: datetime | None = None
    total_seconds = 0.0
    distance_m = 0.0
    calories = 0.0
    hr_sum = 0.0
    hr_count = 0
    hr_max: int | None = None
    track: list[list[float]] = []
    for child in activity_el:
        name = _local_name(child.tag)
        if name == "Id":
            started_at = _parse_iso_datetime(_element_text(child))
        elif name == "Lap":
            seconds, distance, burned, avg, peak = _parse_tcx_lap(child)
            total_seconds += seconds
            distance_m += distance
            calories += burned
            if avg is not None:
                hr_sum += avg
                hr_count += 1
            if peak is not None and (hr_max is None or peak > hr_max):
                hr_max = peak
            _extend_track(child, track)
    if started_at is None or total_seconds <= 0:
        return None
    started_ms = int(started_at.timestamp() * 1000)
    ended_ms = started_ms + int(total_seconds * 1000)
    if ended_ms <= started_ms:
        return None
    return TcxActivity(
        type=_TCX_SPORT_MAP.get(sport.lower(), sport.lower()),
        started_at_ms=started_ms,
        ended_at_ms=ended_ms,
        distance_m=distance_m if distance_m > 0 else None,
        calories_kcal=calories if calories > 0 else None,
        hr_avg=round(hr_sum / hr_count) if hr_count else None,
        hr_max=hr_max,
        track_geojson=_line_string(track) if len(track) >= 2 else None,
    )


def _parse_tcx_lap(lap_el: ElementTree.Element) -> tuple:
    seconds = distance = calories = 0.0
    hr_avg: float | None = None
    hr_max: int | None = None
    for child in lap_el:
        name = _local_name(child.tag)
        if name == "TotalTimeSeconds":
            seconds = _to_float(_element_text(child)) or 0.0
        elif name == "DistanceMeters":
            distance = _to_float(_element_text(child)) or 0.0
        elif name == "Calories":
            calories = _to_float(_element_text(child)) or 0.0
        elif name == "AverageHeartRateBpm":
            hr_avg = _to_float(_value_text(child))
        elif name == "MaximumHeartRateBpm":
            hr_max = _to_int(_value_text(child))
    return seconds, distance, calories, hr_avg, hr_max


def _extend_track(lap_el: ElementTree.Element, track: list[list[float]]) -> None:
    for child in lap_el:
        if _local_name(child.tag) != "Track":
            continue
        for point in child:
            if _local_name(point.tag) != "Trackpoint":
                continue
            position = _trackpoint_position(point)
            if position is not None:
                track.append(position)


def _trackpoint_position(point: ElementTree.Element) -> list[float] | None:
    for child in point:
        if _local_name(child.tag) == "Position":
            lat = lon = None
            for coord in child:
                name = _local_name(coord.tag)
                if name == "LatitudeDegrees":
                    lat = _to_float(_element_text(coord))
                elif name == "LongitudeDegrees":
                    lon = _to_float(_element_text(coord))
            if lat is not None and lon is not None:
                return [lon, lat]
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _element_text(element: ElementTree.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def _value_text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    for child in element:
        if _local_name(child.tag) == "Value":
            return _element_text(child)
    return ""


def _to_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _line_string(track: list[list[float]]) -> str:
    coords = "[" + ",".join(f"[{lon},{lat}]" for lon, lat in track) + "]"
    return '{"type":"LineString","coordinates":' + coords + "}"


def parse_healthconnect_json(text: str) -> TakeoutFile:
    """Parse a Health Connect weight export: {"records": [...]}.

    Mirrors the server's HealthConnectSync payload (WeightEntry rows:
    measured_at as ISO datetime or epoch milliseconds, weight_kg).
    Bad records are skipped, never fatal.
    """
    outcome = TakeoutFile()
    try:
        doc = json.loads(text)
    except ValueError:
        return outcome
    records = doc.get("records", []) if isinstance(doc, dict) else []
    if not isinstance(records, list):
        return outcome
    for record in records:
        if not isinstance(record, dict):
            outcome.rows_skipped += 1
            continue
        measured_at = _hc_measured_at_ms(record.get("measured_at"))
        try:
            weight_kg = float(record.get("weight_kg"))
        except (ValueError, TypeError):
            weight_kg = None
        if measured_at is None or weight_kg is None:
            outcome.rows_skipped += 1
            continue
        outcome.weights.append(TakeoutWeight(measured_at_ms=measured_at, weight_kg=weight_kg))
    return outcome


def _hc_measured_at_ms(raw) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        moment = int(raw)
        return moment if 0 < moment < 100_000_000_000_000 else None
    if isinstance(raw, str):
        parsed = _parse_iso_datetime(raw.strip())
        if parsed is None:
            return None
        return int(parsed.timestamp() * 1000)
    return None


ZIP_DAILY_CSV_BASENAME = "daily activity metrics.csv"
ZIP_ACTIVITIES_PREFIX = "takeout/fit/activities/"
ZIP_WEIGHT_PREFIX = "raw_com.google.weight_"


def parse_takeout_zip(zip_path, on_progress=None) -> TakeoutFile:
    """Parse a Takeout archive member by member (CSV streamed line by line).

    Matches members the way the server importer does: the daily-metrics
    CSV by basename, `Activities/*.tcx`, `raw_com.google.weight_*.json`
    by filename prefix. Unknown members are ignored.
    """
    import io
    import zipfile

    outcome = TakeoutFile()
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        total = len(members)
        for done, member in enumerate(members):
            if member.is_dir():
                continue
            normalized = member.filename.replace("\\", "/").lower()
            basename = normalized.rsplit("/", 1)[-1]
            try:
                if basename == ZIP_DAILY_CSV_BASENAME:
                    with archive.open(member) as raw:
                        text_lines = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
                        parsed = parse_daily_csv_stream(text_lines)
                    outcome.days.extend(parsed.days)
                    outcome.rows_skipped += parsed.rows_skipped
                elif normalized.startswith(ZIP_ACTIVITIES_PREFIX) and basename.endswith(".tcx"):
                    with archive.open(member) as raw:
                        parsed = parse_tcx(raw.read().decode("utf-8", "replace"))
                    outcome.activities.extend(parsed.activities)
                    outcome.rows_skipped += parsed.rows_skipped
                elif (basename.startswith(ZIP_WEIGHT_PREFIX)
                        and basename.endswith(".json")):
                    with archive.open(member) as raw:
                        parsed = parse_weight_json(raw.read().decode("utf-8", "replace"))
                    outcome.weights.extend(parsed.weights)
                    outcome.rows_skipped += parsed.rows_skipped
            except (zipfile.BadZipFile, ElementTree.ParseError, OSError, EOFError):
                outcome.rows_skipped += 1
            if on_progress is not None:
                on_progress(done + 1, total)
    return outcome
