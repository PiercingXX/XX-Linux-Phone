"""Auth order: hub probe -> stored token -> Sign in (no provider on Linux).

Covers decide/resolve/login/renew, the untrusted-hub rule, adopt/wipe on
user switch, sign-out, and the secret stores. Estate HTTP goes to the
fake server on 127.0.0.1 only.
"""

import json

from xx_audiobook import auth as auth_module
from xx_audiobook.auth import (
    AuthCoordinator,
    Discovery,
    InMemoryCredentialStore,
    LoginError,
    LoginSuccess,
    Session,
    SessionStore,
    StoredCredentials,
)

DEFAULT_ORIGIN = "https://door.example:8457"


def _coordinator(provider=None, hub=None, store=None, probe_ran=None,
                 client=None):
    del provider
    store = store if store is not None else InMemoryCredentialStore()
    return AuthCoordinator(
        store=store, client=client, default_origin=DEFAULT_ORIGIN,
        probe=lambda: (probe_ran.append(True), hub)[1]
        if probe_ran is not None else hub)


def test_decide_order_is_hub_stored_login():
    coordinator = _coordinator()
    hub = Discovery("h-tok", "hub", 5000, "https://hub.example")
    stored = StoredCredentials("https://stored.example", "s-tok", "u1")

    first = coordinator.decide(hub, stored)
    assert isinstance(first, auth_module.HubUp)
    assert first.token == "h-tok"
    assert first.source == "hub"

    second = coordinator.decide(None, stored)
    assert isinstance(second, auth_module.UseStored)
    assert second.credentials.token == "s-tok"

    assert isinstance(coordinator.decide(None, None),
                      auth_module.NeedLogin)
    assert isinstance(
        coordinator.decide(None, StoredCredentials("", "tok")),
        auth_module.NeedLogin)


def test_hub_without_origin_uses_stored_then_default():
    coordinator = _coordinator()
    bare = Discovery("h-tok", "hub", 0, "")
    assert coordinator.decide(
        bare, None).origin == DEFAULT_ORIGIN
    stored = StoredCredentials("https://stored.example", "old", "u1")
    assert coordinator.decide(
        bare, stored).origin == "https://stored.example"


def test_resolve_prefers_hub_then_stored_then_login():
    ran = []
    store = InMemoryCredentialStore(
        StoredCredentials("https://stored.example", "s-tok", "u1",
                          username="alice", display_name="Alice"))
    hub = Discovery("h-tok", "hub", 5000, "")
    session = _coordinator(hub=hub, store=store, probe_ran=ran).resolve()
    assert session.source == "hub"
    assert session.token == "h-tok"
    assert session.display_name == "Alice"
    assert ran == [True]
    assert store.load().token == "h-tok"

    stored_only = _coordinator(store=InMemoryCredentialStore(
        StoredCredentials("https://door.example", "s-tok", "u1",
                          display_name="Alice"))).resolve()
    assert stored_only.source == "stored"
    assert stored_only.display_name == "Alice"

    assert _coordinator().resolve() is None


def test_probe_failure_falls_through_to_stored():
    def boom():
        raise OSError("hub down")

    store = InMemoryCredentialStore(
        StoredCredentials("https://door.example", "s-tok", "u1"))
    coordinator = AuthCoordinator(store=store,
                                  default_origin=DEFAULT_ORIGIN, probe=boom)
    assert coordinator.resolve().token == "s-tok"


def test_untrusted_hub_origin_never_replaces_stored():
    store = InMemoryCredentialStore(
        StoredCredentials("https://stored.example", "old", "u1"))
    coordinator = _coordinator(
        hub=Discovery("h-tok", "hub", 5000, "https://evil.example"),
        store=store)
    session = coordinator.resolve()
    assert session.origin == "https://stored.example"
    assert store.load().origin == "https://stored.example"

    fresh = _coordinator(
        hub=Discovery("h-tok", "hub", 5000,
                      "https://evil.example")).resolve()
    assert fresh.origin == DEFAULT_ORIGIN


def test_login_posts_client_and_never_stores_password(fake_server):
    store = InMemoryCredentialStore()
    coordinator = AuthCoordinator(
        store=store, default_origin=DEFAULT_ORIGIN,
        probe=lambda: None)
    result = coordinator.login(fake_server.origin, "alice", "hunter2")
    assert isinstance(result, LoginSuccess)
    assert result.session.token == "tok-1"
    assert result.session.username == "alice"

    _method, path, _headers, body, _query = fake_server.last_call(
        "POST", "/auth/login")
    assert path == "/auth/login"
    doc = json.loads(body)
    assert doc["client"] == "xx-audiobook"
    assert doc["username"] == "alice"

    saved = store.load()
    assert saved.token == "tok-1"
    assert "hunter2" not in repr(saved)
    assert "hunter2" not in result.session.token
    for value in (saved.origin, saved.token, saved.user_id, saved.username,
                  saved.display_name, saved.role):
        assert "hunter2" not in value


