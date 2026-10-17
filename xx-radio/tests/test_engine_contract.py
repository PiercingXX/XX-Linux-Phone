"""The Engine contract, exercised through the fake engine."""

import inspect

import pytest

from xx_music import engine as e
from xx_music.fake_engine import FakeEngine


CONTRACT = ("status", "sign_in", "sign_out", "search", "suggestions", "home",
            "moods", "mood_shelves", "charts", "library", "album", "artist",
            "playlist", "radio", "lyrics", "rate", "resolve", "art_url")


def test_base_engine_says_unsupported_not_crash():
    base = e.Engine()
    assert base.status() == e.EngineStatus(can_browse=False, can_play=False)
    assert base.suggestions("x") == []
    assert base.lyrics("x") is None
    assert base.art_url("u", 64) == "u"
    for name in ("search", "home", "moods", "charts", "resolve"):
        method = getattr(base, name)
        args = [] if name in ("home", "moods", "charts") else ["x"]
        with pytest.raises(e.EngineError) as caught:
            method(*args)
        assert caught.value.kind == e.ErrorKind.UNSUPPORTED


def test_fake_overrides_every_contract_method_with_same_signature():
    for name in CONTRACT:
        assert hasattr(e.Engine, name)
        base = inspect.signature(getattr(e.Engine, name))
        fake = inspect.signature(getattr(FakeEngine, name))
        assert list(base.parameters) == list(fake.parameters), name


def test_every_error_kind_has_a_sentence_without_codes():
    for kind in e.ErrorKind:
        text = e.ERROR_TEXT[kind]
        assert text.endswith(".")
        assert not any(ch.isdigit() for ch in text), text
    for state in e.ComponentState:
        assert e.COMPONENT_STATE_TEXT[state]


def test_search_home_pages_and_radio():
    fake = FakeEngine()
    results = fake.search("lumen")
    assert [t.title for t in results.tracks][:1] == ["Glass Harbor"]
    assert results.artists and results.albums
    only_tracks = fake.search("lumen", e.ItemKind.TRACK)
    assert only_tracks.tracks and not only_tracks.albums
    assert fake.search("   ").empty
    shelves = fake.home()
    assert all(shelf.items for shelf in shelves)
    album = fake.album("al-tide")
    assert len(album.tracks) == 3
    artist = fake.artist("ar-paper")
    assert artist.radio_seed == artist.top_tracks[0].id
    radio = fake.radio("tr-001", limit=4, exclude=frozenset({"tr-002"}))
    ids = [t.id for t in radio]
    assert "tr-001" not in ids and "tr-002" not in ids and len(ids) == 4
    assert radio[0].artists == fake.album("al-tide").artists


def test_not_found_and_auth_required_are_engine_errors():
    fake = FakeEngine()
    with pytest.raises(e.EngineError) as caught:
        fake.album("nope")
    assert caught.value.kind == e.ErrorKind.NOT_FOUND
    with pytest.raises(e.EngineError) as caught:
        fake.library(e.LibrarySection.PLAYLISTS)
    assert caught.value.kind == e.ErrorKind.AUTH_REQUIRED
    assert caught.value.user_text == "Sign in to see this."
    assert fake.sign_in({"name": "Sam"}) == "Sam"
    assert fake.library(e.LibrarySection.PLAYLISTS)
    assert fake.status().signed_in


def test_scripted_failure_fires_once():
    fake = FakeEngine()
    fake.fail_next("home", e.ErrorKind.SOURCE_CHANGED)
    with pytest.raises(e.EngineError) as caught:
        fake.home()
    assert caught.value.kind == e.ErrorKind.SOURCE_CHANGED
    assert not caught.value.transient
    assert fake.home()


def test_resolve_points_at_local_media(tmp_path):
    fake = FakeEngine(media_dir=tmp_path)
    with pytest.raises(e.EngineError) as caught:
        fake.resolve("tr-001")
    assert caught.value.kind == e.ErrorKind.NOT_AVAILABLE
    (tmp_path / "tr-001.ogg").write_bytes(b"OggS")
    source = fake.resolve("tr-001")
    assert source.uri.startswith("file://")
    assert source.expires_at == 0.0
    with pytest.raises(e.EngineError) as caught:
        FakeEngine().resolve("tr-001")
    assert caught.value.kind == e.ErrorKind.HELPER_MISSING


def test_track_round_trips_through_plain_dicts():
    track = FakeEngine().album("al-ember").tracks[0]
    assert e.Track.from_dict(track.to_dict()) == track
    bare = e.Track.from_dict({"id": "x"})
    assert bare.title == "" and bare.album is None and bare.duration_s is None


def test_rating_and_lyrics():
    fake = FakeEngine(signed_in=True)
    fake.rate("tr-004", e.Rating.LIKE)
    liked = fake.library(e.LibrarySection.LIKED)
    assert [item.id for item in liked] == ["tr-004"]
    lyrics = fake.lyrics("tr-004")
    assert lyrics.synced and lyrics.lines[0].text.startswith("Cinder Road")
