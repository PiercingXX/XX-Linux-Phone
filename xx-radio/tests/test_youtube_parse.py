"""Parsers for every recorded ytmusicapi response, plus broken copies.

Each fixture in fixtures/ytmusic/ was recorded on a real phone with
ytmusicapi 1.12.3. A mutated copy must raise EngineError(SOURCE_CHANGED),
never a raw KeyError: that is the M0 failure mode (KeyError: 'endpoint'
from an older ytmusicapi) turned into a visible update banner.
"""

import copy
import json
from pathlib import Path

import pytest

from xx_music import engine as e
from xx_radio.youtube import parse

FIXTURES = Path(__file__).parent / "fixtures" / "ytmusic"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def assert_changed(call):
    with pytest.raises(e.EngineError) as caught:
        call()
    assert caught.value.kind == e.ErrorKind.SOURCE_CHANGED


def test_search_all_buckets():
    results = parse.parse_search(load("search_all.json"))
    assert len(results.tracks) == 11  # 8 songs + 3 videos
    assert len(results.albums) == 3
    assert len(results.artists) == 7
    assert len(results.playlists) == 6
    assert not results.empty
    top = next(t for t in results.tracks if t.id == "0wTOi2TCSSk")
    assert top.title == "Robot Rock (Edit)"
    assert top.duration_s == 188.0
    assert top.art_url
    album = next(a for a in results.albums
                 if a.id == "MPREb_K8qWMWVqXGi")
    assert album.title == "Random Access Memories"
    artist = next(a for a in results.artists if a.title == "Daft Punk")
    assert artist.id == "UCRr1xG_2WIDs18a6cIiCxeA"
    playlist = results.playlists[0]
    assert playlist.kind == e.ItemKind.PLAYLIST and playlist.id


def test_search_songs():
    results = parse.parse_search(load("search_songs.json"))
    assert len(results.tracks) == 20
    assert not results.albums and not results.artists and not results.playlists
    first = results.tracks[0]
    assert first.id == "4D7u5KF7SP8"
    assert first.title.startswith("Get Lucky")
    assert first.artist_line == "Daft Punk, Pharrell Williams, Nile Rodgers"
    assert first.album == e.AlbumRef("Random Access Memories",
                                     "MPREb_K8qWMWVqXGi")
    assert first.duration_s == 370.0
    assert first.art_url.startswith("https://")


def test_search_empty_is_empty_not_an_error():
    assert parse.parse_search([]).empty


def test_search_broken():
    assert_changed(lambda: parse.parse_search({"not": "a list"}))
    raw = load("search_songs.json")
    song = raw[0]
    for mutate in (lambda s: s.pop("videoId"),
                   lambda s: s.update(videoId=None),
                   lambda s: s.update(title=5),
                   lambda s: s.update(artists="Daft Punk"),
                   lambda s: s.update(duration_seconds="long"),
                   lambda s: s.update(thumbnails="nope")):
        broken = copy.deepcopy(song)
        mutate(broken)
        assert_changed(lambda: parse.parse_search([broken]))
    broken = copy.deepcopy(raw[0])
    broken["resultType"] = "jingle"
    assert_changed(lambda: parse.parse_search([broken]))
    broken = copy.deepcopy(load("search_all.json")[0])
    broken["artists"] = []
    assert_changed(lambda: parse.parse_search([broken]))


def test_suggestions():
    assert parse.parse_suggestions(load("search_suggestions.json")) == [
        "daft punk",
        "daft punk harder better faster stronger",
        "daft punk one more time",
        "daft punk get lucky",
        "daft punk around the world",
        "daft punk veridis quo",
    ]
    assert parse.parse_suggestions([]) == []


def test_suggestions_broken():
    assert_changed(lambda: parse.parse_suggestions("daft punk"))
    assert_changed(lambda: parse.parse_suggestions([7]))
    assert_changed(lambda: parse.parse_suggestions([{"nope": 1}]))


def test_home():
    shelves = parse.parse_home(load("home.json"))
    assert [(s.title, len(s.items)) for s in shelves] == [
        ("Quick picks", 20),
        ("Today's biggest hits", 10),
        ("Trending community playlists", 10),
        ("Throwbacks", 10),
        ("Trending songs for you", 15),
    ]
    first = shelves[0].items[0]
    assert first.kind == e.ItemKind.TRACK
    assert first.id == "xukbqwRuN5w"
    assert first.title == "Choosin' Texas"
    assert first.subtitle == "Ella Langley"
    assert first.track is not None and first.track.id == first.id
    playlist = shelves[1].items[0]
    assert playlist.kind == e.ItemKind.PLAYLIST
    assert playlist.id == "RDCLAK5uy_lJ8xZWiZj2GCw7MArjakb6b0zfvqwldps"


