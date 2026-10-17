"""The account the person optionally signs in with (PORT_SPEC.md 2.6).

One optional account: anonymous by default, browser headers (recommended)
or OAuth with the person's own client id and secret (catalog only: yt-dlp
no longer accepts OAuth, so streams stay anonymous). This module is the
API the future Settings › Account UI drives; it owns no widgets.

All blocking work (validation, device-flow polling, libsecret round
trips) runs on the caller's worker thread; construct cheaply on the main
loop. ``xx_music`` stays source-agnostic: only this package knows
ytmusicapi shapes, and the client always arrives through the injected
``client_factory`` seam.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from xx_music.engine import EngineError, ErrorKind, LibrarySection, Rating
from xx_radio.youtube import auth

METHOD_ANONYMOUS = "anonymous"
METHOD_BROWSER = "browser"
METHOD_OAUTH = "oauth"

_DEFAULT_LABEL = "YouTube Music"

#: Engine rating to the word ytmusicapi's rate_song takes.
_RATING_WORDS = {
    Rating.LIKE: "LIKE",
    Rating.DISLIKE: "DISLIKE",
    Rating.NONE: "INDIFFERENT",
}

_AUTH_ERROR_HINTS = (
    "auth", "unauthor", "forbidden", "login", "sign in", "expired",
    "invalid cookie", "sapisid", "401", "403",
)


@dataclass(frozen=True)
class AccountState:
    """What Settings › Account shows. ``catalog_only`` is true for OAuth:
    library and ratings sync, but streams stay anonymous."""

    signed_in: bool
    method: str = METHOD_ANONYMOUS
    account_name: str = ""
    brand_user: str | None = None
    catalog_only: bool = False


def _engine_error(error: Exception) -> EngineError:
    if isinstance(error, EngineError):
        return error
    if any(hint in str(error).lower() for hint in _AUTH_ERROR_HINTS):
        return EngineError(ErrorKind.AUTH_EXPIRED, "sign-in expired")
    return EngineError(ErrorKind.OTHER, str(error)[:200])


def rating_word(rating: Rating) -> str:
    """Map an engine rating to ytmusicapi's ``rate_song`` word."""
    return _RATING_WORDS[rating]


