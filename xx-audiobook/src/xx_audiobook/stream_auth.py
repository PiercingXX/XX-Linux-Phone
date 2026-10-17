"""Estate-only bearer for media URLs (M2).

Mirrors playback/StreamAuth.kt: the header is attached only to requests
whose scheme + host + port equal the signed-in estate origin, taken from
the API base, never hard-coded. Local file:// tracks and any other host
get nothing. Pure string rules so the matrix is unit-testable.
"""

from __future__ import annotations

from xx_audiobook import auth as auth_module


class StreamAuth:
    """In-memory bearer; the token is never logged or persisted here."""

    def __init__(self) -> None:
        self._origin: str | None = None
        self._bearer: str | None = None

    def set(self, api_base: str, token: str) -> None:
        try:
            self._origin = auth_module.origin_key(api_base)
        except ValueError:
            self._origin = None
        cleaned = (token or "").strip()
        self._bearer = cleaned or None

    def clear(self) -> None:
        self._origin = None
        self._bearer = None

    def headers_for(self, uri: str) -> dict[str, str]:
        ours = self._origin
        token = self._bearer
        if not ours or not token:
            return {}
        try:
            target = auth_module.origin_key(uri)
        except ValueError:
            return {}
        if target != ours:
            return {}
        return {"Authorization": f"Bearer {token}"}