def test_home_broken():
    assert_changed(lambda: parse.parse_home({}))
    raw = load("home.json")
    broken = copy.deepcopy(raw)
    del broken[0]["contents"]
    assert_changed(lambda: parse.parse_home(broken))
    broken = copy.deepcopy(raw)
    broken[0]["contents"][0] = {"title": "Nameless"}
    assert_changed(lambda: parse.parse_home(broken))
    broken = copy.deepcopy(raw)
    broken[0]["contents"][0].pop("title")
    assert_changed(lambda: parse.parse_home(broken))
    broken = copy.deepcopy(raw)
    broken[0]["title"] = ""
    assert_changed(lambda: parse.parse_home(broken))


def test_moods():
    moods = parse.parse_moods(load("mood_categories.json"))
    assert len(moods) == 38
    assert {m.group for m in moods} == {"Moods & moments", "Genres"}
    chill = next(m for m in moods if m.title == "Chill")
    assert chill.id == "ggMPOg1uX1JOQWZFeDByc2Jm"
    assert chill.group == "Moods & moments"


def test_moods_broken():
    assert_changed(lambda: parse.parse_moods({}))
    assert_changed(lambda: parse.parse_moods([]))
    assert_changed(lambda: parse.parse_moods({"Moods": "nope"}))
    assert_changed(lambda: parse.parse_moods({"Moods": [{"title": "Chill"}]}))


def test_mood_playlists():
    raw = [{"playlistId": "PL1", "title": "Chill out", "thumbnails": [],
            "count": "5", "owned": False}]
    shelves = parse.parse_mood_playlists(raw)
    assert len(shelves) == 1
    item = shelves[0].items[0]
    assert item.kind == e.ItemKind.PLAYLIST
    assert (item.id, item.title, item.subtitle) == ("PL1", "Chill out",
                                                   "5 songs")
    assert parse.parse_mood_playlists([]) == []
    assert_changed(lambda: parse.parse_mood_playlists({}))
    assert_changed(lambda: parse.parse_mood_playlists(
        [{"title": "No id"}]))


def test_charts():
    raw = {
        "countries": {"selected": {"text": "Global"}, "options": ["ZZ"]},
        "videos": [{"title": "Daily Top", "playlistId": "PL1",
                    "thumbnails": []}],
        "artists": [{"title": "Daft Punk", "browseId": "UC1",
                     "subscribers": "73.1M", "thumbnails": [],
                     "rank": "1", "trend": "neutral"}],
    }
    shelves = parse.parse_charts(raw)
    assert [s.title for s in shelves] == ["Top videos", "Top artists"]
    assert shelves[0].items[0].kind == e.ItemKind.PLAYLIST
    assert shelves[1].items[0] == e.Item(
        e.ItemKind.ARTIST, "UC1", "Daft Punk", "73.1M", "")
    bare = {"countries": {}, "videos": [], "artists": []}
    assert parse.parse_charts(bare) == []
    assert_changed(lambda: parse.parse_charts([]))
    assert_changed(lambda: parse.parse_charts({}))
    broken = copy.deepcopy(raw)
    broken["videos"] = "nope"
    assert_changed(lambda: parse.parse_charts(broken))
    broken = copy.deepcopy(raw)
    del broken["artists"][0]["browseId"]
    assert_changed(lambda: parse.parse_charts(broken))


def test_album():
    page = parse.parse_album(load("album.json"), "MPREb_K8qWMWVqXGi")
    assert page.id == "MPREb_K8qWMWVqXGi"
    assert page.title == "Random Access Memories"
    assert page.artists == (e.ArtistRef("Daft Punk", "UCRr1xG_2WIDs18a6cIiCxeA"),)
    assert page.year == "2013"
    assert page.kind_label == "Album"
    assert page.art_url.startswith("https://")
    assert len(page.tracks) == 13
    first = page.tracks[0]
    assert first.id == "IluRBvnYMoY"
    assert first.title == "Give Life Back to Music"
    assert first.duration_s == 276.0
    assert first.album == e.AlbumRef("Random Access Memories",
                                     "MPREb_K8qWMWVqXGi")
    assert first.art_url == page.art_url


def test_album_broken():
    assert_changed(lambda: parse.parse_album({}, "x"))
    raw = load("album.json")
    broken = copy.deepcopy(raw)
    broken["tracks"] = []
    assert_changed(lambda: parse.parse_album(broken, "x"))
    broken = copy.deepcopy(raw)
    del broken["tracks"][0]["videoId"]
    assert_changed(lambda: parse.parse_album(broken, "x"))
    broken = copy.deepcopy(raw)
    broken["artists"] = "Daft Punk"
    assert_changed(lambda: parse.parse_album(broken, "x"))


