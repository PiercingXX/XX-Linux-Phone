"""A deterministic, offline engine: tests, demo screenshots, UI work.

Holds a small invented catalog in memory. It never opens a socket. Stream
sources point at ``file://`` paths under a directory the caller chooses
(the test suite writes tiny audio files there) so the player and the
cache can be exercised end to end without any network. Failures can be
scripted per method to drive the error states.
"""

from __future__ import annotations

import threading
from pathlib import Path

from xx_music import engine as e

_ARTISTS = {
    "ar-lumen": "Lumen Drive",
    "ar-north": "North Hollow",
    "ar-paper": "Paper Satellites",
}

# (track id, title, artist id, album id, seconds)
_TRACKS = (
    ("tr-001", "Glass Harbor", "ar-lumen", "al-tide", 214),
    ("tr-002", "Low Orbit", "ar-lumen", "al-tide", 187),
    ("tr-003", "Night Ferry", "ar-lumen", "al-tide", 242),
    ("tr-004", "Cinder Road", "ar-north", "al-ember", 198),
    ("tr-005", "Hollow Bells", "ar-north", "al-ember", 263),
    ("tr-006", "Static Garden", "ar-paper", "al-signal", 176),
    ("tr-007", "Signal Fires", "ar-paper", "al-signal", 221),
    ("tr-008", "Quiet Machines", "ar-paper", "al-signal", 305),
)

_ALBUMS = {
    "al-tide": ("Tidewater", "ar-lumen", "2024"),
    "al-ember": ("Ember Season", "ar-north", "2023"),
    "al-signal": ("Signal", "ar-paper", "2025"),
}

_PLAYLISTS = {
    "pl-focus": ("Deep Focus", ("tr-006", "tr-008", "tr-002", "tr-005")),
    "pl-drive": ("Night Drive", ("tr-003", "tr-001", "tr-007", "tr-004")),
}

_MOODS = (
    ("mo-chill", "Chill", "Moods"),
    ("mo-focus", "Focus", "Moods"),
    ("mo-energy", "Energy", "Moods"),
    ("mo-indie", "Indie", "Genres"),
)


def _track(track_id: str) -> e.Track:
    for tid, title, artist_id, album_id, secs in _TRACKS:
        if tid == track_id:
            return e.Track(
                id=tid, title=title,
                artists=(e.ArtistRef(_ARTISTS[artist_id], artist_id),),
                album=e.AlbumRef(_ALBUMS[album_id][0], album_id),
                duration_s=float(secs))
    raise e.EngineError(e.ErrorKind.NOT_FOUND, track_id)


def _track_item(track: e.Track) -> e.Item:
    return e.Item(e.ItemKind.TRACK, track.id, track.title,
                  track.artist_line, track.art_url, track)


def _album_item(album_id: str) -> e.Item:
    title, artist_id, year = _ALBUMS[album_id]
    return e.Item(e.ItemKind.ALBUM, album_id, title,
                  f"{year} · {_ARTISTS[artist_id]}")


def _artist_item(artist_id: str) -> e.Item:
    return e.Item(e.ItemKind.ARTIST, artist_id, _ARTISTS[artist_id], "Artist")


def _playlist_item(playlist_id: str) -> e.Item:
    title, ids = _PLAYLISTS[playlist_id]
    return e.Item(e.ItemKind.PLAYLIST, playlist_id, title,
                  f"{len(ids)} songs")


