"""DTOs, URL rules and the urllib client against the fake server."""

import json
import socket

import pytest

from xx_audiobook import api as api_module
from xx_audiobook.api import (
    ApiError,
    AudiobookApi,
    BookmarkCreate,
    CastPlay,
    CastTransport,
    ItemsQuery,
    PlayRequest,
    ProgressPut,
    ReviewPut,
    SyncRequest,
    UserSettingsPut,
)


def _client(server, token="tok", **kwargs):
    return AudiobookApi(server.api_base, token, **kwargs)


def test_epoch_seconds_decode_exactly():
    progress = api_module.Progress.from_dict({
        "item_id": "it-1", "position_s": 1234.5, "duration_s": 36000.0,
        "speed": 1.25, "is_finished": False, "started_at": 1790000000.0,
        "finished_at": None, "last_played_at": 1790000600.0,
        "updated_at": 1790000600.0})
    assert progress.position_s == 1234.5
    assert progress.updated_at == 1790000600.0
    summary = api_module.ItemSummary.from_dict({
        "id": "it-1", "title": "T", "added_at": 1790000000.0,
        "progress": {"position_s": 1.0, "updated_at": 2.0}})
    assert summary.added_at == 1790000000.0
    assert summary.progress.updated_at == 2.0
    assert summary.missing is False
    assert api_module.ItemSummary.from_dict(
        {"id": "x", "title": "T", "status": "missing"}).missing is True


def test_null_sha256_decodes_as_empty():
    media = api_module.MediaFile.from_dict(
        {"file_id": "f", "size": 3, "sha256": None})
    assert media.sha256 == ""


def test_request_bodies_carry_only_documented_keys():
    assert UserSettingsPut(skip_back_s=10,
                           updated_at=1790003600.0).to_dict() == {
        "skip_back_s": 10, "updated_at": 1790003600.0}
    assert PlayRequest().to_dict() == {"device": ""}
    assert ReviewPut(5, "Loved it").to_dict() == {
        "stars": 5, "body": "Loved it"}
    assert SyncRequest(4100.0, 100.0, 1.25).to_dict() == {
        "position_s": 4100.0, "listened_s": 100.0, "speed": 1.25}
    assert CastPlay("media_player.k", "it-1",
                    position_s=12.0).to_dict() == {
        "entity_id": "media_player.k", "item_id": "it-1",
        "position_s": 12.0}
    assert CastTransport("media_player.k", "pause").to_dict() == {
        "entity_id": "media_player.k", "action": "pause"}


def test_seg_strips_slashes():
    assert api_module.seg("it-1") == "it-1"
    assert api_module.seg("a/b") == "ab"


def test_decode_list_accepts_bare_and_wrapped():
    make = api_module.Me.from_dict
    assert len(api_module.decode_list('[{"user_id":"u"}]', make)) == 1
    assert len(api_module.decode_list('{"results":[{"user_id":"u"}]}',
                                      make)) == 1
    assert api_module.decode_list('{"results":[]}', make) == []
    assert api_module.decode_list('{"other":[]}', make) == []
    with pytest.raises(ApiError.Http):
        api_module.decode_list("garbage", make)
    with pytest.raises(ApiError.Http):
        api_module.decode('{"user_id":', make)


def test_resolve_url_keeps_door_prefix():
    base = "https://door.example:8457/audiobook/"
    assert api_module.resolve_url(
        base, "/api/items/a/files/f") == base + "api/items/a/files/f"
    assert api_module.resolve_url(
        base, "/audiobook/api/items/a/files/f") == (
        base + "api/items/a/files/f")
    assert api_module.resolve_url(
        base, "api/items/a/files/f") == base + "api/items/a/files/f"
    assert api_module.resolve_url(
        base, "https://cdn.example/x") == "https://cdn.example/x"


def test_stream_and_cover_urls_live_under_base(fake_server):
    client = _client(fake_server)
    assert client.file_url("it-1", "f-a") == (
        fake_server.api_base + "api/items/it-1/files/f-a")
    assert client.cover_url("it-1") == (
        fake_server.api_base + "api/items/it-1/cover?size=thumb")
    assert client.file_url("a/b", "f") .endswith("/files/f")


