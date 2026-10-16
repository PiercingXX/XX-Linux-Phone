"""Sync wire helpers: endpoint paths, cursor handling, status mapping.

Mirrors the contract in sync/SyncApi.kt and HttpSyncApi.kt status mapping
(401/403 -> auth-expired, 507 -> volume-full, other 4xx -> rejected,
network/5xx -> transient) plus the CleartextGuardInterceptor.kt policy
(HTTPS always allowed; cleartext only to user-configured hosts).
Pure stdlib so tests run headless.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

EPOCH_ISO = "1970-01-01T00:00:00+00:00"

PATH_WEIGHT = "/v1/weight"
PATH_DAILY = "/v1/daily"
PATH_ACTIVITY = "/v1/activity"
PATH_CHANGES = "/v1/changes"
PATH_BATCHES = "/v1/batches"
PATH_HEALTHZ = "/healthz"


def to_iso(epoch_ms: int) -> str:
    """Epoch millis to an ISO-8601 string with numeric offset (never naive)."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def from_iso(value: str) -> int:
    """ISO-8601 string back to epoch millis."""
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def map_status(code: int | None) -> str:
    """Map an HTTP outcome to the SyncEngine disposition.

    code is None for transport failures (IOException/timeout/DNS).
    """
    if code is None:
        return "transient"
    if 200 <= code < 300:
        return "ok"
    if code in (401, 403):
        return "auth_expired"
    if code == 507:
        return "volume_full"
    if 400 <= code < 500:
        return "rejected"
    return "transient"


def _split_host_port(entry: str) -> tuple[str, int | None]:
    idx = entry.rfind(":")
    if idx <= 0:
        return entry, None
    try:
        return entry[:idx], int(entry[idx + 1:])
    except ValueError:
        return entry, None


def _entry_matches(entry: str, host: str, port: int) -> bool:
    trimmed = entry.strip()
    if not trimmed:
        return False
    if "://" in trimmed:
        try:
            parsed = urlparse(trimmed)
        except ValueError:
            return False
        parsed_host = parsed.hostname or ""
        parsed_port = parsed.port
        if parsed_port is None:
            parsed_port = 443 if parsed.scheme == "https" else 80
        return parsed_host.lower() == host.lower() and parsed_port == port
    entry_host, entry_port = _split_host_port(trimmed)
    return entry_host.lower() == host.lower() and (entry_port is None or entry_port == port)


def is_cleartext_allowed(host: str, port: int, configured: set[str]) -> bool:
    """True when host:port matches a user-configured cleartext host.

    Entries may be a bare host (any port), "host:port", or a URL whose
    host/port is used. Matching is case-insensitive on host.
    """
    return any(_entry_matches(entry, host, port) for entry in configured)


def is_https_allowed(url: str) -> bool:
    """HTTPS is always allowed regardless of the configured host set."""
    return urlparse(url).scheme == "https"


def check_url(url: str, configured: set[str]) -> bool:
    """Enforce the LAN-only policy: HTTPS always, cleartext only if configured."""
    if is_https_allowed(url):
        return True
    parsed = urlparse(url)
    port = parsed.port or 80
    return is_cleartext_allowed(parsed.hostname or "", port, configured)
