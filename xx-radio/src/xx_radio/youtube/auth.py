"""YouTube Music account credentials (PORT_SPEC.md 2.6, milestone M7).

Browser headers (recommended): the person pastes a request-header block or
a "Copy as cURL" command from a desktop browser's developer tools for any
``music.youtube.com`` request. This module parses either form into the
header dict ytmusicapi takes as browser credentials, defaulting
``x-goog-authuser`` to ``"0"`` when the paste has none (the main account)
and keeping any other value so brand accounts keep working.

OAuth (advanced, optional): ytmusicapi's device-code flow with the person's
own Google Cloud client id and secret. Every HTTP round trip goes through
an injected ``post`` callable so tests run against a fake token endpoint
and never touch the network.

Storage is libsecret only (``LibsecretStore``); the cookie jar yt-dlp
needs is written from the stored headers at resolve time onto
``$XDG_RUNTIME_DIR`` and removed on sign-out. Nothing secret is logged:
use :func:`redact_headers` / :func:`redact_text` before logging anything
that may carry headers.

ytmusicapi and ``gi`` are imported lazily, inside the functions that need
them, so importing this module never blocks and never requires them.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol

#: libsecret schema name (PORT_SPEC.md 2.6). Attributes per item:
#: ``{"kind": "browser" | "oauth" | "oauth-client", "account": <label>}``.
CREDENTIALS_SCHEMA = "io.piercingxx.XXRadio.Credentials"

KIND_BROWSER = "browser"
KIND_OAUTH = "oauth"
KIND_OAUTH_CLIENT = "oauth-client"

#: The cookie yt-dlp must never see: with it some YouTube clients refuse
#: the session and list no audio (PORT_SPEC.md 2.3).
LOGIN_INFO_COOKIE = "LOGIN_INFO"

COOKIE_JAR_NAME = "cookies.txt"
COOKIE_JAR_DOMAIN = ".youtube.com"

#: ytmusicapi's device-flow endpoints (ytmusicapi.constants).
OAUTH_SCOPE = "https://www.googleapis.com/auth/youtube"
OAUTH_CODE_URL = "https://www.youtube.com/o/oauth2/device/code"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:88.0)"
    " Gecko/20100101 Firefox/88.0 Cobalt/Version"
)
OAUTH_GRANT_TYPE = "http://oauth.net/grant_type/device/1.0"

_DEFAULT_AUTHUSER = "0"
_DROPPED_HEADERS = frozenset({"host", "content-length", "accept-encoding"})
_BROWSER_DEFAULTS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:88.0)"
        " Gecko/20100101 Firefox/88.0"
    ),
    "accept": "*/*",
    "content-type": "application/json",
    "origin": "https://music.youtube.com",
}


class CredentialsError(ValueError):
    """The pasted headers or cURL command cannot be used as credentials."""


class ValidationError(Exception):
    """The credentials were rejected or could not be checked."""


class CredentialsExpired(ValidationError):
    """The browser session ended or revoked the cookies; sign in again."""


class KeyringUnavailable(Exception):
    """No Secret Service is running; sign-in needs a keyring."""


class OAuthError(Exception):
    """The device flow failed."""


class OAuthDenied(OAuthError):
    """The person denied access or the device code expired."""


class BadOAuthClient(OAuthError):
    """The client id/secret is wrong or the YouTube Data API is off."""


# ---- header parsing --------------------------------------------------------


def parse_header_block(text: str) -> dict[str, str]:
    """Parse a pasted request-header block into lowercase ``{name: value}``.

    Drops HTTP/2 pseudo-headers (``:authority`` and friends) and skips
    blank lines. A value may itself contain ``": "``; only the first one
    separates the name. Later lines win on repeats.
    """
    headers: dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(":"):
            continue
        name, sep, value = line.partition(":")
        if not sep:
            raise CredentialsError(f"not a header line: {raw_line.strip()!r}")
        name = name.strip().lower()
        if not name:
            raise CredentialsError(f"not a header line: {raw_line.strip()!r}")
        headers[name] = value.strip()
    return headers


def _curl_header_args(argv: list[str]) -> tuple[list[str], str | None]:
    inline_headers: list[str] = []
    cookie_flag: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-H", "--header") and i + 1 < len(argv):
            inline_headers.append(argv[i + 1])
            i += 2
        elif arg.startswith("--header="):
            inline_headers.append(arg.partition("=")[2])
            i += 1
        elif arg in ("-b", "--cookie") and i + 1 < len(argv):
            cookie_flag = argv[i + 1]
            i += 2
        elif arg.startswith("--cookie="):
            cookie_flag = arg.partition("=")[2]
            i += 1
        else:
            i += 1
    return inline_headers, cookie_flag


def parse_curl(text: str) -> dict[str, str]:
    """Parse a "Copy as cURL" command into lowercase ``{name: value}``.

    Reads ``-H``/``--header`` entries and a ``-b``/``--cookie`` jar value;
    the URL, method, ``--data*`` bodies and compression flags are ignored.
    """
    command = (text or "").strip()
    if not command.lstrip().startswith("curl"):
        raise CredentialsError("not a curl command: nothing to sign in with")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as error:
        raise CredentialsError(f"could not read the curl command: {error}") from error
    inline_headers, cookie_flag = _curl_header_args(argv)
    headers: dict[str, str] = {}
    for entry in inline_headers:
        name, sep, value = entry.partition(":")
        if not sep or not name.strip():
            continue
        headers[name.strip().lower()] = value.strip()
    if cookie_flag and "cookie" not in headers:
        headers["cookie"] = cookie_flag.strip()
    return headers


def parse_pasted_headers(text: str) -> dict[str, str]:
    """Parse either paste form: cURL when it starts with ``curl``, else a
    header block. Raises :class:`CredentialsError` when empty."""
    if not (text or "").strip():
        raise CredentialsError("paste the request headers first")
    if text.strip().lstrip().startswith("curl"):
        return parse_curl(text)
    return parse_header_block(text)


def build_browser_credentials(headers: dict[str, str]) -> dict[str, str]:
    """Turn parsed headers into ytmusicapi browser credentials.

    Requires a ``Cookie`` header, keeps ``x-goog-authuser`` (defaulting it
    to ``"0"``, the main account, when missing so brand-account pastes that
    carry another value keep working), and drops hop-by-hop headers the
    session must not replay.
    """
    lowered = {str(name).lower(): str(value) for name, value in headers.items()}
    if not lowered.get("cookie", "").strip():
        raise CredentialsError(
            "no Cookie header found: copy the headers of a request made "
            "while signed in to music.youtube.com"
        )
    credentials = dict(lowered)
    credentials.setdefault("x-goog-authuser", _DEFAULT_AUTHUSER)
    for name in list(credentials):
        if name.startswith("sec-") or name in _DROPPED_HEADERS:
            del credentials[name]
    for name, value in _BROWSER_DEFAULTS.items():
        credentials.setdefault(name, value)
    return credentials


# ---- validation (one cheap authenticated call, fakeable seam) ---------------


class BrowserClient(Protocol):
    """What validation and sync need from a ytmusicapi client. The real
    ``YTMusic`` satisfies this; tests inject a fake."""

    def get_library_playlists(self, limit: int | None = ...) -> list: ...


#: Client factory seam: ``factory(headers, user)`` builds an authenticated
#: client, where ``user`` is the brand-account id or None for the main
#: account (ytmusicapi's ``YTMusic(auth=..., user=...)``).
ClientFactory = Callable[[dict[str, str], str | None], BrowserClient]

_AUTH_FAILURE_HINTS = (
    "auth", "unauthor", "forbidden", "login", "sign in", "expired",
    "invalid cookie", "sapisid", "401", "403",
)


def validate_browser_credentials(credentials: dict[str, str],
                                 client_factory: ClientFactory,
                                 user: str | None = None) -> None:
    """Check browser credentials with one cheap authenticated call.

    Builds the client through ``client_factory`` and lists a single
    library playlist. Raises :class:`CredentialsExpired` when YouTube
    rejects the session and :class:`ValidationError` for anything else.
    Call from a worker thread: it performs network I/O.
    """
    try:
        client = client_factory(dict(credentials), user)
        client.get_library_playlists(limit=1)
    except CredentialsExpired:
        raise
    except ValidationError:
        raise
    except Exception as error:
        message = str(error).lower()
        if any(hint in message for hint in _AUTH_FAILURE_HINTS):
            raise CredentialsExpired(
                "Your sign-in expired. Sign in again.") from error
        raise ValidationError(f"could not check the sign-in: {error}") from error


def create_ytmusic_factory() -> ClientFactory:
    """The real factory: build ``ytmusicapi.YTMusic`` from credentials."""

    def factory(credentials: dict[str, str], user: str | None) -> BrowserClient:
        from ytmusicapi import YTMusic

        if user:
            return YTMusic(auth=credentials, user=user)
        return YTMusic(auth=credentials)

    return factory


# ---- redaction --------------------------------------------------------------


_MASKED_HEADERS = frozenset({"cookie", "authorization", "x-goog-page-hints"})
_MASKED_RE = re.compile(
    r"(?i)(cookie|authorization|x-goog-page-hints)\s*[:=]\s*"
    r"(\S[^\n;]*?)(?=\s*(?:[;\n]|$))")
_SAPISID_RE = re.compile(r"(?i)(SAPISIDHASH\s+\d+_)[0-9a-f]+")


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Copy ``headers`` with secret values masked, for logs."""
    redacted = dict(headers)
    for name in list(redacted):
        if name.lower() in _MASKED_HEADERS:
            redacted[name] = "***"
    return redacted


