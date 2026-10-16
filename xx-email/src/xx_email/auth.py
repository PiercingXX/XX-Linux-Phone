"""OAuth installed-app flow for XX Email (stdlib only, no GTK).

Ports AuthRepository/OAuthConfig (ref/xx-email data/auth/): the Android
custom scheme is not portable, so this uses the RFC 8252 loopback
redirect - the consent page opens in the user's browser and the code
lands on a one-shot http.server bound to 127.0.0.1 with an OS-chosen
port in a worker thread. PKCE S256 via hashlib+secrets, redirect state
validated against the outstanding request, code exchanged over urllib,
account email parsed locally from the ID token (no extra round-trip).
Refresh uses the same token endpoint; invalid_grant surfaces as
reauth, exactly like TokenCache.

Consequence: the user creates a Desktop-type OAuth client
(loopback-capable), not the Android type.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import queue
import secrets
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPES = "openid email profile https://www.googleapis.com/auth/gmail.modify"
CLIENT_ID_SUFFIX = "apps.googleusercontent.com"
CALLBACK_PATH = "/oauth2redirect"

TOKEN_TIMEOUT = 60


class ReauthNeeded(Exception):
    """The stored grant is dead (e.g. invalid_grant): sign in again."""


class AuthFlowError(Exception):
    """Loopback flow failed: denied consent, state mismatch, or timeout."""


def is_valid_client_id(client_id: str) -> bool:
    """BYO-client-ID setup check: must be a Google Desktop-type client ID."""
    candidate = (client_id or "").strip()
    return len(candidate) > len(CLIENT_ID_SUFFIX) and \
        candidate.endswith(CLIENT_ID_SUFFIX)


def generate_code_verifier() -> str:
    """Random PKCE verifier (43-128 unreserved chars, cf. CodeVerifierUtil)."""
    return secrets.token_urlsafe(64)


def code_challenge_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def build_auth_url(client_id: str, redirect_uri: str, state: str,
                   challenge: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    return AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)


def email_from_id_token(id_token: str | None) -> str | None:
    """Extract the account email locally from the ID token payload."""
    if not id_token:
        return None
    try:
        payload_b64 = id_token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(
            payload_b64 + padding).decode("utf-8"))
        email = payload.get("email") if isinstance(payload, dict) else None
        return email if isinstance(email, str) and email.strip() else None
    except (ValueError, IndexError, UnicodeDecodeError):
        return None


@dataclass
class TokenResponse:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    id_token: str | None
    raw: dict


def _post_token_endpoint(params: dict[str, str],
                         transport=None) -> dict:
    """POST a form to the token endpoint. transport(body) -> (status, json)."""
    body = urllib.parse.urlencode(params).encode("ascii")
    if transport is not None:
        return transport(body)
    request = urllib.request.Request(
        TOKEN_ENDPOINT, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request,
                                    timeout=TOKEN_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except ValueError:
            payload = {}
        error = payload.get("error", "") if isinstance(payload,
                                                       dict) else ""
        if error == "invalid_grant":
            raise ReauthNeeded(
                f"Stored grant rejected ({error}); sign in again.") from exc
        raise AuthFlowError(f"Token endpoint HTTP {exc.code}") from exc


def _parse_token_response(payload: dict) -> TokenResponse:
    if not isinstance(payload, dict) or "access_token" not in payload:
        error = payload.get("error", "unknown") \
            if isinstance(payload, dict) else "unknown"
        if error == "invalid_grant":
            raise ReauthNeeded(
                "Stored grant rejected (invalid_grant); sign in again.")
        raise AuthFlowError(f"Token endpoint error: {error}")
    expires = payload.get("expires_in")
    return TokenResponse(
        access_token=str(payload["access_token"]),
        refresh_token=payload.get("refresh_token"),
        expires_in=int(expires) if isinstance(expires, int) else None,
        id_token=payload.get("id_token"),
        raw=payload,
    )


def exchange_code(client_id: str, redirect_uri: str, code: str,
                  verifier: str, transport=None) -> TokenResponse:
    return _parse_token_response(_post_token_endpoint({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code": code,
        "code_verifier": verifier,
    }, transport))


def refresh_access_token(client_id: str, refresh_token: str,
                         transport=None) -> TokenResponse:
    return _parse_token_response(_post_token_endpoint({
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }, transport))


class LoopbackFlow:
    """One-shot OAuth receiver on 127.0.0.1 with an OS-chosen port.

    The browser step runs anywhere (real browser in production, a plain
    GET of redirect_uri + ?code=..&state=.. in tests). The serving
    thread never blocks the caller: wait_for_code() blocks the calling
    (worker/test) thread until the redirect arrives or times out.
    """

    def __init__(self, client_id: str, transport=None) -> None:
        if not is_valid_client_id(client_id):
            raise AuthFlowError("No valid OAuth client ID configured.")
        self.client_id = client_id
        self.transport = transport
        self.state = generate_state()
        self.verifier = generate_code_verifier()
        self._results: queue.Queue[tuple[str | None, str | None]] = \
            queue.Queue()
        flow = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                args = urllib.parse.parse_qs(parsed.query)
                code = args.get("code", [None])[0]
                returned_state = args.get("state", [None])[0]
                error = args.get("error", [None])[0]
                if error is not None:
                    flow._results.put((None, f"Authorization failed: {error}"))
                    self._page(400, "Sign-in failed; you can close this tab.")
                    flow._stop_listener()
                elif parsed.path != CALLBACK_PATH or code is None \
                        or not flow._state_ok(returned_state):
                    # Stray probe (favicon, captive portal, forged state):
                    # answer 400 but keep waiting for the real callback, so
                    # one stray GET cannot poison the single-shot queue.
                    self._page(400, "Sign-in failed; you can close this tab.")
                else:
                    flow._results.put((code, None))
                    self._page(200, "Signed in; you can close this tab.")
                    flow._stop_listener()

            def log_message(self, *args: object) -> None:
                pass

            def _page(self, status: int, text: str) -> None:
                body = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type",
                                 "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._stopping = threading.Event()
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self._thread.start()

    def _state_ok(self, returned_state: str | None) -> bool:
        """Constant-time check against the outstanding request state."""
        if not isinstance(returned_state, str):
            return False
        try:
            return hmac.compare_digest(returned_state, self.state)
        except (TypeError, ValueError):
            return False

    def _stop_listener(self) -> None:
        """Halt the one-shot listener after the first valid callback.

        Runs shutdown on a helper thread: shutdown() must be called from
        a thread other than the serve_forever thread (the handler runs
        inside it). Idempotent; wait_for_code() still closes the socket.
        """
        if self._stopping.is_set():
            return
        self._stopping.set()
        threading.Thread(target=self._server.shutdown, daemon=True).start()

    @property
    def redirect_uri(self) -> str:
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}{CALLBACK_PATH}"

    @property
    def authorization_url(self) -> str:
        return build_auth_url(self.client_id, self.redirect_uri,
                              self.state,
                              code_challenge_s256(self.verifier))

    def wait_for_code(self, timeout: float = 300.0) -> str:
        try:
            code, error = self._results.get(timeout=timeout)
        except queue.Empty as exc:
            raise AuthFlowError("Timed out waiting for sign-in.") from exc
        finally:
            self.stop()
        if error is not None:
            raise AuthFlowError(error)
        assert code is not None
        return code

    def complete(self, code: str) -> TokenResponse:
        """Exchange a received code (separated for testability)."""
        return exchange_code(self.client_id, self.redirect_uri, code,
                             self.verifier, self.transport)

    def stop(self) -> None:
        try:
            self._server.shutdown()
        except RuntimeError:
            pass
        self._server.server_close()
        self._thread.join(timeout=5.0)