class FakeEngine(e.Engine):
    key = "fake"
    name = "Demo music"
    capabilities = e.ALL_CAPABILITIES

    def __init__(self, media_dir: Path | str | None = None,
                 signed_in: bool = False) -> None:
        self.media_dir = Path(media_dir) if media_dir else None
        self.signed_in = signed_in
        self.account = "Demo listener" if signed_in else ""
        self.ratings: dict[str, e.Rating] = {}
        self.calls: list[str] = []
        self._fail: dict[str, e.EngineError] = {}
        self._lock = threading.Lock()

    # -- scripting for tests ------------------------------------------------
    def fail_next(self, method: str, kind: e.ErrorKind,
                  detail: str = "") -> None:
        with self._lock:
            self._fail[method] = e.EngineError(kind, detail)

    def _enter(self, method: str) -> None:
        with self._lock:
            self.calls.append(method)
            error = self._fail.pop(method, None)
        if error is not None:
            raise error

    def _need_account(self) -> None:
        if not self.signed_in:
            raise e.EngineError(e.ErrorKind.AUTH_REQUIRED)

    # -- contract -------------------------------------------------------------
    def status(self) -> e.EngineStatus:
        self._enter("status")
        return e.EngineStatus(
            can_browse=True, can_play=True, signed_in=self.signed_in,
            account_name=self.account,
            components=(
                e.Component("catalog", "Catalog", e.ComponentState.OK,
                            "1.0", needed_for="Search and browse"),
                e.Component("player", "Audio", e.ComponentState.OK,
                            "1.0", needed_for="Playback"),
            ))

    def sign_in(self, credentials: dict[str, str]) -> str:
        self._enter("sign_in")
        if not credentials.get("name"):
            raise e.EngineError(e.ErrorKind.AUTH_REQUIRED, "no name")
        self.signed_in = True
        self.account = credentials["name"]
        return self.account

    def sign_out(self) -> None:
        self._enter("sign_out")
        self.signed_in = False
        self.account = ""

    def search(self, query: str, kind: e.ItemKind | None = None,
               limit: int = 20) -> e.SearchResults:
        self._enter("search")
        needle = query.strip().casefold()
        if not needle:
            return e.SearchResults()
        tracks = tuple(
            _track(tid) for tid, title, artist_id, _al, _s in _TRACKS
            if needle in title.casefold()
            or needle in _ARTISTS[artist_id].casefold())[:limit]
        albums = tuple(_album_item(aid) for aid, (title, artist_id, _y)
                       in _ALBUMS.items()
                       if needle in title.casefold()
                       or needle in _ARTISTS[artist_id].casefold())
        artists = tuple(_artist_item(aid) for aid, name in _ARTISTS.items()
                        if needle in name.casefold())
        playlists = tuple(_playlist_item(pid) for pid, (title, _ids)
                          in _PLAYLISTS.items() if needle in title.casefold())
        results = e.SearchResults(tracks, albums, artists, playlists)
        if kind is None:
            return results
        return e.SearchResults(
            tracks=results.tracks if kind == e.ItemKind.TRACK else (),
            albums=results.albums if kind == e.ItemKind.ALBUM else (),
            artists=results.artists if kind == e.ItemKind.ARTIST else (),
            playlists=(results.playlists
                       if kind == e.ItemKind.PLAYLIST else ()))

    def suggestions(self, query: str) -> list[str]:
        self._enter("suggestions")
        needle = query.strip().casefold()
        names = [t[1] for t in _TRACKS] + list(_ARTISTS.values())
        return [n for n in names if needle and n.casefold().startswith(needle)]

    def home(self) -> list[e.Shelf]:
        self._enter("home")
        return [
            e.Shelf("Quick picks", tuple(_track_item(_track(t[0]))
                                         for t in _TRACKS[:6])),
            e.Shelf("Albums for you", tuple(_album_item(a) for a in _ALBUMS)),
            e.Shelf("Playlists", tuple(_playlist_item(p) for p in _PLAYLISTS)),
        ]

    def moods(self) -> list[e.Mood]:
        self._enter("moods")
        return [e.Mood(mid, title, group) for mid, title, group in _MOODS]

    def mood_shelves(self, mood_id: str) -> list[e.Shelf]:
        self._enter("mood_shelves")
        if mood_id not in {m[0] for m in _MOODS}:
            raise e.EngineError(e.ErrorKind.NOT_FOUND, mood_id)
        return [e.Shelf("Playlists", tuple(_playlist_item(p)
                                           for p in _PLAYLISTS))]

    def charts(self, region: str = "") -> list[e.Shelf]:
        self._enter("charts")
        ranked = sorted(_TRACKS, key=lambda t: t[4], reverse=True)
        return [
            e.Shelf("Top songs", tuple(_track_item(_track(t[0]))
                                       for t in ranked)),
            e.Shelf("Top artists", tuple(_artist_item(a) for a in _ARTISTS)),
        ]

    def library(self, section: e.LibrarySection) -> list[e.Item]:
        self._enter("library")
        self._need_account()
        if section == e.LibrarySection.PLAYLISTS:
            return [_playlist_item(p) for p in _PLAYLISTS]
        if section == e.LibrarySection.ALBUMS:
            return [_album_item(a) for a in _ALBUMS]
        if section == e.LibrarySection.ARTISTS:
            return [_artist_item(a) for a in _ARTISTS]
        if section == e.LibrarySection.LIKED:
            return [_track_item(_track(t)) for t, r in self.ratings.items()
                    if r == e.Rating.LIKE]
        return [_track_item(_track(t[0])) for t in _TRACKS]

    def album(self, album_id: str) -> e.AlbumPage:
        self._enter("album")
        if album_id not in _ALBUMS:
            raise e.EngineError(e.ErrorKind.NOT_FOUND, album_id)
        title, artist_id, year = _ALBUMS[album_id]
        return e.AlbumPage(
            album_id, title, (e.ArtistRef(_ARTISTS[artist_id], artist_id),),
            year, "Album", "",
            tuple(_track(t[0]) for t in _TRACKS if t[3] == album_id))

    def artist(self, artist_id: str) -> e.ArtistPage:
        self._enter("artist")
        if artist_id not in _ARTISTS:
            raise e.EngineError(e.ErrorKind.NOT_FOUND, artist_id)
        own = tuple(_track(t[0]) for t in _TRACKS if t[2] == artist_id)
        return e.ArtistPage(
            artist_id, _ARTISTS[artist_id], "", own,
            tuple(_album_item(a) for a, v in _ALBUMS.items()
                  if v[1] == artist_id),
            (),
            tuple(_artist_item(a) for a in _ARTISTS if a != artist_id),
            own[0].id if own else "")

    def playlist(self, playlist_id: str, limit: int = 200) -> e.PlaylistPage:
        self._enter("playlist")
        if playlist_id not in _PLAYLISTS:
            raise e.EngineError(e.ErrorKind.NOT_FOUND, playlist_id)
        title, ids = _PLAYLISTS[playlist_id]
        return e.PlaylistPage(playlist_id, title, "Demo", "", len(ids),
                              tuple(_track(t) for t in ids[:limit]))

    def radio(self, seed_track_id: str, limit: int = 25,
              exclude: frozenset[str] = frozenset()) -> list[e.Track]:
        self._enter("radio")
        seed = _track(seed_track_id)
        ordered = sorted(
            (t[0] for t in _TRACKS),
            key=lambda tid: (_track(tid).artists != seed.artists, tid))
        return [_track(t) for t in ordered
                if t != seed_track_id and t not in exclude][:limit]

    def lyrics(self, track_id: str) -> e.Lyrics | None:
        self._enter("lyrics")
        track = _track(track_id)
        return e.Lyrics((
            e.LyricLine(f"{track.title}, first line", 0),
            e.LyricLine("second line", 4000),
            e.LyricLine("third line", 8000),
        ), source="Demo lyrics")

    def rate(self, track_id: str, rating: e.Rating) -> None:
        self._enter("rate")
        _track(track_id)
        self.ratings[track_id] = rating

    def resolve(self, track_id: str, *, low_data: bool = False
                ) -> e.StreamSource:
        self._enter("resolve")
        _track(track_id)
        if self.media_dir is None:
            raise e.EngineError(e.ErrorKind.HELPER_MISSING, "no media dir")
        path = self.media_dir / f"{track_id}.ogg"
        if not path.is_file():
            raise e.EngineError(e.ErrorKind.NOT_AVAILABLE, track_id)
        return e.StreamSource(path.as_uri(), mime="audio/ogg")


def create(context) -> FakeEngine:
    """Engine factory (see xx_music.appspec.EngineContext)."""
    media = context.paths.data_dir() / "demo-media" if context else None
    return FakeEngine(media_dir=media)
