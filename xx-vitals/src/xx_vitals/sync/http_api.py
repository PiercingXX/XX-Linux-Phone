"""Urllib SyncApi: verb/path mapping, Bearer [REDACTED] status taxonomy.

Production transport is stdlib urllib in worker threads only. The transport
is injectable so tests run without sockets: pass a fake callable
(method, url, headers, body) -> (status_code, response_body).
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from urllib.error import HTTPError, URLError

from xx_vitals.sync import http as cleartext
from xx_vitals.sync.api import (
    AuthExpired,
    BatchStamp,
    ChangesPage,
    Ok,
    Rejected,
    Transient,
    VolumeFull,
    from_iso,
    map_activity_row,
    map_daily_row,
    map_weight_row,
)

Transport = Callable[[str, str, dict, bytes | None], tuple[int, str]]


def _default_transport(
    method: str, url: str, headers: dict, body: bytes | None
) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        try:
            payload = exc.read().decode("utf-8", "replace")
        except OSError:
            payload = ""
        return exc.code, payload
    except (URLError, OSError, TimeoutError) as exc:
        raise ConnectionError(str(exc)) from exc


def _status_to_result(code: int | None, detail: str | None = None):
    if code is None:
        return Transient(detail)
    if 200 <= code < 300:
        return Ok(None)
    if code in (401, 403):
        return AuthExpired()
    if code == 507:
        return VolumeFull()
    if 400 <= code < 500:
        return Rejected(code, detail)
    return Transient(detail or f"HTTP {code}")


class HttpSyncApi:
    PATH_WEIGHT = "/v1/weight"
    PATH_DAILY = "/v1/daily"
    PATH_ACTIVITY = "/v1/activity"
    PATH_ACTIVITIES = "/v1/activities"
    PATH_CHANGES = "/v1/changes"
    PATH_BATCHES = "/v1/batches"

    def __init__(
        self, base_url: str, token_provider, allowed_cleartext_hosts,
        on_auth_expired=None, transport: Transport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token_provider = token_provider
        self._allowed = allowed_cleartext_hosts
        self._on_auth_expired = on_auth_expired
        self._transport = transport if transport is not None else _default_transport

    def _headers(self) -> dict:
        token = self._token_provider()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _call(self, method: str, path: str, body_obj=None) -> tuple[int | None, str]:
        url = self._base + path
        try:
            cleartext.guard_url(url, set(self._allowed()))
        except OSError as exc:
            return None, str(exc)
        data = None if body_obj is None else json.dumps(body_obj).encode("utf-8")
        try:
            return self._transport(method, url, self._headers(), data)
        except (ConnectionError, OSError, TimeoutError) as exc:
            return None, str(exc)

    def _write_result(self, method: str, path: str, body_obj=None):
        code, payload = self._call(method, path, body_obj)
        result = _status_to_result(code, payload or None)
        if isinstance(result, AuthExpired) and self._on_auth_expired is not None:
            self._on_auth_expired()
        return result

    def post_weight(self, measured_at_iso: str, weight_kg: float):
        return self._write_result(
            "POST", self.PATH_WEIGHT,
            {"measured_at": measured_at_iso, "weight_kg": weight_kg},
        )

    def patch_weight(self, server_id: int, measured_at_iso, weight_kg):
        body: dict = {}
        if measured_at_iso is not None:
            body["measured_at"] = measured_at_iso
        if weight_kg is not None:
            body["weight_kg"] = weight_kg
        return self._write_result("PATCH", f"{self.PATH_WEIGHT}/{server_id}", body)

    def delete_weight(self, server_id: int):
        return self._write_result("DELETE", f"{self.PATH_WEIGHT}/{server_id}")

    def post_daily(self, date, steps, distance_m, active_min,
                   effort_points, calories_kcal, derived_mask):
        return self._write_result("POST", self.PATH_DAILY, {
            "date": date, "steps": steps, "distance_m": distance_m,
            "active_min": active_min, "effort_points": effort_points,
            "calories_kcal": calories_kcal, "derived_mask": derived_mask,
        })

    def post_activity(self, type, started_at_iso, ended_at_iso, distance_m,
                      calories_kcal, effort_points, active_min, steps):
        return self._write_result("POST", self.PATH_ACTIVITY, {
            "type": type, "started_at": started_at_iso, "ended_at": ended_at_iso,
            "distance_m": distance_m, "calories_kcal": calories_kcal,
            "effort_points": effort_points, "active_min": active_min, "steps": steps,
        })

    def find_weight_id(self, measured_at_ms: int):
        day = _day_window(measured_at_ms)
        code, payload = self._call(
            "GET", f"{self.PATH_WEIGHT}?from={day[0]}&to={day[1]}"
        )
        if code is None or code >= 500:
            return Transient(payload or None)
        if code in (401, 403):
            self._fire_auth()
            return AuthExpired()
        if code == 507:
            return VolumeFull()
        if code < 200 or code >= 300:
            return Rejected(code, payload or None)
        try:
            rows = json.loads(payload or "{}").get("rows", [])
        except ValueError:
            return Transient("unparseable weight lookup")
        hits = []
        for row in rows:
            if from_iso(row.get("measured_at", "")) == measured_at_ms:
                hits.append(row.get("id"))
        if len(hits) == 1:
            return Ok(hits[0])
        return Ok(None)

    def pull_changes(self, since_iso: str):
        from urllib.parse import quote

        code, payload = self._call("GET", f"{self.PATH_CHANGES}?since={quote(since_iso, safe='')}")
        if code is None or (code is not None and code >= 500):
            return Transient(payload or None)
        if code in (401, 403):
            self._fire_auth()
            return AuthExpired()
        if code == 507:
            return VolumeFull()
        if code < 200 or code >= 300:
            return Rejected(code, payload or None)
        try:
            doc = json.loads(payload)
        except ValueError:
            return Transient("unparseable changes body")
        try:
            return Ok(_parse_changes(doc))
        except (KeyError, TypeError, ValueError):
            return Transient("unparseable changes body")

    def fetch_batch_stamps(self):
        code, payload = self._call("GET", self.PATH_BATCHES)
        if code is None or (code is not None and code >= 500):
            return Transient(payload or None)
        if code in (401, 403):
            self._fire_auth()
            return AuthExpired()
        if code == 507:
            return VolumeFull()
        if code < 200 or code >= 300:
            return Rejected(code, payload or None)
        try:
            doc = json.loads(payload)
        except ValueError:
            return Transient("unparseable batches body")
        rows = doc.get("batches", [])
        stamps = [
            BatchStamp(id=row.get("id", 0), started_at_iso=row.get("started_at"))
            for row in rows if isinstance(row, dict) and row.get("id", 0) > 0
        ]
        return Ok(stamps)

    def fetch_batches(self):
        code, payload = self._call("GET", self.PATH_BATCHES)
        if code in (401, 403):
            self._fire_auth()
            return AuthExpired()
        if code == 507:
            return VolumeFull()
        if code is None or (code is not None and code >= 500):
            return Transient(payload or None)
        if code < 200 or code >= 300:
            return Rejected(code, payload or None)
        try:
            doc = json.loads(payload)
        except ValueError:
            return Transient("unparseable batches body")
        rows = doc.get("batches", [])
        if not isinstance(rows, list):
            return Transient("unparseable batches body")
        return Ok([row for row in rows if isinstance(row, dict)])

    def fetch_daily(self, from_iso_date: str, to_iso_date: str):
        code, payload = self._call(
            "GET", f"{self.PATH_DAILY}?from={from_iso_date}&to={to_iso_date}"
        )
        if code is None or (code is not None and code >= 500):
            return Transient(payload or None)
        if code in (401, 403):
            self._fire_auth()
            return AuthExpired()
        if code == 507:
            return VolumeFull()
        if code < 200 or code >= 300:
            return Rejected(code, payload or None)
        try:
            rows = json.loads(payload or "{}").get("rows", [])
            return Ok([map_daily_row(
                row.get("date", ""), steps=row.get("steps"),
                distance_m=row.get("distance_m"), active_min=row.get("active_min"),
                effort_points=row.get("effort_points"),
                calories_kcal=row.get("calories_kcal"), hr_avg=row.get("hr_avg"),
                hr_min=row.get("hr_min"), hr_max=row.get("hr_max"),
                hr_sample_count=row.get("hr_sample_count"),
                weight_avg_kg=row.get("weight_avg_kg"),
                weight_min_kg=row.get("weight_min_kg"),
                weight_max_kg=row.get("weight_max_kg"),
                derived_mask=row.get("derived_mask"),
                batch_id=row.get("batch_id"),
            ) for row in rows])
        except (ValueError, AttributeError, TypeError):
            return Transient("unparseable daily body")

    def fetch_activities(self, from_iso_date: str, to_iso_date: str):
        code, payload = self._call(
            "GET", f"{self.PATH_ACTIVITIES}?from={from_iso_date}&to={to_iso_date}"
        )
        if code is None or (code is not None and code >= 500):
            return Transient(payload or None)
        if code in (401, 403):
            self._fire_auth()
            return AuthExpired()
        if code == 507:
            return VolumeFull()
        if code < 200 or code >= 300:
            return Rejected(code, payload or None)
        try:
            rows = json.loads(payload or "{}").get("rows", [])
            return Ok([map_activity_row(
                row.get("type", ""), row.get("started_at", ""), row.get("ended_at", ""),
                distance_m=row.get("distance_m"),
                calories_kcal=row.get("calories_kcal"),
                effort_points=row.get("effort_points"),
                active_min=row.get("active_min"), hr_avg=row.get("hr_avg"),
                hr_max=row.get("hr_max"), steps=row.get("steps"),
                track_geojson=row.get("track_geojson"),
                batch_id=row.get("batch_id"),
            ) for row in rows])
        except (ValueError, AttributeError, TypeError):
            return Transient("unparseable activities body")

    def _fire_auth(self) -> None:
        if self._on_auth_expired is not None:
            self._on_auth_expired()


DAY_MS = 24 * 60 * 60 * 1000


def _day_window(measured_at_ms: int) -> tuple[str, str]:
    from datetime import datetime, timezone

    day_before = datetime.fromtimestamp(
        (measured_at_ms - DAY_MS) / 1000, tz=timezone.utc).date()
    day_after = datetime.fromtimestamp(
        (measured_at_ms + DAY_MS) / 1000, tz=timezone.utc).date()
    return (day_before.isoformat(), day_after.isoformat())


def _parse_changes(doc: dict) -> ChangesPage:
    weight = []
    for row in doc.get("weight", []):
        sample = map_weight_row(
            row.get("measured_at", ""), row.get("weight_kg", 0.0),
            batch_id=row.get("batch_id"),
        )
        if sample is not None:
            weight.append(sample)
    activities = [
        map_activity_row(
            row.get("type", ""), row.get("started_at", ""), row.get("ended_at", ""),
            distance_m=row.get("distance_m"), calories_kcal=row.get("calories_kcal"),
            effort_points=row.get("effort_points"), active_min=row.get("active_min"),
            hr_avg=row.get("hr_avg"), hr_max=row.get("hr_max"), steps=row.get("steps"),
            track_geojson=row.get("track_geojson"), batch_id=row.get("batch_id"),
        )
        for row in doc.get("activities", [])
    ]
    dailies = [
        map_daily_row(
            row.get("date", ""), steps=row.get("steps"), distance_m=row.get("distance_m"),
            active_min=row.get("active_min"), effort_points=row.get("effort_points"),
            calories_kcal=row.get("calories_kcal"), hr_avg=row.get("hr_avg"),
            hr_min=row.get("hr_min"), hr_max=row.get("hr_max"),
            hr_sample_count=row.get("hr_sample_count"),
            weight_avg_kg=row.get("weight_avg_kg"), weight_min_kg=row.get("weight_min_kg"),
            weight_max_kg=row.get("weight_max_kg"), derived_mask=row.get("derived_mask"),
            batch_id=row.get("batch_id"),
        )
        for row in doc.get("daily", [])
    ]
    batch_ids = [
        row.get("batch_id") for row in
        (doc.get("weight", []) + doc.get("activities", []) + doc.get("daily", []))
        if row.get("batch_id") is not None
    ]
    return ChangesPage(
        weight=weight, activities=activities, dailies=dailies,
        max_batch_id=max(batch_ids) if batch_ids else 0,
    )
