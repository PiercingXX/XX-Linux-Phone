"""The 401/403 matrix, exactly as Android (AuthInterceptor semantics).

2xx marks access good; 403 not_granted marks access denied only (never
sign-out, never wipe); 401 expired renews plus exactly one retry, else
sign out without wipe; wipe reasons sign out AND wipe; malformed /
not_a_person / absent return to the caller, never wipe. Media auth only
ever goes to the estate host, never file:// or foreign hosts.
"""

from xx_audiobook import api as api_module
from xx_audiobook import auth as auth_module
from xx_audiobook.api import ApiError, AudiobookApi


class _Tokens(api_module.TokenSource):
    def __init__(self, token="tok",
                 base="https://door.example/audiobook/") -> None:
        self.session = (token, base) if token else None
        self.base = base
        self.rejected = []
        self.access = []
        self.renew_calls = 0
        self.renew_to = "fresh"

    def current(self):
        return self.session

    def renew(self, stale_token):
        self.renew_calls += 1
        if self.renew_to is None:
            return None
        self.session = (self.renew_to, self.base)
        return self.session

    def on_rejected(self, action, reason):
        self.rejected.append((action, reason))

    def on_access(self, granted):
        self.access.append(granted)


def _spy_client(script, tokens):
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(dict(headers))
        code, text = script.pop(0)
        return (code, text)

    return AudiobookApi("https://door.example/audiobook/", "tok",
                        transport=transport, tokens=tokens), calls


def test_success_marks_access_good():
    tokens = _Tokens()
    client, _calls = _spy_client([(200, '{"user_id":"u"}')], tokens)
    assert client.me().user_id == "u"
    assert tokens.access == [True]
    assert tokens.rejected == []
    assert tokens.renew_calls == 0


def test_403_not_granted_is_access_only():
    tokens = _Tokens()
    client, _calls = _spy_client([
        (403, '{"detail":"no grant","reason":"not_granted"}'),
        (403, '{"detail":"nope","reason":"something_new"}'),
        (200, '{"user_id":"u"}')], tokens)
    with _raises(403):
        client.me()
    assert tokens.access == [False]
    with _raises(403):
        client.me()
    assert tokens.access == [False]
    client.me()
    assert tokens.access == [False, True]
    assert tokens.rejected == []
    assert tokens.renew_calls == 0


class _raises:
    def __init__(self, code) -> None:
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _tb):
        assert isinstance(exc, ApiError.Http), exc
        assert exc.code == self.code, exc.code
        return True


def test_expired_renews_and_retries_once():
    tokens = _Tokens()
    client, calls = _spy_client([
        (401, '{"detail":"expired","reason":"expired"}'),
        (200, '{"user_id":"u"}')], tokens)
    assert client.me().user_id == "u"
    assert calls[0]["Authorization"] == "Bearer tok"
    assert calls[1]["Authorization"] == "Bearer fresh"
    assert tokens.renew_calls == 1
    assert tokens.rejected == []


def test_expired_without_renewal_signs_out_without_wipe():
    tokens = _Tokens()
    tokens.renew_to = None
    client, _calls = _spy_client(
        [(401, '{"reason":"expired"}')], tokens)
    with _raises(401):
        client.me()
    assert tokens.rejected == [("renew", "expired")]
    assert tokens.renew_calls == 1


def test_wipe_reasons_wipe():
    for reason in ("disabled", "revoked", "account_disabled",
                   "session_revoked"):
        tokens = _Tokens()
        client, _calls = _spy_client(
            [(401, '{"detail":"no","reason":"%s"}' % reason)], tokens)
        with _raises(401):
            client.me()
        assert tokens.rejected == [("wipe", reason)], reason
        assert tokens.renew_calls == 0


def test_other_or_absent_reasons_never_wipe():
    for body in ('{"error":"unauthorized"}', '{"reason":"malformed"}',
                 '{"reason":"not_a_person"}', ""):
        tokens = _Tokens()
        client, _calls = _spy_client([(401, body)], tokens)
        with _raises(401):
            client.me()
        assert tokens.rejected == [], body
        assert tokens.renew_calls == 0


