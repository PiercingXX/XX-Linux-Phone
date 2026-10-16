"""Profile sync presentation. Port of sync/SyncUiModel.kt (pure part).

Absent values render as gaps, never fabricated zeroes; a 401 surfaces as a
re-login line and a 507 as a storage-full line while the queue is kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from xx_vitals.sync.auth import token_user_id

AUTH_NOTICE = "NAS rejected the session token — sign in again"
VOLUME_NOTICE = "NAS storage full — queue kept, will retry"


def describe_host(host: str | None) -> str:
    if host is None or not host.strip():
        return "— not configured —"
    return host


def describe_login(credentials) -> str:
    token = credentials.token if credentials is not None else None
    if token is None or not token.strip():
        return "not signed in"
    if getattr(credentials, "relogin_required", False):
        return "re-login required"
    identity = token_user_id(token)
    if identity is None:
        return "signed in"
    return f"signed in · {identity}"


def describe_when(epoch_ms: int | None, now_ms: int | None = None) -> str:
    if epoch_ms is None or epoch_ms <= 0:
        return "—"
    return datetime.fromtimestamp(epoch_ms / 1000).astimezone().strftime("%Y-%m-%d %H:%M")


def describe_pending(pending_count: int) -> str:
    if pending_count == 0:
        return "0 queued"
    return f"{pending_count} queued"


def failure_notice(kind: str) -> str | None:
    if kind == "auth_expired":
        return AUTH_NOTICE
    if kind == "volume_full":
        return VOLUME_NOTICE
    return None


def notice_for_exception(exc: BaseException) -> str | None:
    from xx_vitals.sync import engine as engine_module

    if isinstance(exc, engine_module.SyncAuthException):
        return AUTH_NOTICE
    name = type(exc).__name__
    if name in ("VolumeFull",) or "507" in str(exc):
        return VOLUME_NOTICE
    return None


@dataclass(frozen=True)
class BatchInfo:
    id: int
    kind: str
    started_at: str | None
    finished_at: str | None
    rows_read: int
    rows_inserted: int
    rows_skipped: int
    status: str
    error: str | None

    @property
    def glyph(self) -> str:
        if self.status == "ok":
            return "✓"
        if self.status == "error":
            return "✗"
        if self.status == "warn":
            return "⚠"
        return "→"


def batch_from_row(row: dict) -> BatchInfo:
    return BatchInfo(
        id=int(row.get("id", 0)),
        kind=str(row.get("kind", "")),
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        rows_read=int(row.get("rows_read", 0)),
        rows_inserted=int(row.get("rows_inserted", 0)),
        rows_skipped=int(row.get("rows_skipped", 0)),
        status=str(row.get("status", "")),
        error=row.get("error"),
    )


def batch_line(info: BatchInfo) -> str:
    when = info.started_at or "—"
    line = (
        f"{info.glyph} {info.kind or 'sync'} · {when} · "
        f"{info.rows_read} read, {info.rows_inserted} kept, "
        f"{info.rows_skipped} skipped"
    )
    if info.error:
        return f"{line} · {info.error}"
    return line


def batches_lines(rows: list[dict]) -> list[str]:
    return [batch_line(batch_from_row(row)) for row in rows]


BATCHES_UNAVAILABLE = "Batch history unavailable right now"


def batches_outcome(api) -> tuple[list[str], str | None]:
    from xx_vitals.sync.api import AuthExpired, Ok, VolumeFull

    result = api.fetch_batches()
    if isinstance(result, Ok):
        lines = batches_lines(result.value)
        if not lines:
            return ["No sync history yet — it appears after the first sync."], None
        return lines, None
    if isinstance(result, AuthExpired):
        return [], AUTH_NOTICE
    if isinstance(result, VolumeFull):
        return [], VOLUME_NOTICE
    return [], BATCHES_UNAVAILABLE
