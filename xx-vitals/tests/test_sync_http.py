"""Port of CleartextPolicyTest + HttpSyncApiTest (fake transport, no sockets)."""

from xx_vitals import sync_wire
from xx_vitals.sync import http as http_guard
from xx_vitals.sync.api import AuthExpired, Ok, Rejected, Transient, VolumeFull
from xx_vitals.sync.http_api import HttpSyncApi


def test_configured_host_with_port_allowed():
    assert http_guard.is_cleartext_allowed(
        "192.168.1.50", 8080, {"http://192.168.1.50:8080"})
    assert sync_wire.is_cleartext_allowed(
        "192.168.1.50", 8080, {"http://192.168.1.50:8080"})


def test_bare_hostname_allows_any_port():
    assert http_guard.is_cleartext_allowed("nas.local", 9999, {"nas.local"})


def test_different_port_refused():
    assert not http_guard.is_cleartext_allowed(
        "192.168.1.50", 9090, {"http://192.168.1.50:8080"})


def test_unconfigured_host_refused():
    assert not http_guard.is_cleartext_allowed(
        "evil-device.lan", 80, {"nas.local:8080"})


def test_empty_config_refuses():
    assert not http_guard.is_cleartext_allowed("nas.local", 8080, set())


def test_host_matching_case_insensitive():
    assert http_guard.is_cleartext_allowed("NAS.LOCAL", 8080, {"nas.local"})


def test_https_always_allowed():
    assert http_guard.check_url("https://nas:8443/v1/daily", set())
    assert not http_guard.check_url("http://nas:8080/v1/daily", set())
    assert http_guard.check_url("http://nas:8080/v1/daily", {"nas:8080"})


def _api(script, recorded, allowed=None, auth_calls=None):
    def transport(method, url, headers, body):
        recorded.append((method, url, headers, body))
        return script.pop(0)

    return HttpSyncApi(
        base_url="http://nas:8080", token_provider=lambda: "fabric-token",
        allowed_cleartext_hosts=lambda: ({"nas:8080"} if allowed is None else allowed),
        on_auth_expired=(lambda: auth_calls.append(1)) if auth_calls is not None else None,
        transport=transport,
    )


def test_post_weight_bearer_and_iso_body():
    recorded = []
    api = _api([(200, '{"received_at":"2026-08-01T08:00:00+00:00"}')], recorded)
    result = api.post_weight("2026-08-01T08:00:00.000Z", 80.5)
    assert isinstance(result, Ok)
    method, url, headers, body = recorded[0]
    assert method == "POST" and url.endswith("/v1/weight")
    assert headers["Authorization"] == "Bearer fabric-token"
    assert '"measured_at"' in body.decode() and '"weight_kg"' in body.decode()


def test_patch_and_delete_hit_id_routes():
    recorded = []
    api = _api([(200, '{"updated":true}'), (200, '{"deleted":true}')], recorded)
    api.patch_weight(42, "2026-08-01T09:00:00.000Z", 81.0)
    api.delete_weight(7)
    assert recorded[0][0] == "PATCH" and recorded[0][1].endswith("/v1/weight/42")
    assert recorded[1][0] == "DELETE" and recorded[1][1].endswith("/v1/weight/7")


def test_401_maps_to_auth_and_fires_callback():
    recorded, auth_calls = [], []
    api = _api([(401, '{"detail":"bad token"}')], recorded, auth_calls=auth_calls)
    assert isinstance(api.post_weight("2026-08-01T08:00:00.000Z", 80.0), AuthExpired)
    assert auth_calls == [1]


def test_507_maps_to_volume_full():
    api = _api([(507, "")], [])
    assert isinstance(api.post_weight("2026-08-01T08:00:00.000Z", 80.0), VolumeFull)


def test_transport_failure_is_transient():
    def failing(method, url, headers, body):
        raise ConnectionError("offline")

    api = HttpSyncApi(
        base_url="http://nas:8080", token_provider=lambda: "t",
        allowed_cleartext_hosts=lambda: {"nas:8080"}, transport=failing)
    assert isinstance(api.post_weight("2026-08-01T08:00:00.000Z", 80.0), Transient)


def test_malformed_changes_body_is_transient():
    api = _api([(200, "<html>not json</html>")], [])
    result = api.pull_changes("2026-08-01T00:00:00+00:00")
    assert isinstance(result, Transient)
    assert "unparseable" in (result.reason or "")


def test_cleartext_denied_before_socket():
    recorded = []
    api = _api([(200, "{}")], recorded, allowed=set())
    result = api.post_weight("2026-08-01T08:00:00.000Z", 80.0)
    assert isinstance(result, Transient)
    assert "cleartext" in (result.reason or "")
    assert recorded == []


def test_cleartext_denied_on_reads_without_touching_wire():
    recorded = []
    api = _api([(200, "{}")], recorded, allowed={"http://some-other-nas.lan:9999"})
    result = api.fetch_daily("2026-08-01", "2026-08-02")
    assert isinstance(result, Transient)
    assert "cleartext" in (result.reason or "")
    assert recorded == []


def test_loopback_cleartext_allowed_only_when_configured():
    assert http_guard.check_url("http://127.0.0.1:8080/v1/daily", {"127.0.0.1:8080"})
    assert not http_guard.check_url("http://127.0.0.1:8080/v1/daily", set())
    assert not http_guard.check_url("http://127.0.0.1:9090/v1/daily", {"127.0.0.1:8080"})
    assert http_guard.check_url("http://localhost:8080/v1/daily", {"localhost"})
    assert not http_guard.check_url("http://evil-device.lan/v1/daily", {"localhost"})


