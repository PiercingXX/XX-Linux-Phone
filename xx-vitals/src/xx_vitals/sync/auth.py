"""Fabric sign-in against xx-chat. Port of sync/FabricLoginClient.kt.

xx-vitals never verifies passwords and never mints tokens: credentials go
to `POST /api/v1/fabric/login` on the configured xx-chat host only, and the
returned session token is kept in libsecret (Secret Service), never in
config.json or SQLite. All network and all `*_sync` secret calls run on
worker threads; the UI layer posts results back via GLib.idle_add.
"""

from __future__ import annotations

import base64
import http.client
import json
from dataclasses import dataclass
from urllib.parse import urlparse

APP_ID = "io.piercingxx.XXVitals"
LOGIN_PATH = "/api/v1/fabric/login"

_TOKEN_FIELDS = ("token", "access_token", "session_token", "session", "jwt")


@dataclass(frozen=True)
class LoginSuccess:
    token: str
    expires_at_ms: int | None


@dataclass(frozen=True)
class LoginFailure:
    code: int | None
    message: str


@dataclass(frozen=True)
class HostOk:
    url: str


@dataclass(frozen=True)
class HostError:
    message: str


def parse_host_url(raw: str) -> HostOk | HostError:
    trimmed = raw.strip().rstrip("/")
    if not trimmed:
        return HostError("Enter the host URL")
    if not trimmed.startswith("http://") and not trimmed.startswith("https://"):
        return HostError("URL must start with http:// or https://")
    without_scheme = trimmed.split("://", 1)[1]
    host_part = without_scheme.split("/", 1)[0].split("?", 1)[0]
    bare_host = host_part.rsplit(":", 1)[0] if ":" in host_part else host_part
    if not bare_host or ("." not in bare_host and bare_host != "localhost"):
        return HostError("Not a valid host")
    if ":" in host_part:
        try:
            port = int(host_part.rsplit(":", 1)[1])
        except ValueError:
            return HostError("Port must be 1–65535")
        if not 1 <= port <= 65535:
            return HostError("Port must be 1–65535")
    return HostOk(trimmed)


def extract_token(doc: object) -> str | None:
    if not isinstance(doc, dict):
        return None
    for key in _TOKEN_FIELDS:
        token = _non_blank_string(doc.get(key))
        if token is not None:
            return token
    nested = doc.get("data")
    if isinstance(nested, dict):
        for key in ("token", "access_token"):
            token = _non_blank_string(nested.get(key))
            if token is not None:
                return token
    return None


def _non_blank_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _pad_base64(segment: str) -> str:
    return segment + "=" * ((4 - len(segment) % 4) % 4)


def _claims(token: str) -> dict | None:
    segments = token.split(".")
    if len(segments) != 4 or not segments[0].startswith("v"):
        return None
    try:
        decoded = base64.urlsafe_b64decode(_pad_base64(segments[2])).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    try:
        doc = json.loads(decoded)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def token_expires_at_ms(token: str) -> int | None:
    claims = _claims(token)
    if claims is None:
        return None
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    return int(exp * 1000)


def token_user_id(token: str) -> str | None:
    claims = _claims(token)
    if claims is None:
        return None
    user_id = claims.get("user_id")
    if isinstance(user_id, str) and user_id:
        return user_id
    return None


def fabric_login(
    xx_chat_base_url: str,
    username: str,
    password: str,
    transport=None,
    timeout: float = 30.0,
) -> LoginSuccess | LoginFailure:
    parsed = parse_host_url(xx_chat_base_url)
    if isinstance(parsed, HostError):
        return LoginFailure(None, parsed.message)
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    call = transport if transport is not None else _default_transport
    try:
        code, text = call("POST", parsed.url + LOGIN_PATH, headers, body, timeout)
    except OSError as exc:
        return LoginFailure(None, str(exc) or "network unreachable")
    if code in (401, 403):
        return LoginFailure(code, "Wrong username or password")
    if code is None or not 200 <= code < 300:
        label = f"HTTP {code}" if code is not None else "network unreachable"
        return LoginFailure(code, f"xx-chat answered {label}")
    try:
        doc = json.loads(text or "")
    except ValueError:
        return LoginFailure(code, "unparseable login response")
    token = extract_token(doc)
    if token is None:
        return LoginFailure(code, "login response carried no token")
    return LoginSuccess(token=token, expires_at_ms=token_expires_at_ms(token))


def _default_transport(method: str, url: str, headers: dict, body: bytes, timeout: float):
    from xx_vitals.sync import http as cleartext

    parts = urlparse(url)
    if parts.scheme == "http":
        cleartext.guard_url(url, _allowed_cleartext())
    if parts.scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            parts.hostname or "", parts.port or 443, timeout=timeout
        )
    else:
        conn = http.client.HTTPConnection(parts.hostname or "", parts.port or 80, timeout=timeout)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8", "replace")
    finally:
        conn.close()


def _allowed_cleartext() -> set[str]:
    try:
        from xx_vitals.prefs import load as load_prefs

        return set(load_prefs().allowed_cleartext_hosts)
    except OSError:
        return set()


@dataclass(frozen=True)
class SyncCredentials:
    nas_host: str | None
    xxchat_host: str | None
    token: str | None
    relogin_required: bool

    @property
    def can_sync(self) -> bool:
        return bool(self.nas_host and self.nas_host.strip()) and bool(
            self.token and self.token.strip()
        )

    @property
    def token_configured(self) -> bool:
        return bool(self.token and self.token.strip())


class SecretBackend:
    def store_token(self, host: str, token: str) -> None:
        raise NotImplementedError

    def lookup_token(self, host: str) -> str | None:
        raise NotImplementedError

    def clear_token(self, host: str) -> None:
        raise NotImplementedError


class MemorySecretBackend(SecretBackend):
    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}

    def store_token(self, host: str, token: str) -> None:
        self._tokens[host] = token

    def lookup_token(self, host: str) -> str | None:
        return self._tokens.get(host)

    def clear_token(self, host: str) -> None:
        self._tokens.pop(host, None)


class LibsecretBackend(SecretBackend):
    def _schema(self):
        from gi.repository import Secret

        return Secret.Schema.new(
            APP_ID,
            Secret.SchemaFlags.NONE,
            {
                "application": Secret.SchemaAttributeType.STRING,
                "host": Secret.SchemaAttributeType.STRING,
            },
        )

    def _attributes(self, host: str) -> dict:
        return {"application": APP_ID, "host": host}

    def store_token(self, host: str, token: str) -> None:
        from gi.repository import Secret

        Secret.password_store_sync(
            self._schema(), self._attributes(host),
            Secret.COLLECTION_DEFAULT, f"{APP_ID} fabric token", token, None,
        )

    def lookup_token(self, host: str) -> str | None:
        from gi.repository import Secret

        return Secret.password_lookup_sync(self._schema(), self._attributes(host), None)

    def clear_token(self, host: str) -> None:
        from gi.repository import Secret

        Secret.password_clear_sync(self._schema(), self._attributes(host), None)


def load_credentials(config, secrets: SecretBackend) -> SyncCredentials:
    nas_host = (config.server_url or "").strip() or None
    xxchat_host = (getattr(config, "xxchat_url", "") or "").strip() or None
    token = secrets.lookup_token(nas_host) if nas_host else None
    return SyncCredentials(
        nas_host=nas_host,
        xxchat_host=xxchat_host,
        token=token,
        relogin_required=bool(getattr(config, "relogin_required", False)),
    )