def redact_text(text: str) -> str:
    """Mask cookie/authorization values and SAPISID hashes in free text."""
    masked = _MASKED_RE.sub(lambda m: f"{m.group(1)}: ***", text or "")
    return _SAPISID_RE.sub(lambda m: f"{m.group(1)}***", masked)


# ---- yt-dlp cookie jar ------------------------------------------------------


def cookie_jar_path(runtime_dir: str | os.PathLike[str]) -> Any:
    """Where the transient jar lives: ``$XDG_RUNTIME_DIR/xx-radio/``."""
    from pathlib import Path

    return Path(runtime_dir) / "xx-radio" / COOKIE_JAR_NAME


def split_cookie_header(cookie_header: str) -> list[tuple[str, str]]:
    """Split a ``Cookie`` header into ``(name, value)`` pairs, minus
    ``LOGIN_INFO`` (PORT_SPEC.md 2.3)."""
    pairs: list[tuple[str, str]] = []
    for chunk in (cookie_header or "").split(";"):
        name, sep, value = chunk.partition("=")
        name = name.strip()
        if not sep or not name or name == LOGIN_INFO_COOKIE:
            continue
        pairs.append((name, value.strip()))
    return pairs


def cookie_header_to_netscape(cookie_header: str) -> str:
    """Render a ``Cookie`` header as Netscape jar text for yt-dlp."""
    lines = ["# Netscape HTTP Cookie File",
             "# xx-radio: written at resolve time, removed on sign-out"]
    for name, value in split_cookie_header(cookie_header):
        lines.append("\t".join(
            (COOKIE_JAR_DOMAIN, "TRUE", "/", "TRUE", "0", name, value)))
    return "\n".join(lines) + "\n"