def test_login_errors_are_one_clear_line(fake_server):
    coordinator = _coordinator()
    fake_server.add_fault("POST", "/auth/login", 401,
                          {"error": "unauthorized"})
    assert coordinator.login(
        fake_server.origin, "a", "b").message == \
        "Wrong username or password."
    fake_server.add_fault("POST", "/auth/login", 401,
                          {"reason": "disabled"})
    assert coordinator.login(
        fake_server.origin, "a", "b").message == \
        "This estate account is disabled."
    fake_server.add_fault("POST", "/auth/login", 429, {})
    assert "Too many tries" in coordinator.login(
        fake_server.origin, "a", "b").message
    refused = _coordinator().login("http://127.0.0.1:1", "a", "b")
    assert isinstance(refused, LoginError)
    assert refused.message == auth_module.UNREACHABLE_MESSAGE
    assert _coordinator().login(
        "https://door.example:8454", "a", "b").message == \
        "That port is reserved."
    assert isinstance(
        _coordinator().login(fake_server.origin, "", ""), LoginError)


def test_renew_prefers_fresh_hub_token_then_refresh(fake_server):
    stale = Session("https://door.example", "old", "u1", 900, "stored")
    fresh = _coordinator(
        hub=Discovery("newer", "hub", 5000, "")).renew(stale)
    assert fresh.token == "newer"

    store = InMemoryCredentialStore()
    coordinator = AuthCoordinator(store=store,
                                  default_origin=DEFAULT_ORIGIN,
                                  probe=lambda: None)
    renewed = coordinator.renew(
        stale.__class__(fake_server.origin, "stale-tok", "u1", 900,
                        "stored"))
    assert renewed.token == "tok-2"
    assert renewed.expires_at == 9999999999
    _method, path, headers, _body, _query = fake_server.last_call(
        "POST", "/auth/refresh")
    assert path == "/auth/refresh"
    assert headers["authorization"] == "Bearer stale-tok"
    assert store.load().token == "tok-2"


def test_renew_returns_none_when_refresh_rejected(fake_server):
    fake_server.add_fault("POST", "/auth/refresh", 401,
                          {"reason": "expired"})
    coordinator = _coordinator()
    stale = Session(fake_server.origin, "stale-tok", "u1", 900, "stored")
    assert coordinator.renew(stale) is None


def test_adopt_wipes_on_user_switch_and_sign_out_clears():
    wiped = []
    owned = {}
    coordinator = _coordinator()
    sessions = SessionStore(
        coordinator, debug_override=lambda: None,
        wipe_local_state=lambda: wiped.append(True),
        owner_get=lambda: owned.get("uid"),
        owner_set=lambda uid: owned.update({"uid": uid}))
    sessions.adopt(Session("https://door.example", "a", "u-a", 0, "login"))
    assert wiped == []
    sessions.adopt(Session("https://door.example", "b", "u-b", 0, "login"))
    assert wiped == [True]
    assert owned["uid"] == "u-b"
    assert sessions.live_api_base() == "https://door.example/audiobook/"
    sessions.sign_out(wipe=False, message="bye")
    assert sessions.session is None
    assert sessions.sign_out_message == "bye"
    assert sessions.live_api_base() is None


def test_debug_override_is_verbatim_and_env_only(monkeypatch):
    coordinator = _coordinator()
    sessions = SessionStore(coordinator, debug_override=lambda: None)
    session = Session("https://door.example", "t", "u", 0, "stored")
    assert sessions.api_base_for(session) == \
        "https://door.example/audiobook/"
    monkeypatch.setenv("XX_AUDIOBOOK_API_BASE", "http://127.0.0.1:8750/")
    assert sessions.api_base_for(session) == \
        "https://door.example/audiobook/"


def test_session_never_logs_token():
    session = Session("https://door.example", "secret-token", "u1", 0,
                      "stored")
    assert "secret-token" not in repr(session)
    assert "secret-token" not in str(session)
    stored = StoredCredentials("https://door.example", "secret-token")
    assert "secret-token" not in repr(stored)


def test_memory_store_roundtrip():
    store = InMemoryCredentialStore()
    assert store.load() is None
    creds = StoredCredentials("https://door.example", "t", "u1")
    store.save(creds)
    assert store.load() == creds
    store.clear()
    assert store.load() is None