def test_status_taxonomy_matches_engine():
    assert isinstance(Ok(None), Ok)
    assert isinstance(Rejected(422, "x"), Rejected)
    assert isinstance(Transient("x"), Transient)


def test_fetch_batches_returns_rows_newest_first():
    recorded = []
    body = ('{"batches": [{"id": 9, "kind": "manual", '
            '"started_at": "2026-10-01T08:00:00+00:00", "finished_at": null, '
            '"rows_read": 3, "rows_inserted": 3, "rows_skipped": 0, '
            '"status": "ok", "error": null}]}')
    api = _api([(200, body)], recorded)
    result = api.fetch_batches()
    assert isinstance(result, Ok)
    assert recorded[0][0] == "GET" and recorded[0][1].endswith("/v1/batches")
    assert result.value[0]["kind"] == "manual"


def test_fetch_batches_401_and_507_surface():
    recorded, auth_calls = [], []
    api = _api([(401, "{}")], recorded, auth_calls=auth_calls)
    assert isinstance(api.fetch_batches(), AuthExpired)
    assert auth_calls == [1]
    assert isinstance(_api([(507, "")], []).fetch_batches(), VolumeFull)


def test_fetch_batches_garbage_is_transient():
    assert isinstance(_api([(200, "nope")], []).fetch_batches(), Transient)
    assert isinstance(_api([(200, '{"batches": {}}')], []).fetch_batches(), Transient)


def test_find_weight_id_window_spans_plus_minus_one_day():
    import json

    from xx_vitals.sync.api import from_iso

    recorded = []
    body = json.dumps({"rows": []})
    api = _api([(200, body)], recorded)
    at = from_iso("2026-08-01T08:00:00+00:00")
    assert isinstance(api.find_weight_id(at), Ok)
    method, url, _headers, _body = recorded[0]
    assert method == "GET" and "/v1/weight?from=" in url and "&to=" in url
    assert "from=2026-07-31" in url and "to=2026-08-02" in url


def test_find_weight_id_exact_match_wins():
    import json

    recorded = []
    body = json.dumps({"rows": [
        {"id": 55, "measured_at": "2026-08-01T06:00:00+00:00"},
        {"id": 56, "measured_at": "2026-08-01T08:00:00+00:00"},
        {"id": 57, "measured_at": "2026-08-01T20:00:00+00:00"},
    ]})
    api = _api([(200, body)], recorded)
    from xx_vitals.sync.api import from_iso

    found = api.find_weight_id(from_iso("2026-08-01T08:00:00+00:00"))
    assert isinstance(found, Ok) and found.value == 56


def test_find_weight_id_ignores_sub_second_neighbour():
    recorded = []
    body = '{"rows":[{"id":56,"measured_at":"2026-08-01T08:00:00.250000+00:00"}]}'
    api = _api([(200, body)], recorded)
    from xx_vitals.sync.api import from_iso

    found = api.find_weight_id(from_iso("2026-08-01T08:00:00+00:00"))
    assert isinstance(found, Ok) and found.value is None


def test_find_weight_id_ambiguous_millis_match_is_no_match():
    recorded = []
    body = '{"rows":[{"id":56,"measured_at":"2026-08-01T08:00:00+00:00"},' \
        '{"id":57,"measured_at":"2026-08-01T08:00:00+00:00"}]}'
    api = _api([(200, body)], recorded)
    from xx_vitals.sync.api import from_iso

    found = api.find_weight_id(from_iso("2026-08-01T08:00:00+00:00"))
    assert isinstance(found, Ok) and found.value is None


def test_fetch_daily_maps_rows():
    recorded = []
    body = ('{"rows": [{"source_id": 3, "date": "2026-08-01", "steps": 9000, '
            '"effort_points": 90.4, "derived_mask": 2, "batch_id": 11}]}')
    api = _api([(200, body)], recorded)
    result = api.fetch_daily("2026-08-01", "2026-08-01")
    assert isinstance(result, Ok)
    assert recorded[0][0] == "GET" and "/v1/daily?from=" in recorded[0][1]
    day = result.value[0]
    assert (day.date, day.steps, day.effort_points, day.derived_mask) == (
        "2026-08-01", 9000, 90, 2)


def test_fetch_daily_rejects_and_garbage_map():
    assert isinstance(
        _api([(422, '{"detail":"nope"}')], []).fetch_daily("2026-08-01", "2026-08-02"),
        Rejected)
    assert isinstance(
        _api([(200, "<html>")], []).fetch_daily("2026-08-01", "2026-08-02"),
        Transient)


def test_fetch_activities_maps_rows():
    recorded = []
    body = ('{"rows": [{"source_id": 4, "type": "walking", '
            '"started_at": "2026-08-01T07:30:00+00:00", '
            '"ended_at": "2026-08-01T08:10:00+00:00", '
            '"effort_points": 45.0, "active_min": 40, "steps": 4200, '
            '"batch_id": 11}]}')
    api = _api([(200, body)], recorded)
    result = api.fetch_activities("2026-08-01", "2026-08-01")
    assert isinstance(result, Ok)
    assert recorded[0][0] == "GET" and "/v1/activities?from=" in recorded[0][1]
    walk = result.value[0]
    assert (walk.type, walk.steps, walk.effort_points) == ("walking", 4200, 45)


def test_batch_stamps_drop_zero_ids():
    recorded = []
    body = ('{"batches": [{"id": 0, "started_at": "2026-08-01T05:00:00+00:00"}, '
            '{"id": 11, "started_at": "2026-08-01T05:00:00+00:00"}]}')
    api = _api([(200, body)], recorded)
    result = api.fetch_batch_stamps()
    assert isinstance(result, Ok)
    assert [stamp.id for stamp in result.value] == [11]
