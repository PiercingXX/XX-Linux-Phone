"""M1 contract depth: DTO constructors match openapi required fields.

The vendored contracts/openapi.json is always present, so nothing here
skips. Request DTOs must send only documented keys (the server answers
422 otherwise); response DTOs must carry every required server field.
"""

import dataclasses
import json
from pathlib import Path

from xx_audiobook import api as api_module
from xx_audiobook import api_routes as routes_module

OPENAPI = Path(__file__).parent.parent / "contracts" / "openapi.json"


def _spec():
    assert OPENAPI.is_file(), "vendored openapi.json is missing"
    return json.loads(OPENAPI.read_text(encoding="utf-8"))


def _schemas():
    return _spec()["components"]["schemas"]


REQUESTS = [
    (api_module.PlayRequest, "PlayIn"),
    (api_module.SyncRequest, "SyncIn"),
    (api_module.ProgressPut, "ProgressIn"),
    (api_module.BookmarkCreate, "BookmarkIn"),
    (api_module.ReviewPut, "ReviewIn"),
    (api_module.CastPlay, "CastPlayIn"),
    (api_module.CastTransport, "TransportIn"),
    (api_module.UserSettingsPut, "UserSettingsIn"),
]

RESPONSES = [
    (api_module.PlayResponse, "PlayOut"),
    (api_module.UserSettings, "UserSettings"),
    (api_module.Bookmark, "Bookmark"),
    (api_module.Review, "Review"),
    (api_module.Progress, "ProgressWrite"),
    (api_module.Me, "Me"),
]


def test_contract_file_is_vendored_with_no_skips():
    spec = _spec()
    assert len(spec["paths"]) == 88
    assert "UserSettings" in spec["components"]["schemas"]


def test_request_dtos_match_server_models():
    schemas = _schemas()
    problems = []
    for dto_cls, name in REQUESTS:
        schema = schemas[name]
        props = set(schema.get("properties", {}))
        fields = {f.name for f in dataclasses.fields(dto_cls)}
        for required in schema.get("required", []):
            if required not in fields:
                problems.append(
                    f"{name}.{required}: required, missing in DTO")
        for key in dto_cls().to_dict():
            if key not in props:
                problems.append(
                    f"{name}.{key}: sent by the client, unknown "
                    "to the server (422)")
        for key in fields:
            if key not in props:
                problems.append(
                    f"{name}.{key}: DTO field unknown to the server")
    assert problems == []


def test_response_dtos_carry_required_fields():
    schemas = _schemas()
    problems = []
    for dto_cls, name in RESPONSES:
        schema = schemas[name]
        props = set(schema.get("properties", {}))
        fields = {f.name for f in dataclasses.fields(dto_cls)}
        for required in schema.get("required", []):
            if required not in fields:
                problems.append(
                    f"{name}.{required}: required, missing in DTO")
        for field_name in fields:
            if field_name not in props:
                problems.append(
                    f"{name}.{field_name}: DTO field unknown "
                    "to the server")
    assert problems == []


def test_seven_named_dtos_cover_contract_shapes():
    schemas = _schemas()
    for name in ("PlayOut", "SyncIn", "ProgressIn", "UserSettings",
                 "BookmarkIn", "ReviewIn", "CastPlayIn"):
        assert name in schemas, name
    assert set(schemas["ReviewIn"].get("required", [])) == {"stars"}
    assert set(schemas["BookmarkIn"].get("required", [])) == {
        "item_id", "position_s"}
    assert set(schemas["CastPlayIn"].get("required", [])) == {
        "entity_id", "item_id"}
    assert set(schemas["SyncIn"].get("required", [])) == {"position_s"}
    assert set(schemas["ProgressIn"].get("required", [])) == {"position_s"}


def test_server_examples_decode_into_dtos():
    schemas = _schemas()
    decoded = 0
    assert api_module.PlayResponse.from_dict(
        schemas["PlayOut"]["examples"][0]).session_id != ""
    decoded += 1
    assert api_module.UserSettings.from_dict(
        schemas["UserSettings"]["examples"][0]).skip_forward_s > 0
    decoded += 1
    assert api_module.Progress.from_dict(
        schemas["Progress"]["examples"][0]).item_id != ""
    decoded += 1
    assert api_module.Bookmark.from_dict(
        schemas["Bookmark"]["examples"][0]).position_s >= 0
    decoded += 1
    assert api_module.Me.from_dict(
        schemas["Me"]["examples"][0]).user_id != ""
    decoded += 1
    assert api_module.ItemDetail.from_dict(
        schemas["ItemDetail"]["examples"][0]).id != ""
    decoded += 1
    assert api_module.PlaySession.from_dict(
        schemas["SessionOut"]["examples"][0]).session_id != ""
    decoded += 1
    targets = schemas["CastTargets"]["examples"][0]["targets"]
    assert api_module.CastTarget.from_dict(targets[0]).entity_id != ""
    decoded += 1
    assert api_module.Stats.from_dict(
        schemas["Stats"]["examples"][0]).by_month != []
    decoded += 1
    assert api_module.Manifest.from_dict(
        schemas["ItemManifest"]["examples"][0]).files != []
    decoded += 1
    assert decoded >= 8


def test_resolve_url_triple_forms():
    base = "https://door.example:8457/audiobook/"
    wanted = base + "api/items/it-1/files/f-a"
    assert api_module.resolve_url(
        base, "/api/items/it-1/files/f-a") == wanted
    assert api_module.resolve_url(
        base, "/audiobook/api/items/it-1/files/f-a") == wanted
    assert api_module.resolve_url(
        base, "api/items/it-1/files/f-a") == wanted
    assert api_module.resolve_url(
        base, "https://cdn.example/x") == "https://cdn.example/x"


def test_client_routes_cover_person_surface_without_admin():
    spec = _spec()
    defined = {(method.upper(), path)
               for path, operations in spec["paths"].items()
               for method in operations}
    for route in routes_module.CLIENT_ROUTES:
        assert route in defined, route
        assert not route[1].startswith("/api/admin/")
    assert (("GET", "/api/status") in
            set(routes_module.CLIENT_ROUTES))
