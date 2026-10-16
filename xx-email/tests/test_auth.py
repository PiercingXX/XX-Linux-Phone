"""OAuth loopback flow tests: PKCE, state validation, and token endpoint.

Covers AuthRepository/OAuthConfig behavior as ported to the loopback
flow: S256 challenge, state checked against the outstanding request,
code exchange over the token endpoint, local email extraction from the
ID token, and invalid_grant -> ReauthNeeded. The browser step is faked
with a plain GET of the redirect URL (no network beyond 127.0.0.1).
"""

import base64
import json
import urllib.parse
import urllib.request

import pytest

from xx_email import auth
from xx_email.auth import (
    AuthFlowError,
    LoopbackFlow,
    ReauthNeeded,
    build_auth_url,
    code_challenge_s256,
    email_from_id_token,
    exchange_code,
    generate_code_verifier,
    generate_state,
    is_valid_client_id,
    refresh_access_token,
)

CLIENT_ID = "1234567890-abcdef.apps.googleusercontent.com"


def make_id_token(payload):
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(
        b"=").decode("ascii")
    return f"header.{raw}.signature"


def test_rfc7636_challenge_vector():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge_s256(verifier) == \
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_verifier_and_state_shapes():
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    assert all(c.isalnum() or c in "-_" for c in verifier)
    assert generate_state() != generate_state()
    challenge = code_challenge_s256(verifier)
    assert len(challenge) == 43
    assert "=" not in challenge


def test_client_id_must_be_google_desktop_id():
    assert is_valid_client_id(CLIENT_ID) is True
    assert is_valid_client_id("  " + CLIENT_ID + "  ") is True
    assert is_valid_client_id("") is False
    assert is_valid_client_id("not-a-client-id") is False
    assert is_valid_client_id("apps.googleusercontent.com") is False


def test_auth_url_carries_offline_consent_pkce():
    url = build_auth_url(CLIENT_ID, "http://127.0.0.1:9/oauth2redirect",
                         "state-1", "challenge-1")
    assert url.startswith(auth.AUTH_ENDPOINT + "?")
    args = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert args["client_id"] == [CLIENT_ID]
    assert args["response_type"] == ["code"]
    assert args["scope"] == [auth.SCOPES]
    assert "gmail.modify" in args["scope"][0]
    assert args["state"] == ["state-1"]
    assert args["code_challenge"] == ["challenge-1"]
    assert args["code_challenge_method"] == ["S256"]
    assert args["access_type"] == ["offline"]
    assert args["prompt"] == ["consent"]


def test_email_parsed_locally_from_id_token():
    token = make_id_token({"email": "me@example.com", "sub": "123"})
    assert email_from_id_token(token) == "me@example.com"
    assert email_from_id_token(make_id_token({"sub": "123"})) is None
    assert email_from_id_token(make_id_token({"email": "  "})) is None
    assert email_from_id_token("not-a-jwt") is None
    assert email_from_id_token(None) is None


def test_loopback_flow_accepts_fake_browser_redirect():
    seen = {}

    def transport(body):
        seen.update(urllib.parse.parse_qs(body.decode("ascii")))
        return {"access_token": "tok-1", "refresh_token": "rt-1",
                "expires_in": 3600,
                "id_token": make_id_token({"email": "me@example.com"})}

    flow = LoopbackFlow(CLIENT_ID, transport=transport)
    try:
        assert flow.redirect_uri.startswith("http://127.0.0.1:")
        assert f"state={flow.state}" in flow.authorization_url
        redirect = flow.redirect_uri + \
            f"?code=auth-code&state={flow.state}"
        with urllib.request.urlopen(redirect, timeout=10) as response:
            assert response.status == 200
        result = flow.complete(flow.wait_for_code(timeout=10))
    finally:
        flow.stop()
    assert result.access_token == "tok-1"
    assert result.refresh_token == "rt-1"
    assert email_from_id_token(result.id_token) == "me@example.com"
    assert seen["grant_type"] == ["authorization_code"]
    assert seen["code"] == ["auth-code"]
    assert seen["code_verifier"] == [flow.verifier]
    assert seen["redirect_uri"] == [flow.redirect_uri]
    assert seen["client_id"] == [CLIENT_ID]


def test_loopback_flow_ignores_stray_probe_then_accepts_real():
    flow = LoopbackFlow(CLIENT_ID)
    try:
        forged = flow.redirect_uri + "?code=auth-code&state=forged"
        with pytest.raises(Exception):
            urllib.request.urlopen(forged, timeout=10)
        redirect = flow.redirect_uri + \
            f"?code=auth-code&state={flow.state}"
        with urllib.request.urlopen(redirect, timeout=10) as response:
            assert response.status == 200
        assert flow.wait_for_code(timeout=10) == "auth-code"
    finally:
        flow.stop()


def test_loopback_state_check_is_constant_time():
    flow = LoopbackFlow(CLIENT_ID)
    try:
        assert flow._state_ok(flow.state) is True
        assert flow._state_ok("forged") is False
        assert flow._state_ok(None) is False
        assert flow._state_ok("") is False
    finally:
        flow.stop()


def test_loopback_flow_surfaces_provider_error():
    flow = LoopbackFlow(CLIENT_ID)
    try:
        redirect = flow.redirect_uri + \
            f"?error=access_denied&state={flow.state}"
        with urllib.request.urlopen(redirect, timeout=10):
            pass
    except Exception:
        pass
    finally:
        with pytest.raises(AuthFlowError):
            flow.wait_for_code(timeout=10)
        flow.stop()


def test_loopback_flow_times_out_without_browser():
    flow = LoopbackFlow(CLIENT_ID)
    try:
        with pytest.raises(AuthFlowError, match="Timed out"):
            flow.wait_for_code(timeout=0.2)
    finally:
        flow.stop()


def test_loopback_flow_rejects_bad_client_id():
    with pytest.raises(AuthFlowError):
        LoopbackFlow("bogus")


def test_exchange_posts_grant_and_verifier():
    seen = {}

    def transport(body):
        seen.update(urllib.parse.parse_qs(body.decode("ascii")))
        return {"access_token": "tok", "expires_in": 3600}

    result = exchange_code(CLIENT_ID, "http://127.0.0.1:9/oauth2redirect",
                           "code-1", "verifier-1", transport=transport)
    assert result.access_token == "tok"
    assert result.refresh_token is None
    assert seen["grant_type"] == ["authorization_code"]
    assert seen["code_verifier"] == ["verifier-1"]


def test_exchange_error_surfaces_without_reauth_flag():
    def transport(body):
        return {"error": "invalid_request"}

    with pytest.raises(AuthFlowError):
        exchange_code(CLIENT_ID, "http://x/", "c", "v", transport=transport)


def test_refresh_invalid_grant_becomes_reauth():
    def transport(body):
        params = urllib.parse.parse_qs(body.decode("ascii"))
        assert params["grant_type"] == ["refresh_token"]
        return {"error": "invalid_grant"}

    with pytest.raises(ReauthNeeded):
        refresh_access_token(CLIENT_ID, "rt-dead", transport=transport)


def test_refresh_success_returns_tokens():
    def transport(body):
        return {"access_token": "tok-2", "refresh_token": "rt-2",
                "expires_in": 3600}

    result = refresh_access_token(CLIENT_ID, "rt-1", transport=transport)
    assert (result.access_token, result.refresh_token) == ("tok-2", "rt-2")
