"""Estate origin rules, hub probe, estate login and 401/403 handling.

Mirrors hub/EstateOrigin.kt, hub/HubDiscovery.kt (minus the provider: no
Linux equivalent exists), auth/UnauthorizedReason.kt, EstateAuthClient.kt,
AuthCoordinator.kt (Linux order hub -> stored -> Sign in) and the
SessionManager adopt/wipe core. Pure functions so the matrix is
unit-testable without a display. Every blocking call runs on worker
threads, never the GTK main loop.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
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
    lowered = reason.strip().lower()
    if not lowered:
        return AuthAction.NONE
    if lowered in WIPE_REASONS:
        return AuthAction.WIPE
    if lowered == EXPIRED:
        return AuthAction.RENEW
    return AuthAction.NONE


def host_of(url: str) -> str | None:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    return host or None


def pick(*candidates) -> str:
    for candidate in candidates:
        if candidate:
            normalized = normalize(candidate)
            if normalized:
                return normalized
    return ""


def resolve_api_base(origin: str, debug_override: str | None = None) -> str:
    """The API base: a debug env override verbatim, else {origin}/audiobook/."""
    override = (debug_override or "").strip()
    if override:
        try:
            parts = urlsplit(override)
        except ValueError:
            parts = None
        if (parts is not None and parts.hostname
                and parts.scheme in ("http", "https")):
            return override.rstrip("/") + "/"
    return api_base(origin) if normalize(origin) else ""


@dataclass
class Session:
    """A signed-in estate session. Never logs the token (see __repr__)."""

    origin: str
    token: str
    user_id: str = ""
    expires_at: int = 0
    source: str = "stored"
    username: str = ""
    display_name: str = ""
    role: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role.lower() == "admin"

    def __repr__(self) -> str:
        return (f"Session(origin={self.origin!r}, user_id={self.user_id!r},"
                f" source={self.source!r}, token=***)")


@dataclass
class StoredCredentials:
    """What survives a restart. Never a password."""

    origin: str
    token: str
    user_id: str = ""
    expires_at: int = 0
    username: str = ""
    display_name: str = ""
    role: str = ""

    def to_session(self, source: str) -> Session:
        return Session(origin=self.origin, token=self.token,
                       user_id=self.user_id, expires_at=self.expires_at,
                       source=source, username=self.username,
                       display_name=self.display_name, role=self.role)

    def __repr__(self) -> str:
        return (f"StoredCredentials(origin={self.origin!r},"
                f" user_id={self.user_id!r}, token=***)")


class CredentialStore:
    """Token + origin persistence. The password is never an input here."""

    def load(self) -> StoredCredentials | None:
        raise NotImplementedError

    def save(self, credentials: StoredCredentials) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


class InMemoryCredentialStore(CredentialStore):
    """Test and fallback store (mirrors InMemoryCredentialStore)."""

    def __init__(self, initial: StoredCredentials | None = None) -> None:
        self._lock = threading.Lock()
        self._value = initial

    def load(self) -> StoredCredentials | None:
        with self._lock:
            return self._value

    def save(self, credentials: StoredCredentials) -> None:
        with self._lock:
            self._value = credentials

    def clear(self) -> None:
        with self._lock:
            self._value = None


class LibsecretBackend(CredentialStore):
    """Token bundle in the default collection (the xx-vitals shape).

    Schema attributes: application = APP_ID, host = originKey.
    Corrupt entries are deleted and recreated. All calls block: run them
    on worker threads.
    """

    def _schema(self):
        from gi.repository import Secret

        from xx_audiobook import __app_id__
        return Secret.Schema.new(
            __app_id__,
            Secret.SchemaFlags.NONE,
            {
                "application": Secret.SchemaAttributeType.STRING,
                "host": Secret.SchemaAttributeType.STRING,
            },
        )

    def _attributes(self, origin: str) -> dict:
        from xx_audiobook import __app_id__
        return {"application": __app_id__, "host": origin_key(origin)}

    def load(self) -> StoredCredentials | None:
        from gi.repository import Secret

        from xx_audiobook import __app_id__
        try:
            items = Secret.password_search_sync(
                self._schema(), {"application": __app_id__}, None,
                Secret.SearchFlags.ALL)
        except Exception:
            return None
        for item in items or []:
            try:
                _attrs, secret = item.get_secret_sync()
            except Exception:
                continue
            if secret is None:
                continue
            try:
                doc = json.loads(secret.decode("utf-8"))
            except (ValueError, AttributeError):
                try:
                    item.delete_sync()
                except Exception:
                    pass
                continue
            if (isinstance(doc, dict) and doc.get("token")
                    and doc.get("origin")):
                return StoredCredentials(
                    origin=str(doc.get("origin", "")),
                    token=str(doc.get("token", "")),
                    user_id=str(doc.get("user_id", "") or ""),
                    expires_at=int(doc.get("expires_at", 0) or 0),
                    username=str(doc.get("username", "") or ""),
                    display_name=str(doc.get("display_name", "") or ""),
                    role=str(doc.get("role", "") or ""))
            try:
                item.delete_sync()
            except Exception:
                pass
        return None

    def save(self, credentials: StoredCredentials) -> None:
        from gi.repository import Secret

        from xx_audiobook import __app_id__
        bundle = json.dumps({
            "origin": credentials.origin, "token": credentials.token,
            "user_id": credentials.user_id,
            "expires_at": credentials.expires_at,
            "username": credentials.username,
            "display_name": credentials.display_name,
            "role": credentials.role,
        })
        Secret.password_store_sync(
            self._schema(), self._attributes(credentials.origin),
            Secret.COLLECTION_DEFAULT,
            f"{__app_id__} estate token", bundle, None)

    def clear(self) -> None:
        from gi.repository import Secret

        from xx_audiobook import __app_id__
        Secret.password_clear_sync(
            self._schema(), {"application": __app_id__}, None)


@dataclass
class Discovery:
    token: str
    hub_id: str = ""
    expires_at: int = 0
    origin: str = ""


def _b64_decode(text: str) -> bytes | None:
    padded = text + "=" * (-len(text) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return decoder(padded)
        except (ValueError, TypeError):
            continue
    return None


def _hub_verify(document: str, now_seconds: int) -> Discovery | None:
    parts = document.split(".")
    if len(parts) != 4 or parts[0] != "v1" or parts[1] != "hub":
        return None
    raw = _b64_decode(parts[2])
    if raw is None:
        return None
    try:
        claims = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    session = claims.get("session") or ""
    if not isinstance(session, str) or not session:
        return None
    listen = claims.get("listen") or ""
    if listen not in ("127.0.0.1", "::1"):
        return None
    exp = claims.get("exp", 0) or 0
    try:
        exp = int(exp)
    except (TypeError, ValueError):
        return None
    if exp != 0 and now_seconds >= exp:
        return None
    signed_part = f"{parts[0]}.{parts[1]}.{parts[2]}"
    key = hashlib.sha256(session.encode("utf-8")).digest()
    expected = hmac.new(key, signed_part.encode("ascii"),
                        hashlib.sha256).digest()
    given = _b64_decode(parts[3])
    if given is None or not hmac.compare_digest(expected, given):
        return None
    origin = claims.get("origin") or ""
    hub_id = claims.get("hub_id") or ""
    return Discovery(
        token=session,
        hub_id=hub_id if isinstance(hub_id, str) else "",
        expires_at=exp,
        origin=origin.strip().rstrip("/") if isinstance(origin, str) else "")


def parse_hub_envelope(body: str, now_seconds: int) -> Discovery | None:
    """A bare envelope string or {"discovery": ..., "origin": ...}."""
    trimmed = body.strip()
    if trimmed.startswith("v1."):
        return _hub_verify(trimmed, now_seconds)
    try:
        obj = json.loads(trimmed)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    doc = obj.get("discovery") or ""
    if not isinstance(doc, str) or not doc:
        return None
    verified = _hub_verify(doc, now_seconds)
    if verified is None:
        return None
    if verified.origin:
        return verified
    envelope_origin = obj.get("origin") or ""
    if isinstance(envelope_origin, str):
        return Discovery(token=verified.token, hub_id=verified.hub_id,
                         expires_at=verified.expires_at,
                         origin=envelope_origin.strip().rstrip("/"))
    return verified


def hub_sign(claims: dict) -> str:
    """Sign claims the way xx-apps does (tests and debug fakes)."""
    raw = base64.urlsafe_b64encode(
        json.dumps(claims).encode("utf-8")).decode("ascii").rstrip("=")
    signed_part = f"v1.hub.{raw}"
    session = claims.get("session", "")
    key = hashlib.sha256(session.encode("utf-8")).digest()
    mac = hmac.new(key, signed_part.encode("ascii"),
                   hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")
    return f"{signed_part}.{sig}"


def trusted_origin(hub_origin: str | None, stored_origin: str | None,
                   default_origin: str | None) -> str | None:
    """The hub origin, only when https on an already-known host."""
    candidate = (hub_origin or "").strip()
    if not candidate or not candidate.lower().startswith("https://"):
        return None
    normalized = normalize(candidate)
    if not normalized:
        return None
    host = host_of(normalized)
    if not host:
        return None
    known = set()
    for origin in (stored_origin, default_origin):
        if origin and origin.strip():
            known_host = host_of(normalize(origin))
            if known_host:
                known.add(known_host)
    if host in known:
        return normalized.lower()
    return None


def hub_probe(host: str = HUB_HOST, port: int = HUB_PORT,
              timeout_s: float = HUB_TIMEOUT_S,
              now_seconds: int | None = None) -> Discovery | None:
    """One bounded GET of /hub/discovery. Never throws, never retries."""
    now = now_seconds if now_seconds is not None else int(time.time())
    request = urllib.request.Request(
        f"http://{host}:{port}{HUB_PATH}",
        headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request,
                                    timeout=timeout_s) as response:
            if not 200 <= response.status < 300:
                return None
            body = response.read().decode("utf-8", "replace")
    except Exception:
        return None
    try:
        return parse_hub_envelope(body, now)
    except Exception:
        return None


@dataclass
class Grant:
    token: str
    expires_at: int = 0
    user_id: str = ""
    role: str = ""
    display_name: str = ""
    grants: list | None = None

    def __repr__(self) -> str:
        return (f"Grant(user_id={self.user_id!r},"
                f" expires_at={self.expires_at!r}, token=***)")


@dataclass
class LoginOk:
    grant: Grant


@dataclass
class LoginRejected:
    code: int
    reason: str | None


@dataclass
class LoginRateLimited:
    pass


@dataclass
class LoginUnreachable:
    message: str = UNREACHABLE_MESSAGE


@dataclass
class LoginFailed:
    code: int
    message: str


def parse_grant(text: str) -> Grant | None:
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    token = obj.get("token") or ""
    if not isinstance(token, str) or not token.strip():
        return None
    try:
        expires_at = int(obj.get("expires_at", 0) or 0)
    except (TypeError, ValueError):
        expires_at = 0
    grants = obj.get("grants")
    return Grant(
        token=token.strip(), expires_at=expires_at,
        user_id=str(obj.get("user_id", "") or ""),
        role=str(obj.get("role", "") or ""),
        display_name=str(obj.get("display_name", "") or ""),
        grants=[str(g) for g in grants]
        if isinstance(grants, list) else [])


class EstateAuthClient:
    """The estate door's phone routes (plain client, no auth handling).

    POST {origin}/auth/login {username,password,client,device_label} and
    POST {origin}/auth/refresh (bearer, {}). The password lives only in
    the login request body; it is never logged or stored.
    """

    CLIENT = "xx-audiobook"

    def __init__(self, timeout_s: float = 10.0) -> None:
        self._timeout_s = timeout_s

    def login(self, origin: str, username: str, password: str):
        body = json.dumps({
            "username": username, "password": password,
            "client": self.CLIENT, "device_label": "xx-audiobook",
        }).encode("utf-8")
        request = urllib.request.Request(
            normalize(origin) + "/auth/login", data=body,
            headers={"Accept": "application/json",
                     "Content-Type": "application/json"},
            method="POST")
        return self._call(request)

    def refresh(self, origin: str, token: str):
        request = urllib.request.Request(
            normalize(origin) + "/auth/refresh", data=b"{}",
            headers={"Accept": "application/json",
                     "Content-Type": "application/json",
                     "Authorization": "Bearer " + token},
            method="POST")
        return self._call(request)

    def _call(self, request):
        try:
            with urllib.request.urlopen(request,
                                        timeout=self._timeout_s) as response:
                text = response.read().decode("utf-8", "replace")
                code = response.status
        except urllib.error.HTTPError as exc:
            try:
                text = exc.read().decode("utf-8", "replace")
            except OSError:
                text = ""
            code = exc.code
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if isinstance(exc, ValueError):
                return LoginFailed(0, str(exc) or "bad address")
            return LoginUnreachable()
        except Exception:
            return LoginUnreachable()
        if 200 <= code < 300:
            grant = parse_grant(text)
            if grant is not None:
                return LoginOk(grant)
            return LoginFailed(
                code, "The estate answered without a token.")
        if code in (401, 403):
            return LoginRejected(code, parse_reason(text))
        if code == 429:
            return LoginRateLimited()
        return LoginFailed(code, f"The estate answered {code}.")


@dataclass
class HubUp:
    origin: str
    token: str
    user_id: str = ""
    expires_at: int = 0
    source: str = "hub"


@dataclass
class UseStored:
    credentials: StoredCredentials


@dataclass
class NeedLogin:
    pass


@dataclass
class LoginSuccess:
    session: Session


@dataclass
class LoginError:
    message: str


class AuthCoordinator:
    """Linux auth order: hub probe -> stored token -> Sign in.

    There is no Linux equivalent of the xx-apps content provider, and
    nothing replaces it. All calls block; run them off the main thread.
    """

    def __init__(self, store: CredentialStore | None = None,
                 client: EstateAuthClient | None = None,
                 default_origin: str = "",
                 probe=None,
                 now_seconds=None) -> None:
        self._store = (store if store is not None
                       else InMemoryCredentialStore())
        self._client = (client if client is not None
                        else EstateAuthClient())
        self._default_origin = default_origin
        self._probe = probe if probe is not None else hub_probe
        self._now_seconds = (now_seconds if now_seconds is not None
                             else lambda: int(time.time()))

    def decide(self, discovery: Discovery | None,
               stored: StoredCredentials | None):
        stored_origin = stored.origin if stored is not None else ""
        if discovery is not None and discovery.token.strip():
            hub_origin = trusted_origin(discovery.origin, stored_origin,
                                        self._default_origin)
            return HubUp(
                origin=pick(hub_origin, stored_origin,
                            self._default_origin),
                token=discovery.token.strip(), user_id="",
                expires_at=discovery.expires_at, source="hub")
        if (stored is not None and stored.token.strip()
                and stored.origin.strip()):
            return UseStored(stored)
        return NeedLogin()

    def resolve(self) -> Session | None:
        try:
            discovery = self._probe()
        except Exception:
            discovery = None
        stored = self._store.load()
        decision = self.decide(discovery, stored)
        if isinstance(decision, HubUp):
            same_user = stored is not None
            session = Session(
                origin=decision.origin, token=decision.token,
                user_id=(stored.user_id if same_user and stored else ""),
                expires_at=decision.expires_at, source=decision.source,
                username=(stored.username if same_user and stored else ""),
                display_name=(stored.display_name if same_user and stored
                              else ""),
                role=(stored.role if same_user and stored else ""))
            self._persist(session)
            return session
        if isinstance(decision, UseStored):
            return decision.credentials.to_session("stored")
        return None

    def login(self, origin_input: str, username: str, password: str):
        blocked = problem(origin_input)
        if blocked is not None:
            return LoginError(blocked)
        if not username.strip() or not password:
            return LoginError("Type your estate username and password.")
        origin = normalize(origin_input)
        result = self._client.login(origin, username.strip(), password)
        if isinstance(result, LoginOk):
            session = Session(
                origin=origin, token=result.grant.token,
                user_id=result.grant.user_id,
                expires_at=result.grant.expires_at, source="login",
                username=username.strip(),
                display_name=result.grant.display_name,
                role=result.grant.role)
            self._persist(session)
            return LoginSuccess(session)
        if isinstance(result, LoginRejected):
            if action_for_reason(result.reason) == AuthAction.WIPE:
                return LoginError("This estate account is disabled.")
            if result.reason == "not_a_person":
                return LoginError(
                    "That is a machine credential, not a person.")
            return LoginError("Wrong username or password.")
        if isinstance(result, LoginRateLimited):
            return LoginError(
                "Too many tries. Wait a minute and try again.")
        return LoginError(result.message)

    def renew(self, stale: Session) -> Session | None:
        try:
            discovery = self._probe()
        except Exception:
            discovery = None
        if (discovery is not None and discovery.token.strip()
                and discovery.token.strip() != stale.token):
            origin = (trusted_origin(discovery.origin, stale.origin,
                                     self._default_origin)
                      or stale.origin)
            renewed = Session(
                origin=pick(origin, stale.origin), token=discovery.token,
                user_id=stale.user_id, expires_at=discovery.expires_at,
                source="hub", username=stale.username,
                display_name=stale.display_name, role=stale.role)
            self._persist(renewed)
            return renewed
        result = self._client.refresh(stale.origin, stale.token)
        if isinstance(result, LoginOk):
            renewed = Session(
                origin=stale.origin, token=result.grant.token,
                user_id=(result.grant.user_id or stale.user_id),
                expires_at=result.grant.expires_at, source=stale.source,
                username=stale.username, display_name=stale.display_name,
                role=(result.grant.role or stale.role))
            self._persist(renewed)
            return renewed
        return None

    def forget(self) -> None:
        self._store.clear()

    def _persist(self, session: Session) -> None:
        self._store.save(StoredCredentials(
            origin=session.origin, token=session.token,
            user_id=session.user_id, expires_at=session.expires_at,
            username=session.username, display_name=session.display_name,
            role=session.role))


class SessionStore:
    """Who is signed in, against which API base (SessionManager core).

    Owns the session, the 403 not_granted access flag, and the adopt
    rule: a different user id wipes local state first. Implements the
    api.TokenSource protocol (current/renew/on_rejected/on_access) so the
    HTTP client sends the Bearer [REDACTED] renews once on 401 expired.
    """

    def __init__(self, coordinator: AuthCoordinator,
                 debug_override=None,
                 wipe_local_state=None,
                 before_sign_out=None,
                 owner_get=None, owner_set=None) -> None:
        self._coordinator = coordinator
        self._debug_override = (debug_override
                                if debug_override is not None
                                else _default_debug_override)
        self._wipe_local_state = (wipe_local_state
                                  if wipe_local_state is not None
                                  else (lambda: None))
        self._before_sign_out = (before_sign_out
                                 if before_sign_out is not None
                                 else (lambda wipe: None))
        self._owner = {}
        self._owner_get = owner_get or (lambda: self._owner.get("uid"))
        self._owner_set = owner_set or (lambda uid: self._owner.update(
            {"uid": uid}))
        self._lock = threading.Lock()
        self._session: Session | None = None
        self._access_denied = False
        self._sign_out_message: str | None = None

    @property
    def session(self) -> Session | None:
        with self._lock:
            return self._session

    @property
    def access_denied(self) -> bool:
        with self._lock:
            return self._access_denied

    @property
    def sign_out_message(self) -> str | None:
        with self._lock:
            return self._sign_out_message

    def api_base_for(self, session: Session) -> str:
        return resolve_api_base(session.origin,
                                self._debug_override())

    def live_api_base(self) -> str | None:
        session = self.session
        if session is None:
            return None
        base = self.api_base_for(session)
        return base or None

    def start(self) -> Session | None:
        session = self._coordinator.resolve()
        if session is None:
            with self._lock:
                self._session = None
            return None
        self.adopt(session)
        return session

    def login(self, origin: str, username: str, password: str):
        result = self._coordinator.login(origin, username, password)
        if isinstance(result, LoginSuccess):
            self.adopt(result.session)
        return result

    def adopt(self, session: Session) -> None:
        uid = session.user_id
        if uid:
            try:
                prior = self._owner_get()
            except Exception:
                prior = None
            if prior and prior != uid:
                try:
                    self._wipe_local_state()
                except Exception:
                    pass
            if prior != uid:
                try:
                    self._owner_set(uid)
                except Exception:
                    pass
        with self._lock:
            self._access_denied = False
            self._sign_out_message = None
            self._session = session

    def sign_out(self, wipe: bool,
               message: str | None = None) -> None:
        try:
            self._before_sign_out(wipe)
        except Exception:
            pass
        try:
            self._coordinator.forget()
        except Exception:
            pass
        if wipe:
            try:
                self._wipe_local_state()
            except Exception:
                pass
        with self._lock:
            self._access_denied = False
            self._sign_out_message = message
            self._session = None

    def current(self) -> tuple | None:
        session = self.session
        if session is None:
            return None
        base = self.api_base_for(session)
        if not base:
            return None
        return (session.token, base)

    def renew(self, stale_token: str):
        session = self.session
        if session is None or session.token != stale_token:
            current = self.session
            if current is None:
                return None
            return (current.token, self.api_base_for(current))
        stale = session
        renewed = self._coordinator.renew(stale)
        if renewed is None:
            return None
        with self._lock:
            self._session = renewed
        return (renewed.token, self.api_base_for(renewed))

    def on_rejected(self, action: str, reason: str | None) -> None:
        if action == "wipe":
            self.sign_out(
                wipe=True,
                message="This device was signed out of the estate.")
        else:
            self.sign_out(
                wipe=False,
                message="Your session expired. Sign in again.")

    def on_access(self, granted: bool) -> None:
        with self._lock:
            self._access_denied = not granted


def _default_debug_override() -> str | None:
    from xx_audiobook import prefs as prefs_module
    return prefs_module.debug_api_base()