class AccountManager:
    """Sign-in, sign-out and the signed-in data paths (library, history,
    ratings) over an injected client factory."""

    def __init__(self, secrets: auth.SecretStore,
                 runtime_dir: str | Path,
                 client_factory: auth.ClientFactory | None = None,
                 token_post: auth.TokenPost | None = None) -> None:
        self._secrets = secrets
        self._runtime_dir = Path(runtime_dir)
        self._client_factory = client_factory
        self._token_post = token_post

    # -- state ---------------------------------------------------------

    def state(self) -> AccountState:
        """Current account from the secret store alone (no I/O besides
        libsecret; call from a worker)."""
        browsers = self._secrets.accounts(auth.KIND_BROWSER)
        if browsers:
            label = browsers[0]
            return AccountState(True, METHOD_BROWSER, label,
                                self._brand_user(label))
        oauths = self._secrets.accounts(auth.KIND_OAUTH)
        if oauths:
            return AccountState(True, METHOD_OAUTH, oauths[0],
                                None, catalog_only=True)
        return AccountState(False)

    def _brand_user(self, account: str) -> str | None:
        try:
            stored = self._secrets.lookup(auth.KIND_BROWSER, account)
            user = (json.loads(stored or "{}")).get("user")
            return str(user) if user else None
        except (ValueError, AttributeError):
            return None

    def _stored_headers(self, account: str) -> dict[str, str]:
        stored = self._secrets.lookup(auth.KIND_BROWSER, account)
        if not stored:
            raise EngineError(ErrorKind.AUTH_REQUIRED, "not signed in")
        try:
            credentials = json.loads(stored)["headers"]
        except (ValueError, KeyError, TypeError) as error:
            raise EngineError(ErrorKind.AUTH_EXPIRED,
                              "stored sign-in is unreadable") from error
        return dict(credentials)

    def _client(self, headers: dict[str, str], user: str | None) -> Any:
        if self._client_factory is None:
            factory = auth.create_ytmusic_factory()
        else:
            factory = self._client_factory
        return factory(dict(headers), user)

    # -- browser sign-in -----------------------------------------------

    def sign_in_with_headers(self, pasted: str, account_name: str = "",
                             brand_user: str | None = None) -> AccountState:
        """Parse, validate, then store a browser-header paste. Raises
        CredentialsError (bad paste), ValidationError (rejected) or
        KeyringUnavailable. Call from a worker."""
        parsed = auth.parse_pasted_headers(pasted)
        credentials = auth.build_browser_credentials(parsed)
        auth.validate_browser_credentials(
            credentials, self._client_factory or auth.create_ytmusic_factory(),
            brand_user)
        label = account_name.strip() or _DEFAULT_LABEL
        payload = {"headers": credentials}
        if brand_user:
            payload["user"] = brand_user
        self._secrets.store(auth.KIND_BROWSER, label, json.dumps(payload))
        return AccountState(True, METHOD_BROWSER, label, brand_user)

    def sign_out(self) -> AccountState:
        """Forget every sign-in and remove the cookie jar. The person's
        own OAuth client id/secret (kind ``oauth-client``) is kept so
        they do not have to retype it."""
        for account in self._secrets.accounts(auth.KIND_BROWSER):
            self._secrets.remove(auth.KIND_BROWSER, account)
        for account in self._secrets.accounts(auth.KIND_OAUTH):
            self._secrets.remove(auth.KIND_OAUTH, account)
        auth.clear_cookie_jar(self._runtime_dir)
        return AccountState(False)

    # -- OAuth sign-in ---------------------------------------------------

    def remember_oauth_client(self, client_id: str,
                              client_secret: str) -> None:
        if not client_id.strip() or not client_secret.strip():
            raise auth.CredentialsError(
                "enter your own client id and secret first")
        self._secrets.store(auth.KIND_OAUTH_CLIENT, _DEFAULT_LABEL,
                            json.dumps({"client_id": client_id.strip(),
                                        "client_secret": client_secret}))

    def start_oauth(self, client_id: str,
                    client_secret: str) -> auth.DeviceCode:
        """Step 1: get the code the person approves. Call on a worker."""
        return auth.start_device_flow(client_id, client_secret,
                                      post=self._token_post)

    def finish_oauth(self, client_id: str, client_secret: str,
                     code: auth.DeviceCode, account_name: str = "",
                     sleep: Callable[[float], None] | None = None,
                     timeout_s: float = 600.0) -> AccountState:
        """Step 2: poll until approved, then store the token. The token
        gives catalog access only (streams stay anonymous)."""
        token = auth.poll_device_token(
            client_id, client_secret, code.device_code,
            interval=code.interval, post=self._token_post,
            sleep=sleep, timeout_s=timeout_s)
        label = account_name.strip() or _DEFAULT_LABEL
        self._secrets.store(auth.KIND_OAUTH, label, json.dumps(token))
        return AccountState(True, METHOD_OAUTH, label, None,
                            catalog_only=True)

    # -- cookie jar (resolver side) ---------------------------------------

    def ensure_cookie_jar(self) -> Path | None:
        """Write the jar from the stored browser cookies for one resolve.
        Only the resolver calls this; OAuth sign-ins have no cookies, so
        their streams stay anonymous. Returns the jar path or None."""
        current = self.state()
        if not current.signed_in or current.method != METHOD_BROWSER:
            return None
        headers = self._stored_headers(current.account_name)
        cookie = headers.get("cookie", "")
        if not cookie.strip():
            return None
        return Path(auth.write_cookie_jar(cookie, self._runtime_dir))

    def clear_cookie_jar(self) -> None:
        auth.clear_cookie_jar(self._runtime_dir)

    # -- signed-in data paths ----------------------------------------------

    def _signed_client(self) -> tuple[Any, str | None]:
        current = self.state()
        if not current.signed_in:
            raise EngineError(ErrorKind.AUTH_REQUIRED, "not signed in")
        if current.method == METHOD_BROWSER:
            headers = self._stored_headers(current.account_name)
            return self._client(headers, current.brand_user), current.brand_user
        stored = self._secrets.lookup(auth.KIND_OAUTH, current.account_name)
        if not stored:
            raise EngineError(ErrorKind.AUTH_REQUIRED, "not signed in")
        try:
            token = json.loads(stored)
        except ValueError as error:
            raise EngineError(ErrorKind.AUTH_EXPIRED,
                              "stored sign-in is unreadable") from error
        return self._client(token, None), None

    def fetch_library(self, section: LibrarySection,
                      limit: int = 25) -> list:
        """One library section through the signed-in client (raw JSON
        items; parse.py turns them into engine models in M1)."""
        client, _user = self._signed_client()
        method = {
            LibrarySection.PLAYLISTS: "get_library_playlists",
            LibrarySection.SONGS: "get_library_songs",
            LibrarySection.ALBUMS: "get_library_albums",
            LibrarySection.ARTISTS: "get_library_artists",
            LibrarySection.LIKED: "get_liked_songs",
            LibrarySection.HISTORY: "get_history",
        }[section]
        try:
            if section == LibrarySection.HISTORY:
                return list(getattr(client, method)())
            return list(getattr(client, method)(limit=limit))
        except Exception as error:
            raise _engine_error(error) from error

    def fetch_history(self) -> list:
        """Watch history through the signed-in client (raw JSON items)."""
        return self.fetch_library(LibrarySection.HISTORY)

    def set_rating(self, track_id: str, rating: Rating) -> None:
        """Sync one like/dislike to the signed-in library."""
        if not track_id:
            raise EngineError(ErrorKind.NOT_FOUND, "no song")
        client, _user = self._signed_client()
        try:
            client.rate_song(track_id, rating_word(rating))
        except Exception as error:
            raise _engine_error(error) from error
