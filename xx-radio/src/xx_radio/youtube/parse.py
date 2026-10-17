"""ytmusicapi responses into xx_music.engine models.

Every function takes the decoded value one ``YTMusic`` method returns
(ytmusicapi 1.12.3) and nothing else, so the parsers stay testable on the
recorded fixtures without the library installed.

A response that does not have the documented shape -- a missing key, a
wrong type, or an empty answer where YouTube always answers -- raises
``EngineError(SOURCE_CHANGED)``. Raw ``KeyError``/``TypeError`` never
escape: fields YouTube may omit (artists on some songs, album, duration,
views, thumbnails) fall back to empty values instead.
"""

from __future__ import annotations

from typing import Any, Callable

from xx_music import engine as e
from xx_radio.youtube import art

Json = dict[str, Any]


def _changed(what: str) -> e.EngineError:
    return e.EngineError(e.ErrorKind.SOURCE_CHANGED, f"youtube: {what}")


def _obj(raw: Any, what: str) -> Json:
    if not isinstance(raw, dict):
        raise _changed(what)
    return raw


def _list(raw: Any, what: str) -> list[Any]:
    if not isinstance(raw, list):
        raise _changed(what)
    return raw


def _id(raw: Json, key: str, what: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise _changed(what)
    return value


def _title(raw: Json, what: str) -> str:
    return _id(raw, "title", what)


def _opt_str(raw: Json, key: str, what: str) -> str:
    value = raw.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _changed(f"{what}.{key}")
    return value


def _opt_float(raw: Json, key: str, what: str) -> float | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _changed(f"{what}.{key}")
    return float(value)


def _artists(raw: Json, what: str) -> tuple[e.ArtistRef, ...]:
    value = raw.get("artists")
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _changed(f"{what}.artists")
    refs = []
    for entry in value:
        if not isinstance(entry, dict):
            raise _changed(f"{what}.artists")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise _changed(f"{what}.artists.name")
        refs.append(e.ArtistRef(name, _opt_str(entry, "id", what)))
    return tuple(refs)


def _album_ref(raw: Json, what: str, default: e.AlbumRef | None = None
               ) -> e.AlbumRef | None:
    value = raw.get("album")
    if value is None:
        return default
    if isinstance(value, str):
        return e.AlbumRef(value, default.id if default else "")
    if not isinstance(value, dict):
        raise _changed(f"{what}.album")
    name = value.get("name")
    if not isinstance(name, str) or not name:
        raise _changed(f"{what}.album.name")
    return e.AlbumRef(name, _opt_str(value, "id", what))


def _thumb_url(raw: Json, what: str, px: int = 256) -> str:
    thumbs = raw.get("thumbnails")
    if thumbs is None:
        thumbs = raw.get("thumbnail")
    if thumbs is None:
        return ""
    if not isinstance(thumbs, list):
        raise _changed(f"{what}.thumbnails")
    return art.pick(thumbs, px)


def _duration(raw: Json, what: str) -> float | None:
    secs = _opt_float(raw, "duration_seconds", what)
    if secs is not None:
        return secs
    for key in ("length", "duration"):
        text = raw.get(key)
        if isinstance(text, str) and text.strip():
            parsed = parse_duration(text)
            if parsed is not None:
                return parsed
    return None


def parse_duration(text: str) -> float | None:
    """``"6:10"`` to seconds. Returns None when the text is not a duration."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(p.strip().isdigit() for p in parts):
        return None
    total = 0
    for part in parts:
        total = total * 60 + int(part.strip())
    return float(total)


def parse_track(raw: Any, album: e.AlbumRef | None = None, art_url: str = "",
                what: str = "track") -> e.Track:
    """One song-shaped dict (search, album, playlist, watch, library)."""
    item = _obj(raw, what)
    return e.Track(
        id=_id(item, "videoId", f"{what}.videoId"),
        title=_title(item, f"{what}.title"),
        artists=_artists(item, what),
        album=_album_ref(item, what, album),
        duration_s=_duration(item, what),
        art_url=_thumb_url(item, what) or art_url,
        explicit=bool(item.get("isExplicit", False)),
    )


def _subtitle(*parts: str) -> str:
    return " · ".join(p for p in parts if p)


# ---- search and suggestions --------------------------------------------------

_SEARCHABLE = ("song", "video")
_SKIP_TYPES = ("episode", "podcast")


def parse_search(raw: Any) -> e.SearchResults:
    """The list ``YTMusic.search`` returns, split into the four buckets."""
    tracks: list[e.Track] = []
    albums: list[e.Item] = []
    artists: list[e.Item] = []
    playlists: list[e.Item] = []
    for entry in _list(raw, "search"):
        item = _obj(entry, "search.item")
        kind = item.get("resultType")
        if not isinstance(kind, str):
            raise _changed("search.resultType")
        if kind in _SKIP_TYPES:
            continue
        thumbs = _thumb_url(item, "search")
        if kind in _SEARCHABLE:
            track = parse_track(item, art_url=thumbs, what="search.song")
            tracks.append(track)
            continue
        if kind == "album":
            title = _title(item, "search.title")
            browse_id = _id(item, "browseId", "search.album")
            artists_line = ", ".join(a.name for a in _artists(item, "search"))
            albums.append(e.Item(e.ItemKind.ALBUM, browse_id, title,
                                 _subtitle(item.get("year") if isinstance(
                                     item.get("year"), str) else "",
                                     artists_line), thumbs))
        elif kind == "artist":
            refs = _artists(item, "search")
            if refs:
                artist_id = refs[0].id or _opt_str(item, "browseId", "search")
                name = refs[0].name
            else:
                artist_id = _opt_str(item, "browseId", "search")
                name = (_opt_str(item, "artist", "search")
                        or _opt_str(item, "title", "search"))
            if not artist_id or not name:
                raise _changed("search.artist")
            subs = _opt_str(item, "subscribers", "search")
            artists.append(e.Item(e.ItemKind.ARTIST, artist_id, name,
                                  subs or "Artist", thumbs))
        elif kind == "playlist":
            title = _title(item, "search.title")
            browse_id = _id(item, "browseId", "search.playlist")
            author = item.get("author")
            playlists.append(e.Item(
                e.ItemKind.PLAYLIST, browse_id, title,
                author if isinstance(author, str) else "", thumbs))
        else:
            raise _changed(f"search.resultType.{kind}")
    return e.SearchResults(tuple(tracks), tuple(albums), tuple(artists),
                           tuple(playlists))


def parse_suggestions(raw: Any) -> list[str]:
    """The list ``YTMusic.get_search_suggestions`` returns."""
    out = []
    for entry in _list(raw, "suggestions"):
        if isinstance(entry, str):
            if entry:
                out.append(entry)
        elif isinstance(entry, dict):
            text = entry.get("text")
            if not isinstance(text, str) or not text:
                raise _changed("suggestions.text")
            out.append(text)
        else:
            raise _changed("suggestions")
    return out


# ---- home, moods, charts -----------------------------------------------------

def _card(raw: Any, what: str) -> e.Item:
    """One browsable card on a home shelf or a mood page."""
    item = _obj(raw, what)
    title = _title(item, f"{what}.title")
    thumbs = _thumb_url(item, what)
    if item.get("videoId") is not None:
        track = parse_track(item, art_url=thumbs, what=what)
        return e.Item(e.ItemKind.TRACK, track.id, track.title,
                      track.artist_line, track.art_url, track)
    for key, kind in (("playlistId", e.ItemKind.PLAYLIST),
                      ("browseId", None)):
        value = item.get(key)
        if isinstance(value, str) and value:
            if kind is not None:
                desc = item.get("description")
                return e.Item(kind, value, title,
                              desc if isinstance(desc, str) else "", thumbs)
            if value.startswith("MPRE"):
                return e.Item(e.ItemKind.ALBUM, value, title,
                              _opt_str(item, "year", what), thumbs)
            if value.startswith("UC"):
                subs = _opt_str(item, "subscribers", what)
                return e.Item(e.ItemKind.ARTIST, value, title,
                              subs or "Artist", thumbs)
            if value.startswith(("VL", "PL", "RD", "OLAK")):
                return e.Item(e.ItemKind.PLAYLIST, value, title, "", thumbs)
            raise _changed(f"{what}.browseId")
    raise _changed(f"{what}.id")


def parse_home(raw: Any) -> list[e.Shelf]:
    """The shelf list ``YTMusic.get_home`` returns."""
    shelves = []
    for entry in _list(raw, "home"):
        shelf = _obj(entry, "home.shelf")
        title = _title(shelf, "home.shelf")
        items = tuple(_card(content, "home.item")
                      for content in _list(shelf.get("contents"), "home"))
        if items:
            shelves.append(e.Shelf(title, items))
    return shelves


def parse_moods(raw: Any) -> list[e.Mood]:
    """The grouped dict ``YTMusic.get_mood_categories`` returns."""
    groups = _obj(raw, "moods")
    if not groups:
        raise _changed("moods")
    moods = []
    for group, entries in groups.items():
        if not isinstance(group, str) or not group:
            raise _changed("moods.group")
        for entry in _list(entries, "moods"):
            mood = _obj(entry, "moods.mood")
            moods.append(e.Mood(_id(mood, "params", "moods.params"),
                                _title(mood, "moods.mood"), group))
    return moods


def _library_playlist_item(raw: Any, what: str) -> e.Item:
    """A playlist dict in ``get_library_playlists`` format (moods reuse it)."""
    item = _obj(raw, what)
    playlist_id = item.get("playlistId")
    if not isinstance(playlist_id, str) or not playlist_id:
        playlist_id = _id(item, "browseId", f"{what}.playlistId")
    title = _title(item, f"{what}.title")
    count = item.get("count")
    subtitle = f"{count} songs" if isinstance(count, str) and count else ""
    return e.Item(e.ItemKind.PLAYLIST, playlist_id, title, subtitle,
                  _thumb_url(item, what))


def parse_mood_playlists(raw: Any) -> list[e.Shelf]:
    """The playlist list ``YTMusic.get_mood_playlists`` returns, as shelves."""
    items = tuple(_library_playlist_item(entry, "mood")
                  for entry in _list(raw, "mood"))
    return [e.Shelf("Playlists", items)] if items else []


def parse_charts(raw: Any) -> list[e.Shelf]:
    """The dict ``YTMusic.get_charts`` returns: video and artist shelves."""
    charts = _obj(raw, "charts")
    if not isinstance(charts.get("countries"), dict):
        raise _changed("charts.countries")
    shelves = []
    videos = _list(charts.get("videos", []), "charts.videos")
    if videos:
        shelves.append(e.Shelf(
            "Top videos",
            tuple(_library_playlist_item(v, "charts") for v in videos)))
    artists = _list(charts.get("artists", []), "charts.artists")
    if artists:
        items = []
        for entry in artists:
            person = _obj(entry, "charts.artist")
            items.append(e.Item(
                e.ItemKind.ARTIST, _id(person, "browseId", "charts.artist"),
                _title(person, "charts.artist"),
                _opt_str(person, "subscribers", "charts") or "Artist",
                _thumb_url(person, "charts")))
        shelves.append(e.Shelf("Top artists", tuple(items)))
    for key, title in (("genres", "Top genres"), ("languages", "Top songs")):
        if charts.get(key) is not None:
            entries = _list(charts.get(key), f"charts.{key}")
            if entries:
                shelves.append(e.Shelf(
                    title, tuple(_library_playlist_item(v, "charts")
                                 for v in entries)))
    return shelves


# ---- pages -------------------------------------------------------------------

def parse_album(raw: Any, album_id: str) -> e.AlbumPage:
    """The dict ``YTMusic.get_album`` returns."""
    album = _obj(raw, "album")
    art_url = _thumb_url(album, "album")
    default_album = e.AlbumRef(_title(album, "album"), album_id)
    tracks = _list(album.get("tracks"), "album.tracks")
    if not tracks:
        raise _changed("album.tracks")
    return e.AlbumPage(
        id=album_id,
        title=default_album.title,
        artists=_artists(album, "album"),
        year=_opt_str(album, "year", "album"),
        kind_label=_opt_str(album, "type", "album") or "Album",
        art_url=art_url,
        tracks=tuple(parse_track(t, default_album, art_url, "album.track")
                     for t in tracks),
    )


def _page_items(results: Any, what: str, make: Callable[[Json, str], e.Item]
                ) -> tuple[e.Item, ...]:
    return tuple(make(_obj(entry, what), what)
                 for entry in _list(results, what))


def _album_item(raw: Json, what: str) -> e.Item:
    artists = _artists(raw, what)
    line = ", ".join(a.name for a in artists)
    return e.Item(e.ItemKind.ALBUM, _id(raw, "browseId", f"{what}.browseId"),
                  _title(raw, what),
                  _subtitle(_opt_str(raw, "type", what),
                            _opt_str(raw, "year", what), line),
                  _thumb_url(raw, what))


def _artist_item(raw: Json, what: str) -> e.Item:
    return e.Item(e.ItemKind.ARTIST, _id(raw, "browseId", f"{what}.browseId"),
                  _title(raw, what),
                  _opt_str(raw, "subscribers", what) or "Artist",
                  _thumb_url(raw, what))


def _section(raw: Json, key: str, what: str) -> list[Any]:
    section = raw.get(key)
    if not isinstance(section, dict):
        raise _changed(f"{what}.{key}")
    return _list(section.get("results"), f"{what}.{key}")


def parse_artist(raw: Any, artist_id: str) -> e.ArtistPage:
    """The dict ``YTMusic.get_artist`` returns."""
    artist = _obj(raw, "artist")
    top = tuple(parse_track(t, art_url=_thumb_url(t, "artist.song"),
                            what="artist.song")
                for t in _section(artist, "songs", "artist"))
    return e.ArtistPage(
        id=_opt_str(artist, "channelId", "artist") or artist_id,
        name=_id(artist, "name", "artist.name"),
        art_url=_thumb_url(artist, "artist"),
        top_tracks=top,
        albums=_page_items(_section(artist, "albums", "artist"),
                           "artist.albums", _album_item),
        singles=_page_items(_section(artist, "singles", "artist"),
                            "artist.singles", _album_item),
        related=_page_items(_section(artist, "related", "artist"),
                            "artist.related", _artist_item),
        radio_seed=top[0].id if top else "",
    )


def parse_playlist(raw: Any, playlist_id: str) -> e.PlaylistPage:
    """The dict ``YTMusic.get_playlist`` returns."""
    page = _obj(raw, "playlist")
    author = page.get("author")
    if isinstance(author, dict):
        author_name = _opt_str(author, "name", "playlist")
    elif isinstance(author, str):
        author_name = author
    elif author is None:
        author_name = ""
    else:
        raise _changed("playlist.author")
    count = page.get("trackCount")
    if count is not None and (isinstance(count, bool)
                              or not isinstance(count, int)):
        raise _changed("playlist.trackCount")
    tracks = _list(page.get("tracks"), "playlist.tracks")
    return e.PlaylistPage(
        id=_opt_str(page, "id", "playlist") or playlist_id,
        title=_title(page, "playlist"),
        author=author_name,
        art_url=_thumb_url(page, "playlist"),
        track_count=count if isinstance(count, int) else None,
        tracks=tuple(parse_track(t, what="playlist.track") for t in tracks),
        editable=bool(page.get("owned", False)),
    )


# ---- watch playlist (radio), lyrics ------------------------------------------

def parse_watch_playlist(raw: Any) -> list[e.Track]:
    """The ``tracks`` of ``YTMusic.get_watch_playlist`` (radio seed queue)."""
    watch = _obj(raw, "watch")
    tracks = _list(watch.get("tracks"), "watch.tracks")
    if not tracks:
        raise _changed("watch.tracks")
    return [parse_track(t, what="watch.track") for t in tracks]


def parse_watch_lyrics_id(raw: Any) -> str | None:
    """The ``lyrics`` browse id of a watch playlist, if the song has lyrics."""
    watch = _obj(raw, "watch")
    browse_id = watch.get("lyrics")
    if browse_id is None:
        return None
    if not isinstance(browse_id, str) or not browse_id:
        raise _changed("watch.lyrics")
    return browse_id


def parse_lyrics(raw: Any) -> e.Lyrics | None:
    """The value ``YTMusic.get_lyrics`` returns, plain or timestamped."""
    if raw is None:
        return None
    got = _obj(raw, "lyrics")
    words = got.get("lyrics")
    if words is None:
        return None
    source = _opt_str(got, "source", "lyrics")
    if got.get("hasTimestamps") is True:
        lines = []
        for entry in _list(words, "lyrics.lines"):
            if isinstance(entry, dict):
                text = entry.get("lyricLine", entry.get("text"))
                cue = entry.get("cueRange", {})
                start = (cue.get("startTimeMilliseconds")
                         if isinstance(cue, dict)
                         else entry.get("start_time", entry.get("start_ms")))
                if not isinstance(text, str) or not text:
                    raise _changed("lyrics.line")
                if start is None:
                    raise _changed("lyrics.line.start")
                try:
                    start_ms = int(start)
                except (TypeError, ValueError):
                    raise _changed("lyrics.line.start") from None
                lines.append(e.LyricLine(text, start_ms))
            else:
                text = getattr(entry, "text", None)
                start = getattr(entry, "start_time", None)
                if not isinstance(text, str) or not text \
                        or not isinstance(start, int):
                    raise _changed("lyrics.line")
                lines.append(e.LyricLine(text, start))
        if not lines:
            return None
        return e.Lyrics(tuple(lines), source)
    if got.get("hasTimestamps") not in (False, None):
        raise _changed("lyrics.hasTimestamps")
    if not isinstance(words, str):
        raise _changed("lyrics.lyrics")
    lines = tuple(e.LyricLine(line) for line in words.splitlines() if line)
    return e.Lyrics(lines, source) if lines else None


# ---- library -----------------------------------------------------------------

def parse_library_playlists(raw: Any) -> list[e.Item]:
    """The list ``YTMusic.get_library_playlists`` returns."""
    return [_library_playlist_item(entry, "library")
            for entry in _list(raw, "library")]


def parse_library_songs(raw: Any) -> list[e.Track]:
    """The song list ``YTMusic.get_library_songs`` returns."""
    return [parse_track(entry, what="library.song")
            for entry in _list(raw, "library")]


def parse_library_albums(raw: Any) -> list[e.Item]:
    """The album list ``YTMusic.get_library_albums`` returns."""
    return [_album_item(_obj(entry, "library.album"), "library.album")
            for entry in _list(raw, "library")]


def parse_library_artists(raw: Any) -> list[e.Item]:
    """The artist list ``YTMusic.get_library_artists`` returns."""
    items = []
    for entry in _list(raw, "library"):
        person = _obj(entry, "library.artist")
        name = person.get("artist")
        if not isinstance(name, str) or not name:
            raise _changed("library.artist")
        items.append(e.Item(
            e.ItemKind.ARTIST, _id(person, "browseId", "library.artist"),
            name, _opt_str(person, "subscribers", "library") or "Artist",
            _thumb_url(person, "library")))
    return items


def parse_liked_songs(raw: Any) -> list[e.Track]:
    """The playlist dict ``YTMusic.get_liked_songs`` returns, as tracks."""
    page = _obj(raw, "liked")
    return [parse_track(entry, what="liked.track")
            for entry in _list(page.get("tracks"), "liked.tracks")]


def parse_history(raw: Any) -> list[e.Item]:
    """The playlist-item list ``YTMusic.get_history`` returns, as rows."""
    items = []
    for entry in _list(raw, "history"):
        track = parse_track(entry, what="history")
        items.append(e.Item(e.ItemKind.TRACK, track.id, track.title,
                            track.artist_line, track.art_url, track))
    return items
