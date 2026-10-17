"""The YouTube Music engine (unofficial; see README.md).

Implements the catalog half of the xx_music.engine contract over
ytmusicapi: search, suggestions, home, moods, charts, library, pages,
radio, lyrics and ratings. Stream resolution (``resolve``) arrives with
the M2 resolver and account storage (``sign_in``/``sign_out``) with M7
auth; until then they keep the base class ``UNSUPPORTED`` answer and the
``STREAM``/``SIGN_IN`` capabilities stay off.

Constructing the engine does no I/O and imports neither ytmusicapi nor
yt-dlp; every blocking call runs on the caller's worker thread.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from xx_music import engine as e
from xx_radio.youtube import art, catalog, parse, probe

CAPABILITIES = frozenset({
    e.SEARCH, e.SUGGEST, e.HOME, e.MOODS, e.CHARTS, e.LIBRARY, e.RADIO,
    e.LYRICS, e.RATE,
})

_SEARCH_FILTER = {
    e.ItemKind.TRACK: "songs",
    e.ItemKind.ALBUM: "albums",
    e.ItemKind.ARTIST: "artists",
    e.ItemKind.PLAYLIST: "playlists",
}


def _like_status(rating: e.Rating) -> Any:
    try:
        from ytmusicapi.models.content.enums import LikeStatus
    except ImportError:
        return {"like": "LIKE", "dislike": "DISLIKE"}.get(rating.value,
                                                          "INDIFFERENT")
    return {e.Rating.LIKE: LikeStatus.LIKE,
            e.Rating.DISLIKE: LikeStatus.DISLIKE}.get(
                rating, LikeStatus.INDIFFERENT)


class YouTubeMusicEngine(e.Engine):
    key = "ytm"
    name = "YouTube Music"
    capabilities = CAPABILITIES

    def __init__(self, engine_dir: Path | str,
                 catalog_client: catalog.Catalog | None = None) -> None:
        self.engine_dir = Path(engine_dir)
        self.catalog = (catalog_client if catalog_client is not None
                        else catalog.Catalog())

    def status(self) -> e.EngineStatus:
        try:
            found = probe.probe(self.engine_dir)
            status = probe.status_from(found, _dt.date.today())
        except Exception:
            status = probe.status_from(probe.Installed(), _dt.date.today())
        return e.EngineStatus(
            can_browse=status.can_browse, can_play=status.can_play,
            signed_in=self.catalog.signed_in, components=status.components)

    def search(self, query: str, kind: e.ItemKind | None = None,
               limit: int = 20) -> e.SearchResults:
        if not query.strip():
            return e.SearchResults()
        if kind == e.ItemKind.MOOD:
            return e.SearchResults()
        return self.catalog.fetch(
            e.SEARCH, "search", parse.parse_search, query,
            filter=_SEARCH_FILTER.get(kind) if kind is not None else None,
            limit=limit)

    def suggestions(self, query: str) -> list[str]:
        if not query.strip():
            return []
        return self.catalog.fetch(e.SUGGEST, "get_search_suggestions",
                                  parse.parse_suggestions, query)

    def home(self) -> list[e.Shelf]:
        return self.catalog.fetch(e.HOME, "get_home", parse.parse_home)

    def moods(self) -> list[e.Mood]:
        return self.catalog.fetch(e.MOODS, "get_mood_categories",
                                  parse.parse_moods)

    def mood_shelves(self, mood_id: str) -> list[e.Shelf]:
        return self.catalog.fetch(e.MOODS, "get_mood_playlists",
                                  parse.parse_mood_playlists, mood_id)

    def charts(self, region: str = "") -> list[e.Shelf]:
        country = region.strip().upper() or "ZZ"
        return self.catalog.fetch(e.CHARTS, "get_charts", parse.parse_charts,
                                  country)

    def library(self, section: e.LibrarySection) -> list[e.Item]:
        if not self.catalog.signed_in:
            raise e.EngineError(e.ErrorKind.AUTH_REQUIRED, "youtube: library")
        if section == e.LibrarySection.PLAYLISTS:
            return self.catalog.fetch(e.LIBRARY, "get_library_playlists",
                                      parse.parse_library_playlists, limit=100)
        if section == e.LibrarySection.SONGS:
            tracks = self.catalog.fetch(e.LIBRARY, "get_library_songs",
                                        parse.parse_library_songs, limit=100)
            return [e.Item(e.ItemKind.TRACK, t.id, t.title, t.artist_line,
                           t.art_url, t) for t in tracks]
        if section == e.LibrarySection.ALBUMS:
            return self.catalog.fetch(e.LIBRARY, "get_library_albums",
                                      parse.parse_library_albums, limit=100)
        if section == e.LibrarySection.ARTISTS:
            return self.catalog.fetch(e.LIBRARY, "get_library_artists",
                                      parse.parse_library_artists, limit=100)
        if section == e.LibrarySection.LIKED:
            tracks = self.catalog.fetch(e.LIBRARY, "get_liked_songs",
                                        parse.parse_liked_songs, limit=100)
            return [e.Item(e.ItemKind.TRACK, t.id, t.title, t.artist_line,
                           t.art_url, t) for t in tracks]
        tracks = self.catalog.fetch(e.LIBRARY, "get_history",
                                    parse.parse_history)
        return tracks

    def album(self, album_id: str) -> e.AlbumPage:
        return self.catalog.fetch(
            e.SEARCH, "get_album", lambda raw: parse.parse_album(raw,
                                                                 album_id),
            browseId=album_id)

    def artist(self, artist_id: str) -> e.ArtistPage:
        return self.catalog.fetch(
            e.SEARCH, "get_artist", lambda raw: parse.parse_artist(
                raw, artist_id), channelId=artist_id)

    def playlist(self, playlist_id: str, limit: int = 200) -> e.PlaylistPage:
        return self.catalog.fetch(
            e.SEARCH, "get_playlist", lambda raw: parse.parse_playlist(
                raw, playlist_id), playlist_id, limit)

    def radio(self, seed_track_id: str, limit: int = 25,
              exclude: frozenset[str] = frozenset()) -> list[e.Track]:
        tracks = self.catalog.fetch(
            e.RADIO, "get_watch_playlist", parse.parse_watch_playlist,
            videoId=seed_track_id, radio=True,
            limit=limit + len(exclude) + 1)
        return [t for t in tracks
                if t.id != seed_track_id and t.id not in exclude][:limit]

    def lyrics(self, track_id: str) -> e.Lyrics | None:
        browse_id = self.catalog.fetch(
            e.LYRICS, "get_watch_playlist", parse.parse_watch_lyrics_id,
            videoId=track_id, limit=1)
        if browse_id is None:
            return None
        raw = self.catalog.fetch(e.LYRICS, "get_lyrics", lambda got: got,
                                 browse_id)
        if isinstance(raw, dict) and raw.get("hasTimestamps"):
            return self.catalog.fetch(e.LYRICS, "get_lyrics",
                                      parse.parse_lyrics, browse_id,
                                      timestamps=True)
        try:
            result = parse.parse_lyrics(raw)
        except e.EngineError as exc:
            self.catalog.health.note_failure(e.LYRICS, exc.kind)
            raise
        self.catalog.health.note_success(e.LYRICS)
        return result

    def rate(self, track_id: str, rating: e.Rating) -> None:
        if not self.catalog.signed_in:
            raise e.EngineError(e.ErrorKind.AUTH_REQUIRED, "youtube: rate")
        self.catalog.fetch(e.RATE, "rate_song", lambda got: None, track_id,
                           _like_status(rating))

    def art_url(self, url: str, px: int) -> str:
        return art.sized(url, px)


def create(context) -> YouTubeMusicEngine:
    """Engine factory named by xx_radio.main.SPEC."""
    return YouTubeMusicEngine(context.paths.engine_dir())
