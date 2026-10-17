"""Origin rules and 401/403 handling mirror EstateOrigin/UnauthorizedReason."""

from xx_audiobook import auth as auth_module
from xx_audiobook.auth import AuthAction


def test_api_base_appends_prefix():
    assert auth_module.api_base("https://door:8457") == \
        "https://door:8457/audiobook/"
    assert auth_module.api_base("https://door:8457/") == \
        "https://door:8457/audiobook/"


def test_origin_key_ignores_path_but_not_host_port():
    assert auth_module.origin_key("https://h:8457/audiobook/api/me") == \
        "https://h:8457"
    assert auth_module.origin_key("https://h:8457/x") != \
        auth_module.origin_key("https://other:8457/x")
    assert auth_module.same_origin("https://h:8457/audiobook/a",
                                   "https://h:8457/audiobook/")
    assert not auth_module.same_origin("https://evil:8457/a",
                                       "https://h:8457/audiobook/")


def test_problem_rejects_path_query_and_reserved_ports():
    assert auth_module.problem("") is not None
    assert auth_module.problem("https://door:8457/audiobook") is not None
    assert auth_module.problem("https://door:8457/?x=1") is not None
    assert auth_module.problem("https://door:8455") is not None
    assert auth_module.problem("https://door:8457") is not None
    assert auth_module.problem("gopher://door") is not None
    assert auth_module.problem("https://door:8443") is None


def test_parse_reason_top_level_and_nested():
    assert auth_module.parse_reason('{"reason":"expired"}') == "expired"
    assert auth_module.parse_reason(
        '{"detail":{"reason":"not_granted"}}') == "not_granted"
    assert auth_module.parse_reason('{"detail":"nope"}') is None
    assert auth_module.parse_reason("garbage") is None
    assert auth_module.parse_reason(None) is None
    assert auth_module.parse_reason("") is None


def test_action_matrix_matches_android():
    assert auth_module.action_for_reason("expired") == AuthAction.RENEW
    assert auth_module.action_for_reason("Expired") == AuthAction.RENEW
    for reason in ("disabled", "revoked", "account_disabled",
                   "session_revoked"):
        assert auth_module.action_for_reason(reason) == AuthAction.WIPE
    for reason in (None, "malformed", "not_a_person", "not_granted", ""):
        assert auth_module.action_for_reason(reason) == AuthAction.NONE


def test_hub_constants():
    assert auth_module.HUB_HOST == "127.0.0.1"
    assert auth_module.HUB_PORT == 17331
    assert auth_module.HUB_TIMEOUT_S == 0.25
    assert auth_module.NOT_GRANTED == "not_granted"


def test_estate_routes_not_in_client_contract():
    from xx_audiobook import api_routes as routes_module
    client = set(routes_module.CLIENT_ROUTES)
    for route in routes_module.ESTATE_ROUTES:
        assert route not in client
