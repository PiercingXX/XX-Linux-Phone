"""The music-source engine contract (see PORT_SPEC.md "Engine contract").

One small, source-agnostic interface between the player core and wherever
music comes from. Engines return only the plain dataclasses below; no
library types (ytmusicapi dicts, yt-dlp info dicts, HTTP responses) ever
cross it. Every method is blocking and is called from a worker thread,
never from the GTK main loop. Failures raise EngineError with a kind the
UI turns into a sentence; engines never raise anything else on purpose.

Ids are opaque strings, unique within one engine. The core keys caches by
(engine.key, id), so two engines can never collide.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any

# ---- capabilities ----------------------------------------------------------

SEARCH = "search"
SUGGEST = "suggest"
HOME = "home"
MOODS = "moods"
CHARTS = "charts"
LIBRARY = "library"
RADIO = "radio"
LYRICS = "lyrics"
RATE = "rate"
SIGN_IN = "sign-in"
STREAM = "stream"

ALL_CAPABILITIES = frozenset({
    SEARCH, SUGGEST, HOME, MOODS, CHARTS, LIBRARY, RADIO, LYRICS, RATE,
    SIGN_IN, STREAM,
})


class ItemKind(enum.StrEnum):
    TRACK = "track"
    ALBUM = "album"
    ARTIST = "artist"
    PLAYLIST = "playlist"
    MOOD = "mood"


class Rating(enum.StrEnum):
    NONE = "none"
    LIKE = "like"
    DISLIKE = "dislike"


class LibrarySection(enum.StrEnum):
    PLAYLISTS = "playlists"
    SONGS = "songs"
    ALBUMS = "albums"
    ARTISTS = "artists"
    LIKED = "liked"
    HISTORY = "history"


# ---- errors ----------------------------------------------------------------

class ErrorKind(enum.StrEnum):
    OFFLINE = "offline"
    TIMEOUT = "timeout"
    AUTH_REQUIRED = "auth-required"
    AUTH_EXPIRED = "auth-expired"
    NOT_FOUND = "not-found"
    NOT_AVAILABLE = "not-available"
    RATE_LIMITED = "rate-limited"
    SOURCE_CHANGED = "source-changed"
    HELPER_MISSING = "helper-missing"
    UNSUPPORTED = "unsupported"
    OTHER = "other"


#: One sentence per kind, shown in the UI. Names, never codes.
ERROR_TEXT: dict[ErrorKind, str] = {
    ErrorKind.OFFLINE: "You're offline. Downloaded music still plays.",
    ErrorKind.TIMEOUT: "The music service took too long to answer.",
    ErrorKind.AUTH_REQUIRED: "Sign in to see this.",
    ErrorKind.AUTH_EXPIRED: "Your sign-in expired. Sign in again.",
    ErrorKind.NOT_FOUND: "This is no longer there.",
    ErrorKind.NOT_AVAILABLE: "This isn't available here.",
    ErrorKind.RATE_LIMITED: "The music service is limiting requests. "
                            "Trying again soon.",
    ErrorKind.SOURCE_CHANGED: "The music service changed something. "
                              "Updating the playback engine may fix it.",
    ErrorKind.HELPER_MISSING: "Playback needs a helper that isn't set up. "
                              "See Settings, Playback engine.",
    ErrorKind.UNSUPPORTED: "This music source can't do that.",
    ErrorKind.OTHER: "Something went wrong.",
}

#: Kinds worth retrying later without user action.
TRANSIENT = frozenset({ErrorKind.OFFLINE, ErrorKind.TIMEOUT,
                       ErrorKind.RATE_LIMITED})


class EngineError(Exception):
    """The only exception an engine raises on purpose."""

    def __init__(self, kind: ErrorKind, detail: str = "",
                 retry_after_s: float | None = None) -> None:
        super().__init__(detail or kind.value)
        self.kind = ErrorKind(kind)
        self.detail = detail
        self.retry_after_s = retry_after_s

    @property
    def user_text(self) -> str:
        return ERROR_TEXT[self.kind]

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT


# ---- models ----------------------------------------------------------------

@dataclass(frozen=True)
class ArtistRef:
    name: str
    id: str = ""


@dataclass(frozen=True)
class AlbumRef:
    title: str
    id: str = ""


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artists: tuple[ArtistRef, ...] = ()
    album: AlbumRef | None = None
    duration_s: float | None = None
    art_url: str = ""
    explicit: bool = False

    @property
    def artist_line(self) -> str:
        return ", ".join(a.name for a in self.artists if a.name)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Track:
        album = raw.get("album")
        return cls(
            id=str(raw["id"]),
            title=str(raw.get("title") or ""),
            artists=tuple(ArtistRef(name=str(a.get("name") or ""),
                                    id=str(a.get("id") or ""))
                          for a in raw.get("artists") or ()),
            album=(AlbumRef(title=str(album.get("title") or ""),
                            id=str(album.get("id") or ""))
                   if isinstance(album, dict) else None),
            duration_s=(float(raw["duration_s"])
                        if raw.get("duration_s") is not None else None),
            art_url=str(raw.get("art_url") or ""),
            explicit=bool(raw.get("explicit")),
        )


@dataclass(frozen=True)
class Item:
    """A card on a shelf or a row in results: anything you can open."""
    kind: ItemKind
    id: str
    title: str
    subtitle: str = ""
    art_url: str = ""
    track: Track | None = None  # set when kind is TRACK


@dataclass(frozen=True)
class Shelf:
    title: str
    items: tuple[Item, ...]


@dataclass(frozen=True)
class Mood:
    id: str
    title: str
    group: str = ""


@dataclass(frozen=True)
class SearchResults:
    tracks: tuple[Track, ...] = ()
    albums: tuple[Item, ...] = ()
    artists: tuple[Item, ...] = ()
    playlists: tuple[Item, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.tracks or self.albums or self.artists
                    or self.playlists)


@dataclass(frozen=True)
class AlbumPage:
    id: str
    title: str
    artists: tuple[ArtistRef, ...] = ()
    year: str = ""
    kind_label: str = "Album"
    art_url: str = ""
    tracks: tuple[Track, ...] = ()


@dataclass(frozen=True)
class ArtistPage:
    id: str
    name: str
    art_url: str = ""
    top_tracks: tuple[Track, ...] = ()
    albums: tuple[Item, ...] = ()
    singles: tuple[Item, ...] = ()
    related: tuple[Item, ...] = ()
    radio_seed: str = ""  # a track id that seeds this artist's radio


@dataclass(frozen=True)
class PlaylistPage:
    id: str
    title: str
    author: str = ""
    art_url: str = ""
    track_count: int | None = None
    tracks: tuple[Track, ...] = ()
    editable: bool = False


@dataclass(frozen=True)
class LyricLine:
    text: str
    start_ms: int | None = None  # None for unsynced lyrics


@dataclass(frozen=True)
class Lyrics:
    lines: tuple[LyricLine, ...]
    source: str = ""

    @property
    def synced(self) -> bool:
        return bool(self.lines) and all(
            line.start_ms is not None for line in self.lines)


@dataclass(frozen=True)
class StreamSource:
    """Where the player (or a download) fetches one track's audio.

    ``uri`` is http(s) or file. ``headers`` ride on every request for it
    (user agent, cookies-free auth headers of a server-backed engine).
    ``expires_at`` is a unix time after which the uri must be resolved
    again; 0 means it does not expire.
    """
    uri: str
    headers: dict[str, str] = field(default_factory=dict)
    mime: str = ""
    expires_at: float = 0.0
    size_bytes: int | None = None
    gain_db: float | None = None  # loudness correction, when known


class ComponentState(enum.StrEnum):
    OK = "ok"
    MISSING = "missing"
    OUTDATED = "outdated"
    BROKEN = "broken"
    OPTIONAL = "optional"  # absent, and the engine works without it


#: User-facing state names.
COMPONENT_STATE_TEXT: dict[ComponentState, str] = {
    ComponentState.OK: "Ready",
    ComponentState.MISSING: "Not installed",
    ComponentState.OUTDATED: "Needs an update",
    ComponentState.BROKEN: "Not working",
    ComponentState.OPTIONAL: "Not set up",
}


@dataclass(frozen=True)
class Component:
    """One piece an engine depends on, for Settings > Playback engine."""
    key: str
    name: str
    state: ComponentState
    version: str = ""
    detail: str = ""
    needed_for: str = ""  # "Search and browse", "Playback", ...

    @property
    def state_text(self) -> str:
        return COMPONENT_STATE_TEXT[self.state]


@dataclass(frozen=True)
class EngineStatus:
    can_browse: bool
    can_play: bool
    signed_in: bool = False
    account_name: str = ""
    components: tuple[Component, ...] = ()


# ---- the contract ----------------------------------------------------------

class Engine:
    """Base class every engine subclasses. Defaults say "unsupported".

    Subclasses set ``key`` (stable, lowercase; part of cache keys),
    ``name`` (shown to people) and ``capabilities``, then override the
    methods those capabilities name. All methods block; call them from
    worker threads only.
    """

    key: str = "engine"
    name: str = "Music"
    capabilities: frozenset[str] = frozenset()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def _unsupported(self, what: str):
        raise EngineError(ErrorKind.UNSUPPORTED, f"{self.key}: {what}")

    # status and account
    def status(self) -> EngineStatus:
        return EngineStatus(can_browse=False, can_play=False)

    def sign_in(self, credentials: dict[str, str]) -> str:
        """Store credentials (libsecret) and return the account name."""
        self._unsupported("sign_in")

    def sign_out(self) -> None:
        self._unsupported("sign_out")

    # catalog
    def search(self, query: str, kind: ItemKind | None = None,
               limit: int = 20) -> SearchResults:
        self._unsupported("search")

    def suggestions(self, query: str) -> list[str]:
        return []

    def home(self) -> list[Shelf]:
        self._unsupported("home")

    def moods(self) -> list[Mood]:
        self._unsupported("moods")

    def mood_shelves(self, mood_id: str) -> list[Shelf]:
        self._unsupported("mood_shelves")

    def charts(self, region: str = "") -> list[Shelf]:
        self._unsupported("charts")

    def library(self, section: LibrarySection) -> list[Item]:
        self._unsupported("library")

    def album(self, album_id: str) -> AlbumPage:
        self._unsupported("album")

    def artist(self, artist_id: str) -> ArtistPage:
        self._unsupported("artist")

    def playlist(self, playlist_id: str, limit: int = 200) -> PlaylistPage:
        self._unsupported("playlist")

    def radio(self, seed_track_id: str, limit: int = 25,
              exclude: frozenset[str] = frozenset()) -> list[Track]:
        self._unsupported("radio")

    def lyrics(self, track_id: str) -> Lyrics | None:
        return None

    def rate(self, track_id: str, rating: Rating) -> None:
        self._unsupported("rate")

    # playback
    def resolve(self, track_id: str, *, low_data: bool = False
                ) -> StreamSource:
        self._unsupported("resolve")

    def art_url(self, url: str, px: int) -> str:
        """The artwork url sized for ``px`` square pixels (default: as is)."""
        return url