def test_client_calls_contract_routes(fake_server):
    client = _client(fake_server)
    assert client.me().user_id == "u-1"
    page = client.items(ItemsQuery(page=2, limit=25, q="long way",
                                   sort="last_played", descending=True,
                                   filter="in_progress"))
    assert page.total == 2
    asked = fake_server.last_call(
        "GET", "/audiobook/api/items")[4]
    assert asked["q"] == ["long way"]
    assert asked["sort"] == ["last_played"]
    assert asked["order"] == ["desc"]
    assert asked["filter"] == ["in_progress"]
    assert client.item("it-1").chapters[0].title == "One"
    assert len(client.continue_listening()) == 1
    assert client.manifest("it-1").files[0].sha256 != ""
    play = client.play("it-1")
    assert play.session_id == "ps-1"
    assert play.tracks[1].start_offset_s == 3600.0
    assert client.sync("ps-1", SyncRequest(4100.0, 100.0, 1.25)).position_s \
        == 4100.0
    client.close_session("ps-1")
    assert len(client.all_progress()) >= 1
    assert client.progress("it-1").position_s == 4100.0
    assert client.progress("ghost") is None
    updated = client.put_progress(
        "it-1", ProgressPut(4200.0, updated_at=1790003700.0))
    assert updated.position_s == 4200.0
    assert client.finish("it-1").is_finished is True
    assert client.unfinish("it-1").is_finished is False
    assert client.bookmarks("it-1")[0].note == "great line"
    added = client.add_bookmark(BookmarkCreate("it-1", 200.0))
    assert added.item_id == "it-1"
    edited = client.edit_bookmark("bm-2",
                                  api_module.BookmarkPatch(note="x"))
    assert edited.note == "x"
    client.delete_bookmark("bm-2")
    reviews = client.reviews("it-1", 1)
    assert reviews.results[1].user.display_name == "Bob"
    assert client.put_review("it-1",
                             ReviewPut(5, "Loved it")).stars == 5
    client.delete_my_review("it-1")
    client.admin_delete_review("rv-2")
    assert client.locations()[0].name == "Home"
    assert client.cast_targets("loc-home")[0].entity_id == \
        "media_player.kitchen"
    assert client.cast_play(
        CastPlay("media_player.kitchen", "it-1",
                 position_s=12.0)).file_id == "f-a"
    assert client.cast_transport(
        CastTransport("media_player.kitchen", "pause")).ok is True
    assert client.series(7).books[0].sequence == "1"
    assert client.author(11).books[0].id == "it-1"
    assert client.narrator(12).name == "Rachel Dulude"
    home = client.home()
    assert [s.id for s in home.shelves][:2] == ["continue",
                                                "continue_series"]
    assert home.shelves[0].items()[0].id == "it-1"
    sessions = client.sessions(2, 20)
    assert sessions.results[0].device == "Pixel 9"
    client.delete_session("ps-1")
    assert client.settings().skip_forward_s == 30
    assert client.put_settings(
        UserSettingsPut(skip_back_s=10,
                        updated_at=1790003600.0)).skip_back_s == 10
    assert client.collections()[0].name == "Staff picks"
    assert client.collection("col-1").books[0].id == "it-1"
    assert client.playlists() == []
    assert client.playlist("pl-1").books[0].id == "it-1"
    assert client.stats(2026).total_listened_s == 7200.0
    assert client.status().subsystems["ha"].state == "not_configured"
    assert client.search("long", 8).books[0].id == "it-1"
    assert "sci-fi" in client.facets()["genres"]


def test_partial_settings_put_sends_only_owned_keys(fake_server):
    client = _client(fake_server)
    client.put_settings(UserSettingsPut(skip_back_s=10,
                                        updated_at=1790003600.0))
    _method, _path, _headers, body, _query = fake_server.last_call(
        "PUT", "/audiobook/api/me/settings")
    assert json.loads(body) == {"skip_back_s": 10,
                                "updated_at": 1790003600.0}


def test_put_progress_sends_client_clock(fake_server):
    client = _client(fake_server)
    client.put_progress("it-1",
                        ProgressPut(4100.0, updated_at=1790003600.0))
    _method, _path, _headers, body, _query = fake_server.last_call(
        "PUT", "/audiobook/api/me/progress/it-1")
    assert json.loads(body)["updated_at"] == 1790003600.0


