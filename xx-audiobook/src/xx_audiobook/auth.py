"""Estate origin rules and 401/403 handling.

Mirrors hub/EstateOrigin.kt and auth/UnauthorizedReason.kt exactly: the same
normalize/apiBase/originKey/problem rules (forbidden ports, no path/query,
Tailscale hint) and the same reason-to-action mapping (expired renews, the
wipe set wipes, everything else is left to the caller; 403 not_granted never
signs out). Pure functions so the matrix is unit-testable without a display.
"""

from __future__ import annotations

import json
from enum import Enum
from urllib.parse import urlsplit

HUB_HOST = "127.0.0.1"
HUB_PORT = 17331
HUB_PATH = "/hub/discovery"
HUB_TIMEOUT_S = 0.25
API_PREFIX = "/audiobook/"
FORBIDDEN_PORTS = frozenset(range(8450, 8460)) | {8448}
MINT_PORT = 8457
UNREACHABLE_MESSAGE = "Can't reach the server. Is Tailscale up?"

EXPIRED = "expired"
NOT_GRANTED = "not_granted"
WIPE_REASONS = frozenset({"disabled", "revoked", "account_disabled",
                          "session_revoked"})


class AuthAction(Enum):
    RENEW = "renew"
    WIPE = "wipe"
    NONE = "none"


def normalize(origin: str) -> str:
    return origin.strip().rstrip("/")


def api_base(origin: str) -> str:
    return normalize(origin) + API_PREFIX


def origin_key(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return f"{parts.scheme}://{host}:{port}"


def same_origin(request_url: str, base: str) -> bool:
    return origin_key(request_url) == origin_key(base)


def problem(origin: str) -> str | None:
    """Validate an estate origin; None means usable."""
    text = origin.strip()
    if not text:
        return "Enter the estate address."
    try:
        parts = urlsplit(text)
    except ValueError:
        return "That address does not parse."
    if parts.scheme not in ("http", "https"):
        return "Use an http(s) address."
    if not parts.hostname:
        return "Use an http(s) address."
    if parts.path not in ("", "/") or parts.query:
        return "Enter just the origin, without a path."
    port = parts.port
    if port is not None and (port in FORBIDDEN_PORTS or port == MINT_PORT):
        return "That port is reserved."
    return None


def parse_reason(body: str | None) -> str | None:
    """Read reason from {"detail":…, "reason":…} or nested detail.reason."""
    if not body or not body.strip():
        return None
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    reason = obj.get("reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    detail = obj.get("detail")
    if isinstance(detail, dict):
        reason = detail.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
    return None


def action_for_reason(reason: str | None) -> AuthAction:
    if reason is None:
        return AuthAction.NONE
    lowered = reason.lower()
    if lowered in WIPE_REASONS:
        return AuthAction.WIPE
    if lowered == EXPIRED:
        return AuthAction.RENEW
    return AuthAction.NONE