def test_artist():
    page = parse.parse_artist(load("artist.json"), "UC_kRDKYrUlrbtrSiyu5Tflg")
    assert page.id == "UC_kRDKYrUlrbtrSiyu5Tflg"
    assert page.name == "Daft Punk"
    assert page.art_url.startswith("https://")
    assert len(page.top_tracks) == 5
    assert page.top_tracks[0].id == "khnokW3Mw24"
    assert page.top_tracks[0].title == "Instant Crush (feat. Julian Casablancas)"
    assert page.radio_seed == page.top_tracks[0].id
    assert len(page.albums) == 10
    assert page.albums[0].kind == e.ItemKind.ALBUM
    assert "2023" in page.albums[0].subtitle
    assert len(page.singles) == 10
    assert len(page.related) == 10
    assert page.related[0].kind == e.ItemKind.ARTIST
    assert page.related[0].subtitle


def test_artist_broken():
    assert_changed(lambda: parse.parse_artist([], "x"))
    raw = load("artist.json")
    broken = copy.deepcopy(raw)
    del broken["name"]
    assert_changed(lambda: parse.parse_artist(broken, "x"))
    broken = copy.deepcopy(raw)
    del broken["songs"]
    assert_changed(lambda: parse.parse_artist(broken, "x"))
    broken = copy.deepcopy(raw)
    del broken["songs"]["results"][0]["videoId"]
    assert_changed(lambda: parse.parse_artist(broken, "x"))
    broken = copy.deepcopy(raw)
    del broken["related"]["results"][0]["browseId"]
    assert_changed(lambda: parse.parse_artist(broken, "x"))


def test_playlist():
    page = parse.parse_playlist(load("playlist.json"),
                                "OLAK5uy_mvGpNqcAglCR8WbCLLHnIitRBdLjho0ZI")
    assert page.id == "OLAK5uy_mvGpNqcAglCR8WbCLLHnIitRBdLjho0ZI"
    assert page.title == "Videos"
    assert page.author == "Daft Punk"
    assert page.track_count == 116
    assert len(page.tracks) == 116
    assert page.art_url.startswith("https://")
    assert page.editable is False
    first = page.tracks[0]
    assert first.id == "a5uQMwRMHcs"
    assert first.title == "Instant Crush (feat. Julian Casablancas)"
    assert first.duration_s == 340.0


def test_playlist_broken():
    assert_changed(lambda: parse.parse_playlist("nope", "x"))
    raw = load("playlist.json")
    broken = copy.deepcopy(raw)
    broken["tracks"] = "nope"
    assert_changed(lambda: parse.parse_playlist(broken, "x"))
    broken = copy.deepcopy(raw)
    del broken["tracks"][0]["title"]
    assert_changed(lambda: parse.parse_playlist(broken, "x"))
    broken = copy.deepcopy(raw)
    broken["trackCount"] = "many"
    assert_changed(lambda: parse.parse_playlist(broken, "x"))


def test_watch_playlist():
    tracks = parse.parse_watch_playlist(load("watch_playlist.json"))
    assert len(tracks) == 50
    first = tracks[0]
    assert first.id == "4D7u5KF7SP8"
    assert first.title.startswith("Get Lucky")
    assert first.duration_s == 370.0
    assert first.album == e.AlbumRef("Random Access Memories",
                                     "MPREb_K8qWMWVqXGi")
    assert first.art_url.startswith("https://")
    raw = load("watch_playlist.json")
    assert parse.parse_watch_lyrics_id(raw) == "MPLYt_K8qWMWVqXGi-8"
    raw["lyrics"] = None
    assert parse.parse_watch_lyrics_id(raw) is None


def test_watch_playlist_broken():
    assert_changed(lambda: parse.parse_watch_playlist({}))
    raw = load("watch_playlist.json")
    broken = copy.deepcopy(raw)
    broken["tracks"] = []
    assert_changed(lambda: parse.parse_watch_playlist(broken))
    broken = copy.deepcopy(raw)
    del broken["tracks"][2]["videoId"]
    assert_changed(lambda: parse.parse_watch_playlist(broken))
    broken = copy.deepcopy(raw)
    broken["lyrics"] = 5
    assert_changed(lambda: parse.parse_watch_lyrics_id(broken))


def test_lyrics_plain():
    lyrics = parse.parse_lyrics(load("lyrics.json"))
    assert lyrics is not None
    assert not lyrics.synced
    assert len(lyrics.lines) == 8
    assert lyrics.lines[0] == e.LyricLine("[lyric line 1]")
    assert lyrics.source == ""
    assert parse.parse_lyrics(None) is None
    assert parse.parse_lyrics({"lyrics": "", "source": None,
                               "hasTimestamps": False}) is None