def test_reason_parsing_handles_nesting_and_case():
    assert auth_module.parse_reason(
        '{"detail":{"reason":"session_revoked"}}') == "session_revoked"
    assert auth_module.action_for_reason(
        "account_disabled") == auth_module.AuthAction.WIPE
    assert auth_module.action_for_reason(None) == auth_module.AuthAction.NONE
    assert auth_module.parse_reason("<html>") is None
    assert auth_module.parse_reason(
        '{"reason":" Expired "}') == "Expired"
    assert auth_module.action_for_reason(
        " Expired ") == auth_module.AuthAction.RENEW


def test_session_store_rejects_like_android(fake_server):
    coordinator = auth_module.AuthCoordinator(
        default_origin="https://door.example")
    sessions = auth_module.SessionStore(
        coordinator, debug_override=lambda: None)
    sessions.adopt(auth_module.Session("https://door.example", "t", "u",
                                       0, "stored"))
    sessions.on_rejected("wipe", "disabled")
    assert sessions.session is None
    assert sessions.sign_out_message == \
        "This device was signed out of the estate."


def test_session_store_access_flag_tracks_403(fake_server):
    coordinator = auth_module.AuthCoordinator(
        default_origin="https://door.example")
    sessions = auth_module.SessionStore(
        coordinator, debug_override=lambda: None)
    sessions.adopt(auth_module.Session("https://door.example", "t", "u",
                                       0, "stored"))
    assert sessions.access_denied is False
    sessions.on_access(False)
    assert sessions.access_denied is True
    sessions.on_access(True)
    assert sessions.access_denied is False
    assert sessions.session is not None


def test_401_matrix_against_fake_server(fake_server):
    base = fake_server.api_base

    def client_for(tokens):
        return AudiobookApi(base, "tok", tokens=tokens)

    tokens = _Tokens("tok", base)
    fake_server.queue(
        "GET", "/audiobook/api/me", 401, '{"reason":"expired"}')
    fake_server.queue("GET", "/audiobook/api/me", 200,
                      '{"user_id":"u-1","username":"a",'
                      '"display_name":"A","is_admin":false}')
    assert client_for(tokens).me().user_id == "u-1"
    assert tokens.renew_calls == 1

    stuck = _Tokens("tok", base)
    stuck.renew_to = None
    fake_server.queue(
        "GET", "/audiobook/api/me", 401, '{"reason":"expired"}')
    try:
        client_for(stuck).me()
        raise AssertionError("expected 401")
    except ApiError.Http as exc:
        assert exc.code == 401
    assert stuck.rejected == [("renew", "expired")]

    doomed = _Tokens("tok", base)
    fake_server.queue(
        "GET", "/audiobook/api/me", 401, '{"reason":"session_revoked"}')
    try:
        client_for(doomed).me()
        raise AssertionError("expected 401")
    except ApiError.Http as exc:
        assert exc.code == 401
    assert doomed.rejected == [("wipe", "session_revoked")]

    denied = _Tokens("tok", base)
    fake_server.queue(
        "GET", "/audiobook/api/me", 403,
        '{"detail":"no grant","reason":"not_granted"}')
    try:
        client_for(denied).me()
        raise AssertionError("expected 403")
    except ApiError.Http as exc:
        assert exc.reason == "not_granted"
        assert exc.message == api_module.NOT_GRANTED_MESSAGE
    assert denied.access == [False]
    assert denied.rejected == []


def test_media_auth_only_for_the_estate_host():
    assert auth_module.same_origin(
        "https://door.example/audiobook/api/items/a/files/f",
        "https://door.example/audiobook/")
    assert not auth_module.same_origin("file:///books/a.m4b",
                                       "https://door.example/audiobook/")
    assert not auth_module.same_origin(
        "https://evil.example/audiobook/api/items/a/files/f",
        "https://door.example/audiobook/")
    assert not auth_module.same_origin(
        "http://door.example:9999/audiobook/a",
        "https://door.example/audiobook/")