def test_403_not_started_carries_reason(fake_server):
    fake_server.add_fault("PUT", "/audiobook/api/items/it-1/review", 403,
                          {"detail": "start first",
                           "reason": "not_started"})
    client = _client(fake_server)
    with pytest.raises(ApiError.Http) as caught:
        client.put_review("it-1", ReviewPut(4))
    assert caught.value.code == 403
    assert caught.value.reason == "not_started"


def test_409_not_playable_is_a_plain_line(fake_server):
    fake_server.add_fault("POST", "/audiobook/api/items/it-1/play", 409,
                          {"detail": "no files", "reason": "not_playable"})
    client = _client(fake_server)
    with pytest.raises(ApiError.Http) as caught:
        client.play("it-1")
    assert caught.value.code == 409
    assert caught.value.reason == "not_playable"
    assert caught.value.message == api_module.NOT_PLAYABLE_MESSAGE


def test_422_surfaces_verbatim(fake_server):
    fake_server.add_fault("PUT", "/audiobook/api/me/settings", 422,
                          {"detail": "bad speed"})
    client = _client(fake_server)
    with pytest.raises(ApiError.Http) as caught:
        client.put_settings(UserSettingsPut(default_speed=99.0))
    assert caught.value.code == 422
    assert caught.value.message == "bad speed"


def test_truncated_body_is_bad_body(fake_server):
    fake_server.add_fault("GET", "/audiobook/api/me/continue", 200,
                          "<truncate>")
    client = _client(fake_server)
    with pytest.raises(ApiError.Http) as caught:
        client.continue_listening()
    assert caught.value.reason == "bad_body"


def test_refused_connection_is_unreachable_not_timeout(fake_server):
    port = fake_server.port
    fake_server.stop()
    client = AudiobookApi(f"http://127.0.0.1:{port}/audiobook/", "tok")
    with pytest.raises(ApiError.Unreachable) as caught:
        client.me()
    assert not isinstance(caught.value, ApiError.Timeout)


def test_slow_failure_is_timeout_but_still_unreachable():
    with pytest.raises(ApiError.Timeout) as caught:
        raise api_module.error_for(socket.timeout("timed out"))
    assert isinstance(caught.value, ApiError.Unreachable)
    assert caught.value.args[0] == api_module.TIMEOUT_MESSAGE


def test_no_base_is_not_signed_in():
    client = AudiobookApi(None, None)
    with pytest.raises(ApiError.NotSignedIn):
        client.me()
    assert client.resolve("api/x") is None


def test_manifest_uses_the_long_timeout(fake_server):
    seen = {}

    def spy(method, url, headers, body, timeout):
        seen["timeout"] = timeout
        return (200, '{"item_id":"it-1","total_size":0,"files":[]}')

    client = AudiobookApi(fake_server.api_base, "tok", transport=spy)
    client.manifest("it-1")
    assert seen["timeout"] == api_module.MANIFEST_READ_TIMEOUT_S


def test_bearer_only_goes_to_the_session_origin():
    seen = {}

    def spy(method, url, headers, body, timeout):
        seen.clear()
        seen.update(headers)
        return (200, '{"user_id":"u"}')

    AudiobookApi("http://127.0.0.1:9/audiobook/", "tok",
                 transport=spy).me()
    assert seen.get("Authorization") == "Bearer tok"

    foreign = _StaticTokens("tok", "https://elsewhere.example/audiobook/")
    AudiobookApi("http://127.0.0.1:9/audiobook/", "tok", transport=spy,
                 tokens=foreign).me()
    assert "Authorization" not in seen

    AudiobookApi("http://127.0.0.1:9/audiobook/", None,
                 transport=spy).me()
    assert "Authorization" not in seen


class _StaticTokens(api_module.TokenSource):
    def __init__(self, token, base) -> None:
        self._token = token
        self._base = base

    def current(self):
        return (self._token, self._base)


def test_network_guard_blocks_non_loopback():
    import socket
    with pytest.raises(OSError):
        socket.create_connection(("10.9.9.9", 9), timeout=0.1)