def write_cookie_jar(cookie_header: str,
                     runtime_dir: str | os.PathLike[str]) -> Any:
    """Write the jar from the stored ``Cookie`` header (mode 0600).

    Only the resolver calls this, right before spawning yt-dlp, so the
    cookies rest on tmpfs only while signed in. Requires a Cookie header.
    """
    if not (cookie_header or "").strip():
        raise CredentialsError("no cookies stored to write")
    target = cookie_jar_path(runtime_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    text = cookie_header_to_netscape(cookie_header)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
    except BaseException:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    os.chmod(target, 0o600)
    return target


def clear_cookie_jar(runtime_dir: str | os.PathLike[str]) -> None:
    """Remove the jar; missing is fine (sign-out and service exit)."""
    from pathlib import Path

    try:
        Path(cookie_jar_path(runtime_dir)).unlink()
    except FileNotFoundError:
        pass


# ---- secret store -----------------------------------------------------------


class SecretStore(Protocol):
    """libsecret-shaped storage. Secrets never touch the disk."""

    def store(self, kind: str, account: str, secret: str) -> None: ...
    def lookup(self, kind: str, account: str) -> str | None: ...
    def remove(self, kind: str, account: str) -> None: ...
    def accounts(self, kind: str) -> list[str]: ...


class FakeSecretStore:
    """In-memory SecretStore for tests."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], str] = {}

    def store(self, kind: str, account: str, secret: str) -> None:
        self._items[(kind, account)] = secret

    def lookup(self, kind: str, account: str) -> str | None:
        return self._items.get((kind, account))

    def remove(self, kind: str, account: str) -> None:
        self._items.pop((kind, account), None)

    def accounts(self, kind: str) -> list[str]:
        return sorted(account for item_kind, account in self._items
                      if item_kind == kind)


class LibsecretStore:
    """The real store: ``gi.repository.Secret`` on a worker thread."""

    def __init__(self, collection: Any = None) -> None:
        self._collection = collection

    def _schema(self) -> Any:
        from gi.repository import Secret

        return Secret.Schema.new(
            CREDENTIALS_SCHEMA, Secret.SchemaFlags.NONE,
            {"kind": Secret.SchemaAttributeType.STRING,
             "account": Secret.SchemaAttributeType.STRING})

    def _attributes(self, kind: str, account: str) -> dict[str, str]:
        return {"kind": kind, "account": account}

    def _service(self) -> Any:
        from gi.repository import Secret

        try:
            service = Secret.Service.get_sync(
                Secret.ServiceFlags.NONE, None)
        except Exception as error:
            raise KeyringUnavailable("Signing in needs a keyring") from error
        if service is None:
            raise KeyringUnavailable("Signing in needs a keyring")
        return service

    def store(self, kind: str, account: str, secret: str) -> None:
        from gi.repository import Secret

        attributes = self._attributes(kind, account)
        try:
            Secret.password_store_sync(
                self._schema(), attributes,
                Secret.COLLECTION_DEFAULT, CREDENTIALS_SCHEMA,
                secret, None)
        except Exception as error:
            raise KeyringUnavailable("Signing in needs a keyring") from error

    def lookup(self, kind: str, account: str) -> str | None:
        from gi.repository import Secret

        try:
            return Secret.password_lookup_sync(
                self._schema(), self._attributes(kind, account), None)
        except Exception as error:
            raise KeyringUnavailable("Signing in needs a keyring") from error

    def remove(self, kind: str, account: str) -> None:
        from gi.repository import Secret

        try:
            Secret.password_clear_sync(
                self._schema(), self._attributes(kind, account), None)
        except Exception as error:
            raise KeyringUnavailable("Signing in needs a keyring") from error

    def accounts(self, kind: str) -> list[str]:
        from gi.repository import Secret

        service = self._service()
        try:
            items = service.search_sync(
                self._schema(), {"kind": kind},
                Secret.SearchFlags.ALL, None)
        except Exception as error:
            raise KeyringUnavailable("Signing in needs a keyring") from error
        names: list[str] = []
        for item in items or []:
            attributes = item.get_attributes() or {}
            if "account" in attributes:
                names.append(attributes["account"])
        return sorted(names)


# ---- OAuth device flow (own client id/secret) --------------------------------


@dataclass(frozen=True)
class DeviceCode:
    """One pending device authorization: show ``user_code`` and
    ``verification_url`` to the person, then poll."""

    device_code: str
    user_code: str
    verification_url: str
    expires_in: int = 1800
    interval: int = 5


class TokenResponse(Protocol):
    status_code: int

    def json(self) -> dict[str, Any]: ...


#: HTTP seam: ``post(url, data, headers)``. Tests inject a fake endpoint.
TokenPost = Callable[[str, dict[str, str], dict[str, str]],
                     TokenResponse]


def _oauth_headers() -> dict[str, str]:
    return {"User-Agent": OAUTH_USER_AGENT}


def _post_form(url: str, data: dict[str, str],
               headers: dict[str, str]) -> TokenResponse:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=encoded, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as reply:
        return _UrllibResponse(reply.status, reply.read().decode("utf-8"))


class _UrllibResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self._text = text

    def json(self) -> dict[str, Any]:
        return json.loads(self._text)


def _raise_for_client_error(payload: dict[str, Any], url: str) -> None:
    issue = str(payload.get("error", ""))
    if issue in ("unauthorized_client", "invalid_client"):
        raise BadOAuthClient(
            "That client id and secret were rejected. Check them and "
            "that the YouTube Data API is enabled.")
    if issue:
        raise OAuthError(
            f"the token endpoint refused the request ({issue}) at {url}")


def start_device_flow(client_id: str, client_secret: str,
                      post: TokenPost | None = None) -> DeviceCode:
    """Ask Google for a device code (step 1 of 2). The secret is sent only
    here, inside the token exchange, never logged. Call on a worker."""
    if not client_id.strip() or not client_secret.strip():
        raise CredentialsError("enter your own client id and secret first")
    send = post or _post_form
    try:
        reply = send(OAUTH_CODE_URL,
                     {"client_id": client_id.strip(), "scope": OAUTH_SCOPE},
                     _oauth_headers())
        payload = reply.json()
    except (BadOAuthClient, OAuthError):
        raise
    except Exception as error:
        raise OAuthError(
            f"could not reach the token endpoint: {error}") from error
    if reply.status_code == 401 or "error" in payload:
        _raise_for_client_error(payload, OAUTH_CODE_URL)
    try:
        url = payload.get("verification_url") or payload["verificationUrl"]
        return DeviceCode(
            device_code=payload["device_code"],
            user_code=payload["user_code"],
            verification_url=url,
            expires_in=int(payload.get("expires_in", 1800)),
            interval=int(payload.get("interval", 5)))
    except (KeyError, TypeError, ValueError) as error:
        raise OAuthError(
            "the token endpoint answered something unexpected") from error


def poll_device_token(client_id: str, client_secret: str,
                      device_code: str, interval: int = 5,
                      post: TokenPost | None = None,
                      sleep: Callable[[float], None] | None = None,
                      timeout_s: float = 600.0) -> dict[str, Any]:
    """Poll until the person approves the code (step 2 of 2). Returns the
    token dict to store under kind ``oauth``. Raises :class:`OAuthDenied`
    when they deny it or the code lapses. Call on a worker."""
    import time as _time

    send = post or _post_form
    rest = sleep or _time.sleep
    deadline = _time.monotonic() + max(timeout_s, interval)
    data = {"client_id": client_id.strip(),
            "client_secret": client_secret,
            "code": device_code,
            "grant_type": OAUTH_GRANT_TYPE}
    while True:
        try:
            reply = send(OAUTH_TOKEN_URL, dict(data), _oauth_headers())
            payload = reply.json()
        except (OAuthError, BadOAuthClient):
            raise
        except Exception as error:
            raise OAuthError(
                f"could not reach the token endpoint: {error}") from error
        issue = str(payload.get("error", ""))
        if reply.status_code == 401 or issue in (
                "unauthorized_client", "invalid_client"):
            _raise_for_client_error(payload, OAUTH_TOKEN_URL)
        if "access_token" in payload and "refresh_token" in payload:
            return dict(payload)
        if issue in ("authorization_pending", "slow_down"):
            if issue == "slow_down":
                interval += 5
            if _time.monotonic() + interval > deadline:
                raise OAuthDenied("the code expired before approval")
            rest(interval)
            continue
        if issue in ("access_denied", "expired_token"):
            raise OAuthDenied(
                "access was denied or the code expired; try again")
        if issue:
            raise OAuthError(
                f"the token endpoint refused the request ({issue})")
        raise OAuthError("the token endpoint answered something unexpected")
