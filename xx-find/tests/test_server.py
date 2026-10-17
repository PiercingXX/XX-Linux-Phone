"""Server store + pure handler + tile math. No sockets in tests."""

from __future__ import annotations

import json

import pytest

from xx_find import crypto, protocol, server

NOW = 1_800_000_000


@pytest.fixture
def store():
    owned = server.Store()
    owned.set_owner_password("correct horse")
    return owned


def _report(device_id, counter, box_hex_owner_pub=None):
    if box_hex_owner_pub is None:
        pub, _ = crypto.generate_owner_box_keypair()
    else:
        pub = box_hex_owner_pub
    sealed = crypto.seal_report(b'{"lat": 47.0}', pub)
    return protocol.build_report_envelope(device_id, counter, NOW, sealed)


def test_pair_and_report_round_trip(store):
    token = store.pair_device("phone-1")
    assert store.check_device_token("phone-1", token) is True
    assert store.check_device_token("phone-1", "wrong") is False
    assert store.submit_report("phone-1", _report("phone-1", 1), NOW) == {
        "ok": True}
    assert store.devices["phone-1"]["last_report_epoch"] == NOW


def test_server_rejects_plaintext_location(store):
    token = store.pair_device("phone-1")
    del token
    bad = _report("phone-1", 1)
    bad["lat"] = 47.0
    with pytest.raises(server.ServerError):
        store.submit_report("phone-1", bad, NOW)


def test_command_queue_is_monotonic(store):
    store.pair_device("phone-1")
    _, signing_hex = crypto.generate_owner_signing_keypair()
    first = protocol.sign_envelope(
        protocol.build_command("phone-1", 1, NOW + 60, "lock"), signing_hex)
    assert store.queue_envelope("phone-1", first)["queued"] == 1
    with pytest.raises(server.ServerError):
        store.queue_envelope("phone-1", first)
    assert [c["cmd"]["counter"] for c in
            store.pending("phone-1", 0)] == [1]


def test_owner_login_and_session(store):
    assert store.check_owner_password("correct horse") is True
    assert store.check_owner_password("nope") is False
    token = store.open_session(NOW)
    assert store.check_session(token, NOW + 10) is True
    assert store.check_session(token, NOW + 99 * 3600) is False


def test_http_report_and_poll_flow(store):
    token = store.pair_device("phone-1")
    headers = {"x-device-token": token}
    status, ctype, text = server.handle_request(
        "POST", "/v1/report", headers,
        json.dumps({"device_id": "phone-1",
                    "report": _report("phone-1", 1)}), store, NOW)
    assert (status, ctype) == (200, "application/json")
    assert json.loads(text) == {"ok": True}
    status, _, text = server.handle_request(
        "GET", "/v1/commands?device=phone-1&since=0", headers, "", store,
        NOW)
    assert status == 200 and json.loads(text) == {"commands": []}


def test_http_rejects_unauthenticated_device(store):
    store.pair_device("phone-1")
    status, _, _ = server.handle_request(
        "POST", "/v1/report", {"x-device-token": "bad"},
        json.dumps({"device_id": "phone-1",
                    "report": _report("phone-1", 1)}), store, NOW)
    assert status == 401


def test_login_limiter_checked_before_password(store):
    bad = json.dumps({"password": "nope"})
    for _ in range(server.LOGIN_MAX_ATTEMPTS):
        status, _, _ = server.handle_request(
            "POST", "/login", {}, bad, store, NOW)
        assert status == 401
    good = json.dumps({"password": "correct horse"})
    status, _, _ = server.handle_request(
        "POST", "/login", {}, good, store, NOW + 1)
    assert status == 429


def test_http_ui_needs_owner_session(store):
    status, _, text = server.handle_request("GET", "/", {}, "", store,
                                            NOW)
    assert status == 200 and "owner login" in text
    session = store.open_session(NOW)
    status, _, text = server.handle_request(
        "GET", "/", {"x-session": session}, "", store, NOW)
    assert status == 200 and "OpenStreetMap" in text


def test_owner_ui_command_ttl_survives_idle_poll(store):
    import re

    from xx_find import schedule

    status, _, text = server.handle_request("GET", "/", {}, "", store,
                                            NOW)
    assert status == 200
    match = re.search(r"exp:\s*now\s*\+\s*(\d+)", text)
    assert match is not None
    ttl = int(match.group(1))
    assert ttl >= int(schedule.IDLE_POLL_S) + 60
    verify_hex, signing_hex = crypto.generate_owner_signing_keypair()
    cmd = protocol.build_command("phone-1", 1, NOW + ttl, "ring")
    envelope = protocol.sign_envelope(cmd, signing_hex)
    assert protocol.validate_command(envelope, verify_hex, 0, NOW) == cmd
    assert protocol.validate_command(
        envelope, verify_hex, 0, NOW + int(schedule.IDLE_POLL_S)) == cmd


def test_tile_math_known_values():
    assert server.latlon_to_tile(0.0, 0.0, 0) == (0, 0)
    assert server.latlon_to_tile(0.0, 0.0, 1) == (1, 1)
    x, _ = server.latlon_to_tile(47.3769, 8.5417, 12)
    assert x == 2145
    assert "{z}" in server.OSM_TILE_URL and "google" not in server.OSM_TILE_URL


def test_store_persists_ciphertext_only(tmp_path):
    path = tmp_path / "store.json"
    owned = server.Store(path)
    token = owned.pair_device("phone-1")
    del token
    envelope = _report("phone-1", 1)
    owned.submit_report("phone-1", envelope, NOW)
    text = path.read_text(encoding="utf-8")
    assert "box" in text
    assert '"lat"' not in text
