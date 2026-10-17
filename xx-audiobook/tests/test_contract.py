"""Contract test: the client route table matches openapi.json exactly.

Linux mirror of the Android data/ContractTest.kt: every route the client
speaks must exist exactly once in the server contract with the used method.
The contract is vendored at contracts/openapi.json (copied from the
server's openapi.json; refresh it when the server contract changes).
"""

from __future__ import annotations

import json
from pathlib import Path

from xx_audiobook import api_routes as routes_module

_HERE = Path(__file__).resolve()


OPENAPI = _HERE.parent.parent / "contracts" / "openapi.json"


def _load_openapi():
    return json.loads(OPENAPI.read_text(encoding="utf-8"))


def test_every_client_route_exists_once_with_method():
    spec = _load_openapi()
    defined: dict[tuple[str, str], int] = {}
    for path, operations in spec["paths"].items():
        for method in operations:
            if method.lower() in ("get", "post", "put", "patch", "delete",
                                  "head", "options"):
                key = (method.upper(), path)
                defined[key] = defined.get(key, 0) + 1
    missing = [route for route in routes_module.CLIENT_ROUTES
               if route not in defined]
    assert missing == []
    duplicates = [route for route, count in defined.items() if count > 1]
    assert duplicates == []


def test_client_routes_are_not_admin_paths():
    admin = [route for route in routes_module.CLIENT_ROUTES
             if route[1].startswith("/api/admin/")]
    assert admin == []


def test_estate_routes_are_not_in_openapi():
    spec = _load_openapi()
    flat = {(method.upper(), path)
            for path, operations in spec["paths"].items()
            for method in operations}
    for route in routes_module.ESTATE_ROUTES:
        assert route not in flat


def test_key_dto_required_fields_present():
    spec = _load_openapi()
    schemas = spec["components"]["schemas"]
    for name in ("PlayOut", "SyncIn", "ProgressIn", "UserSettings",
                 "BookmarkIn", "ReviewIn", "CastPlayIn"):
        assert name in schemas, name
    assert "position_s" in schemas["SyncIn"]["properties"]
    assert "position_s" in schemas["ProgressIn"]["properties"]
    assert "stars" in schemas["ReviewIn"]["properties"]
    assert set(schemas["ReviewIn"].get("required", [])) == {"stars"}