def test_lyrics_timed():
    raw = {"lyrics": [{"lyricLine": "hello",
                       "cueRange": {"startTimeMilliseconds": "1500",
                                    "endTimeMilliseconds": "3000",
                                    "metadata": {"id": "1"}}}],
           "source": "LyricFind",
           "hasTimestamps": True}
    lyrics = parse.parse_lyrics(raw)
    assert lyrics is not None and lyrics.synced
    assert lyrics.lines == (e.LyricLine("hello", 1500),)
    assert lyrics.source == "LyricFind"


def test_lyrics_broken():
    assert_changed(lambda: parse.parse_lyrics({"lyrics": 5}))
    assert_changed(lambda: parse.parse_lyrics(
        {"lyrics": "x", "hasTimestamps": "soon"}))
    assert_changed(lambda: parse.parse_lyrics(
        {"lyrics": [{"cueRange": {}}], "hasTimestamps": True}))
    assert_changed(lambda: parse.parse_lyrics(
        {"lyrics": [{"lyricLine": "x",
                     "cueRange": {"startTimeMilliseconds": "soon"}}],
         "hasTimestamps": True}))


def test_duration_strings():
    assert parse.parse_duration("6:10") == 370.0
    assert parse.parse_duration("1:04:36") == 3876.0
    assert parse.parse_duration(" 4:02 ") == 242.0
    assert parse.parse_duration("") is None
    assert parse.parse_duration("soon") is None
    assert parse.parse_duration("1:2:3:4") is None


def test_library_shapes():
    song = {"videoId": "v1", "title": "Song", "artists": [{"name": "A",
                                                          "id": "UC1"}],
            "album": {"name": "Al", "id": "MPRE1"}, "duration_seconds": 200,
            "thumbnails": [], "isExplicit": False}
    assert parse.parse_library_songs([song])[0].id == "v1"
    assert_changed(lambda: parse.parse_library_songs([{}]))
    assert parse.parse_library_songs([]) == []

    playlists = parse.parse_library_playlists(
        [{"playlistId": "PL1", "title": "Mix", "thumbnails": [],
          "count": "5", "owned": True}])
    assert playlists[0].subtitle == "5 songs"
    assert_changed(lambda: parse.parse_library_playlists(
        [{"title": "No id"}]))

    albums = parse.parse_library_albums(
        [{"browseId": "MPRE1", "title": "Al", "type": "Album",
          "thumbnails": [], "artists": [{"name": "A", "id": "UC1"}],
          "year": "2020"}])
    assert albums[0].kind == e.ItemKind.ALBUM
    assert "2020" in albums[0].subtitle
    assert_changed(lambda: parse.parse_library_albums(
        [{"title": "No id"}]))

    artists = parse.parse_library_artists(
        [{"browseId": "UC1", "artist": "A", "subscribers": "1K",
          "thumbnails": []}])
    assert artists[0] == e.Item(e.ItemKind.ARTIST, "UC1", "A", "1K", "")
    assert_changed(lambda: parse.parse_library_artists(
        [{"browseId": "UC1"}]))

    liked = parse.parse_liked_songs({"id": "LM", "tracks": [song]})
    assert [t.id for t in liked] == ["v1"]
    assert_changed(lambda: parse.parse_liked_songs({}))

    history = parse.parse_history([song])
    assert history[0].kind == e.ItemKind.TRACK and history[0].track is not None
    assert_changed(lambda: parse.parse_history([{"title": "No id"}]))


def test_garbage_never_leaks_raw_errors():
    garbage = [{}, [], "x", 5, None, True]
    fns = [parse.parse_search, parse.parse_suggestions, parse.parse_home,
           parse.parse_moods, parse.parse_mood_playlists, parse.parse_charts,
           parse.parse_watch_playlist, parse.parse_library_playlists,
           parse.parse_library_songs, parse.parse_library_albums,
           parse.parse_library_artists, parse.parse_history]
    for fn in fns:
        for bad in garbage:
            try:
                got = fn(bad)
            except e.EngineError as caught:
                assert caught.kind == e.ErrorKind.SOURCE_CHANGED, fn
            else:
                assert got in ([], e.SearchResults()), (fn, bad)
    for bad in garbage:
        if bad is None:
            continue
        try:
            got = parse.parse_lyrics(bad)
        except e.EngineError as caught:
            assert caught.kind == e.ErrorKind.SOURCE_CHANGED
        else:
            assert got is None, bad
