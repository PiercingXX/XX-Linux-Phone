"""Fabric login + libsecret boundary: credentials go to xx-chat only, the
token carries display claims without verification, and secrets never land in
config.json or SQLite. No test touches a real keyring or session bus."""

import base64
import json
import os

os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent-test-bus")

from xx_vitals import prefs
from xx_vitals.sync import auth


def _b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _token(user_id="operator-7", exp=1800000000):
    claims = json.dumps({"user_id": user_id, "iat": 1700000000, "exp": exp})
    return f"v1.{_b64('keyid123')}.{_b64(claims)}.{_b64('signature-bytes')}"


def _transport_for(code, body):
    calls = []

    def transport(method, url, headers, body_bytes, timeout):
        calls.append((method, url, json.loads(body_bytes.decode("utf-8"))))
        return code, body

    transport.calls = calls
    return transport


def test_login_posts_credentials_to_xxchat_login_path():
    token = _token()
    transport = _transport_for(200, json.dumps({"token": token}))
    result = auth.fabric_login("https://chat.example.lan", "piercing", "secret", transport=transport)
    assert isinstance(result, auth.LoginSuccess)
    assert result.token == token
    method, url, body = transport.calls[0]
    assert method == "POST"
    assert url == "https://chat.example.lan/api/v1/fabric/login"
    assert body == {"username": "piercing", "password": "secret"}


def test_login_reads_expiry_and_identity_without_verifying():
    token = _token(exp=1800000000)
    transport = _transport_for(200, json.dumps({"token": token}))
    result = auth.fabric_login("https://chat.example.lan/", "u", "p", transport=transport)
    assert isinstance(result, auth.LoginSuccess)
    assert result.expires_at_ms == 1800000000 * 1000
    assert auth.token_user_id(result.token) == "operator-7"


def test_login_accepts_alternate_token_field_names():
    token = _token()
    transport = _transport_for(200, json.dumps({"access_token": token, "expires_in": 3600}))
    result = auth.fabric_login("https://chat.example.lan", "u", "p", transport=transport)
    assert isinstance(result, auth.LoginSuccess)
    assert result.token == token


def test_login_null_token_field_falls_through():
    token = _token()
    transport = _transport_for(200, json.dumps({"token": None, "access_token": token}))
    result = auth.fabric_login("https://chat.example.lan", "u", "p", transport=transport)
    assert isinstance(result, auth.LoginSuccess)
    assert result.token == token


def test_wrong_password_is_explicit_failure():
    transport = _transport_for(401, json.dumps({"detail": "no"}))
    result = auth.fabric_login("https://chat.example.lan", "u", "wrong", transport=transport)
    assert isinstance(result, auth.LoginFailure)
    assert result.code == 401


def test_response_without_token_is_failure():
    transport = _transport_for(200, json.dumps({"ok": True}))
    result = auth.fabric_login("https://chat.example.lan", "u", "p", transport=transport)
    assert isinstance(result, auth.LoginFailure)


def test_network_failure_reports_instead_of_throwing():
    def dead(_method, _url, _headers, _body, _timeout):
        raise OSError("network unreachable")

    result = auth.fabric_login("https://chat.example.lan", "u", "p", transport=dead)
    assert isinstance(result, auth.LoginFailure)
    assert result.code is None


def test_redirect_is_failure_and_never_followed():
    seen = []

    def redirecting(method, url, headers, body, timeout):
        seen.append(url)
        return 307, ""

    result = auth.fabric_login("https://chat.example.lan", "u", "p", transport=redirecting)
    assert isinstance(result, auth.LoginFailure)
    assert len(seen) == 1


def test_bad_host_url_rejected_without_touching_wire():
    transport = _transport_for(200, "{}")
    result = auth.fabric_login("nas.local:8080", "u", "p", transport=transport)
    assert isinstance(result, auth.LoginFailure)
    assert transport.calls == []


def test_malformed_tokens_yield_null_claims_never_throw():
    assert auth.token_expires_at_ms("garbage") is None
    assert auth.token_user_id("a.b.c.d") is None
    corrupt = _token().split(".")
    corrupt[2] = "!!!not-base64!!!"
    assert auth.token_user_id(".".join(corrupt)) is None
    assert auth.token_expires_at_ms(".".join(corrupt)) is None


def test_memory_backend_round_trip_and_sign_out():
    secrets = auth.MemorySecretBackend()
    assert secrets.lookup_token("https://nas:8443") is None
    secrets.store_token("https://nas:8443", "tok")
    assert secrets.lookup_token("https://nas:8443") == "tok"
    secrets.clear_token("https://nas:8443")
    assert secrets.lookup_token("https://nas:8443") is None


def test_token_lives_in_secrets_never_in_config():
    cfg = prefs.AppConfig(server_url="https://nas:8443")
    assert "token" not in prefs.AppConfig.__dataclass_fields__
    assert "token" not in cfg.to_dict()
    secrets = auth.MemorySecretBackend()
    secrets.store_token("https://nas:8443", "tok")
    creds = auth.load_credentials(cfg, secrets)
    assert creds.token == "tok"
    assert creds.can_sync is True
    assert creds.token_configured is True
    assert "tok" not in json.dumps(cfg.to_dict())


def test_unconfigured_host_cannot_sync():
    cfg = prefs.AppConfig()
    creds = auth.load_credentials(cfg, auth.MemorySecretBackend())
    assert creds.can_sync is False
    assert creds.token_configured is False


def test_relogin_flag_flows_from_config():
    cfg = prefs.AppConfig(server_url="https://nas:8443", relogin_required=True)
    secrets = auth.MemorySecretBackend()
    secrets.store_token("https://nas:8443", "tok")
    creds = auth.load_credentials(cfg, secrets)
    assert creds.relogin_required is True
    assert creds.can_sync is True


def test_host_parsing_never_invents_defaults():
    assert isinstance(auth.parse_host_url(""), auth.HostError)
    assert isinstance(auth.parse_host_url("nas.local:8080"), auth.HostError)
    assert isinstance(auth.parse_host_url("ftp://x"), auth.HostError)
    assert isinstance(auth.parse_host_url("http://host:notaport"), auth.HostError)
    parsed = auth.parse_host_url(" http://192.168.1.50:8080/ ")
    assert isinstance(parsed, auth.HostOk)
    assert parsed.url == "http://192.168.1.50:8080"
