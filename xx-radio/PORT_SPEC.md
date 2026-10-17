# xx-radio: spec

An unofficial YouTube Music player for Linux phones. It runs entirely on the
phone and talks only to YouTube. There is no server. GTK4 with the shared
`common/xx_ui` design system. App id `io.piercingxx.XXRadio`.

This file is authoritative for the build. Sections: 1 product, 2 engine
decisions (with package evidence), 3 the engine contract, 4 resilience,
5 architecture, 6 UI, 7 XX-WM integration, 8 milestones, 9 extending
xx-radio, 10 open questions.

Ground rules carried by every stage:

- Never block the GTK main loop. Engine calls, disk scans, image fetches,
  libsecret and D-Bus round trips all run on worker threads
  (`xx_music.tasks.Worker`); yt-dlp runs in a child process.
- The core package `xx_music` is source-agnostic. Only
  `xx_radio/youtube/` may import ytmusicapi or yt-dlp
  (`tests/test_isolation.py` enforces it).
- Tests never touch the network. A socket guard in `tests/conftest.py`
  fails any IP connect; YouTube parsing is tested on recorded, scrubbed
  fixtures, and everything else on `FakeEngine`.
- The UI uses names, never codes: no HTTP statuses, itags, error class
  names or package jargon. "Token helper", not "PO token".

---

## 1. Product

### 1.1 What it does

| Area | Behaviour |
|---|---|
| Search | Songs, albums, artists and playlists with a filter row; suggestions while typing; recent searches as chips. Tapping a song plays it and starts a radio after it. |
| Home | YouTube Music's home shelves (anonymous or personal), plus local shelves: Recently played, Recently liked, Not played in a while, On this phone. Painted from the disk cache instantly, refreshed in the background. |
| Moods & genres | Chip row on Home opens a mood or genre page (playlists). |
| Charts | Top songs and top artists, region from the locale, overridable in Settings. Hidden when YouTube returns nothing for the region. |
| Library (signed in) | Playlists, Songs, Albums, Artists, Liked songs, History. Signed out: Liked (local), History (local) and Downloads only. |
| Pages | Artist (top songs, albums, singles, similar artists, Play and Radio buttons), Album and Playlist (Play, Shuffle, Download, track list). |
| Radio | From any song or artist. Extends itself when 6 or fewer songs are left, from the same original seed, skipping songs already queued and songs heard in the last 48 hours. |
| Queue | Play next, Add to queue, tap to jump, remove, move up/down, shuffle (reset per new queue), repeat off/all/one. Survives restarts (restored paused). |
| Background playback | A separate player service owns GStreamer and MPRIS2, so music keeps playing with the window closed and shows in the XX-WM shade, lock screen and xx-auto. |
| Offline | Download songs, albums and playlists (Wi-Fi only by default). Prefetch the next songs of the queue. One storage cap for both, pinned downloads never evicted. Offline mode plays a mix from what is on the phone. |
| Lyrics | When YouTube has them: synced lines follow playback and tap-to-seek; unsynced lyrics scroll by position. |
| Sleep timer | 15, 30, 45, 60 minutes or End of this song; the last 10 seconds fade out. |
| Ratings | Like / Dislike (YouTube rating when signed in, local otherwise). Disliked songs leave the queue and never return to radios. Also offered as thumbs on xx-auto through `io.piercingxx.XXAuto.Actions1`. |

### 1.2 What carries over from an earlier Android music client

Know-how only; nothing is copied.

Carries over (generic music-player behaviour):

- Queue snapshot persisted on change of index, length, current song,
  playing flag or position rounded to 5 s; writes coalesced; restore paused.
- Prefetch window: 8 songs ahead on unmetered networks, 2 on metered. Never
  fetch the song that is playing a second time; one download at a time,
  nearest first; start only once playback has a healthy buffer; cancel a
  download the moment its song leaves the window; `.part` then atomic rename.
- Skip policy: Previous restarts the song after 3 s or on the first song;
  Next at the end of a finite list stops unless repeat is All.
- Sleep timer choices and the "end of song" mode (pause when ≤ 1.5 s left).
- Playback error policy (section 4.3): drop gone songs, retry network
  errors twice (0.5 s, 1 s) then skip, switch to the offline mix after 3
  different songs in a row fail to connect, pause instead of skipping
  through the whole queue when the failure is not about one song.
- Offline cap model: default 2 GB, always leave free space, evict unpinned
  least-recently-played first, never evict pinned downloads or the
  current song plus the prefetch window.
- Background download gate: pause downloads while the player buffers and
  for 30 s after any tap or song start; while music plays, one song then a
  20 s gap; full speed when idle. Daily cap and cool-downs on refusals.
- Offline mix: up to 24 songs from what is on the phone, the seed artist at
  most 1 in 4, no artist again within 3 places, nothing heard in 48 h.
- Local smart shelves from local history and ratings (likes in the last 30
  days; liked but unplayed for 90 days).
- Art sizing: ask YouTube's image hosts for the size needed (128 px rows
  and mini player, 256 px tiles, 640 px for MPRIS art, 1200 px Now
  Playing); deduplicate identical in-flight fetches; 96 MB disk art cache.
- Look: true-black flat surfaces, mono type, square-ish tiles, 64 px mini
  player with a 2 px progress line, big inverted round play button,
  sweeping 2 px loading line instead of spinners.

Does not carry over: everything that depended on that client's own
backend server or on features beyond playing music (none of it applies to
a phone that talks to YouTube directly), casting, crossfade (playbin has
no cheap twin-player crossfade; revisit later) and multiple accounts (one
optional account here). Radio is YouTube Music's own radio.

---

## 2. Engine decisions

Checked 2026-10-17 against the postmarketOS v26.06 test VM (`apk search`,
x86_64 index), Alpine aports master (for aarch64/armv7 arch lists), Arch
`pacman -Si`, the AUR RPC, and Debian `madison`.

### 2.1 Package evidence

| Need | postmarketOS v26.06 / Alpine | Arch | Debian |
|---|---|---|---|
| ytmusicapi | `py3-ytmusicapi` 1.12.1 (deps: py3-requests) | `python-ytmusicapi` 1.12.3 | `python3-ytmusicapi` 1.12.3 in testing/sid; **not in trixie** |
| yt-dlp | `yt-dlp` 2026.08.19 (`yt-dlp-core` + hard dep on `yt-dlp-ejs`, ffmpeg, mutagen, brotli, websockets, secretstorage) | `yt-dlp` 2026.08.19 (hard dep on `yt-dlp-ejs`) | `yt-dlp` 2026.08.19 in testing and trixie-backports; trixie itself 2025.04.30 (too old). Suggests `deno \| nodejs \| quickjs` |
| yt-dlp-ejs (challenge solver scripts) | `yt-dlp-ejs` 0.8.0, needs virtual `yt-dlp-ejs-rt`: `-rt-deno` (priority 100, x86_64 + aarch64 only), `-rt-quickjs` (20), `-rt-nodejs` (10) | `yt-dlp-ejs` 0.8.0, depends on `deno` | **not packaged** |
| JS runtime | `deno` 2.7.4 (aarch64, x86_64), `nodejs` 24.18.1, `nodejs-current` 26.5.1, `quickjs` 2025-09-13 | `deno` 2.9.7, `nodejs` 26.11.1 (+ lts 20/22/24) | `nodejs` 20.19 in trixie, 24.21 in testing; `quickjs` 2025-04-26; **no deno** |
| PO-token plugin (bgutil) | not packaged | AUR only: `python-bgutil-ytdlp-pot-provider` 1.3.1 (flagged out of date), `bgutil-ytdlp-pot-provider` 0.8.1 (a Rust reimplementation) | not packaged |
| GStreamer | `gst-plugins-base/good/bad`, `gst-libav` 1.28.3, `py3-gst` | 1.28.8, `gst-python` | trixie 1.26.2, testing 1.28.8, `python3-gst-1.0` |
| libsecret | `libsecret` 0.21.7, `gnome-keyring` | `libsecret` 0.21.8.2 | `gir1.2-secret-1` |
| NetworkManager | `networkmanager` | yes | yes |

So on postmarketOS (the main target) and Arch everything except the token
helper is one package install. On aarch64 postmarketOS, `apk add yt-dlp`
already pulls Deno through the solver's provider priority; armv7 gets
QuickJS, which can solve challenges but cannot run the token generator.

### 2.2 Catalog: ytmusicapi from the distribution, in process — DECIDED

ytmusicapi is plain Python over `requests`, packaged everywhere that
matters, and is the catalog: search, suggestions, home, moods, charts,
artist/album/playlist pages, watch playlists (radio), lyrics, library,
ratings. It runs in-process on worker threads (one client per process,
guarded by a lock), in both the window and the player service (the service
needs it for radio extension and for MPRIS thumbs while the window is
closed). Its raw dicts never leave `xx_radio/youtube/`: `parse.py` turns
them into `xx_music.engine` models, and any parse failure (missing key,
wrong type, empty where YouTube always answers) raises
`EngineError(SOURCE_CHANGED)`.

Fallback when it is not packaged (Debian trixie) or too old: the private
engine venv (2.7) installs it with pip; the import path prefers that venv
once it is promoted. Never vendored into the repo: it changes as fast as
YouTube does.

### 2.3 Stream resolution: yt-dlp in a child process — DECIDED

yt-dlp turns a song id into a direct audio URL. It runs as
`python3 -m xx_radio.youtube.resolve_worker` (JSON over stdin/stdout, a 45 s
timeout, killed on timeout, `nice`d), not in-process, because: the JS
challenge work is CPU-heavy and would hold the GIL against the GTK and
D-Bus loops; a hang can be killed; an updated yt-dlp is picked up on the
next resolve without restarting playback; and its memory is returned after
each run. One worker run can resolve several ids (prefetch batches) to
amortise interpreter start-up.

Options per resolve:

- format: `bestaudio[acodec=opus]/bestaudio/best[acodec!=none]`
  (Opus in WebM plays with `gst-plugins-good` + base, no libav needed; AAC
  m4a is the fallback and needs `gst-libav`); on metered networks with
  Data saver on: `bestaudio[acodec=opus][abr<=72]/bestaudio[abr<=96]/bestaudio`.
- `js_runtimes`: the runtime found by the probe (2.4), passed explicitly.
- `plugin_dirs`: the engine dir's plugin folder (token helper, 2.5).
- cookies: signed in only, from a Netscape jar written to
  `$XDG_RUNTIME_DIR/xx-radio/cookies.txt` (0600, tmpfs) from libsecret,
  without the `LOGIN_INFO` cookie (with it some YouTube clients refuse the
  session and list no audio). Deleted on sign-out and service exit.
- player clients: signed in, `tv,web_music` (proven together with cookies
  and a working solver); anonymous, yt-dlp's own default order (it is
  maintained against YouTube's changes). Both overridable in
  `config.json` (`"player_clients"`) for field fixes without a release.
- `remote_components`: off (no code fetched at resolve time). Debian, which
  lacks `yt-dlp-ejs`, gets it from the engine venv instead.

Output: url, the http headers yt-dlp says to send, mime, expiry (the
URL's `expire=` stamp, else 4 h), size when known, and loudness when YouTube
reports it. The URL is bound to the IP that resolved it, so after a network
change (Wi-Fi to mobile) the first 403 re-resolves once and resumes at the
same position.

### 2.4 JS challenge runtime — DECIDED: system Deno, else Node ≥ 22, else QuickJS

YouTube's player challenges need yt-dlp-ejs plus a JS runtime. Probe order
and floors (`xx_radio/youtube/probe.py`): Deno ≥ 2.0, Node ≥ 22 (Node 20
was rejected in the field), Bun ≥ 1.0.31, QuickJS ≥ 2023-12-09; first in
the engine's private `bin/`, then `PATH`. Only Deno and Node also run the
token generator, so the package recommendation is:

- postmarketOS aarch64/x86_64: `yt-dlp` (brings `yt-dlp-ejs-rt-deno`).
- postmarketOS armv7: `yt-dlp` + `nodejs` (Deno does not exist there).
- Arch: `yt-dlp` (brings `deno`).
- Debian: `yt-dlp` from trixie-backports + `nodejs` ≥ 22 (testing has
  24; trixie's 20 is below the floor). See open question 3.

Missing or too old: the Challenge solver row says "Not installed" or
"Needs an update"; playback state is "Playback needs a helper".

### 2.5 PO tokens: bgutil in script mode, set up on demand — DECIDED (spike in M0)

Since 2026-09-21 YouTube lists no audio formats to any yt-dlp client
without a PO token, signed in or not; requests just fail with no audio and
no bot-check message. The bgutil provider mints tokens. It has two halves:
a yt-dlp plugin (pure Python, `bgutil-ytdlp-pot-provider` 2.0.0, GPL-3.0)
and a generator (TypeScript) that runs on Node ≥ 20 or Deno ≥ 2.0 and needs
the `canvas` npm module. Neither half is packaged on Alpine or Debian.

Decision: **script mode** (`bgutil:script-deno` / `bgutil:script-node`). The
generator is spawned only when yt-dlp needs a token, and tokens are cached
for 6 h (`TOKEN_TTL`), so nothing stays resident on the phone. The HTTP
server mode (a resident service on 127.0.0.1:4416) is faster per call but
costs memory all day; it stays an option for later if the spike shows
script mode is too slow.

Set-up is a supported, explicit action, never automatic: Settings →
Playback engine → Token helper → Set up. It downloads the bgutil source at
the pinned tag into `~/.local/share/xx-radio/engine/bgutil/`, installs its
dependencies with the available runtime (`deno install
--allow-scripts=npm:canvas --frozen`, or `npm ci` + `tsc` for Node),
pip-installs the matching plugin into the engine venv (2.7), and passes
`youtubepot-bgutilscript:server_home=<that dir>/server` to yt-dlp. A
progress page shows each step by name; failure leaves the previous state
untouched.

Risk to settle in M0 on real aarch64 hardware: `canvas` on musl may have no
prebuilt binary and need a native build (cairo/pango headers, a compiler).
Fallbacks in order: (a) bgutil HTTP mode in a container is not an option on
a phone; (b) the Rust reimplementation (`bgutil-ytdlp-pot-provider-rs`, a
single binary with the same script/server modes; packaged in the AUR) if
it ships aarch64 musl builds; (c) building canvas once with the build
dependencies, documented. M0 picks one and records the evidence here.

When the token helper is missing or broken: search, browse, library,
lyrics and ratings work; downloaded songs play; streaming a song that is
not on the phone pauses the queue (it does not skip through it) with "Playback
needs a helper that isn't set up" and a Set up button; background downloads
stop. `EngineStatus.can_play` is false and the Token helper row says why.

### 2.6 Sign-in — DECIDED: anonymous by default; browser headers; OAuth optional

- Anonymous: search, home, moods, charts, pages, radio and lyrics all work
  without an account. Likes and history are local.
- Browser headers (recommended): the person copies the request headers of
  any `music.youtube.com` request from a desktop browser's developer tools
  (header block or "Copy as cURL"; both accepted). xx-radio extracts the
  headers (dropping HTTP/2 pseudo-headers), requires a `Cookie` header,
  adds `x-goog-authuser: 0` when missing, and lets ytmusicapi validate
  them. Brand accounts: an optional account picker after sign-in. These
  cookies also go to yt-dlp (2.3), which unlocks age-restricted songs and
  Premium audio for Premium accounts. Cookies expire when the person signs
  out of that browser session; the app then says "Your sign-in expired".
- OAuth (advanced, optional): ytmusicapi's device-code flow. Since November
  2024 it requires the person's own Google Cloud OAuth client id and secret
  ("TVs and Limited Input devices", YouTube Data API enabled); there is no
  shared client id. The UI asks for both, shows the device code and URL,
  then polls. OAuth gives catalog access only: yt-dlp no longer accepts
  OAuth, so streams stay anonymous.
- Storage: libsecret only, via `gi.repository.Secret` on a worker thread.
  Schema `io.piercingxx.XXRadio.Credentials`, attributes
  `{"kind": "browser" | "oauth" | "oauth-client", "account": <label>}`.
  Nothing on disk except the tmpfs cookie jar (2.3); logs redact cookies,
  `Authorization` and SAPISID hashes. No Secret Service running → sign-in
  is refused with "Signing in needs a keyring" (Phosh and XX-WM sessions
  ship gnome-keyring).

### 2.7 Engine updates — DECIDED: a private, promotable venv

YouTube breaks yt-dlp and ytmusicapi often; distribution updates can lag
by weeks. Settings → Playback engine → Update engine:

1. `python3 -m venv --system-site-packages ~/.local/share/xx-radio/engine/venv.new`
2. `pip install --upgrade yt-dlp yt-dlp-ejs ytmusicapi bgutil-ytdlp-pot-provider`
   (pinned floors; an opt-in "Pre-release yt-dlp" switch adds `--pre`).
3. Offline self-check in the new venv (imports, versions), then an online
   check: search one fixed term and resolve one fixed public song.
4. Promote on success (`venv.new` → `venv`, old one kept as
   `venv.previous`), else delete `venv.new` and keep the current state.

"Undo update" swaps `venv.previous` back; "Use system versions" removes the
venv. The resolve worker runs with the venv's python when one is promoted;
the catalog prepends the venv's site-packages before its first import
(window and service restart on promote; the service restarts between
songs). Nothing updates by itself (it downloads and runs code); the app
offers it when the health monitor (4.1) sees YouTube breakage, and the
Stream resolver row says "Needs an update" when yt-dlp is older than 90
days (its versions are dates).

### 2.8 Playback: GStreamer in a separate player service — DECIDED

Process `xx-radio --player-service` (same launcher), bus name
`io.piercingxx.XXRadio.Player`, D-Bus activatable
(`io.piercingxx.XXRadio.Player.service`), never the GUI's app id (the
secondary-instance exit trap). It owns:

- `playbin` with audio-only flags and `autoaudiosink` (PipeWire or
  PulseAudio). `source-setup` puts the StreamSource headers on
  `souphttpsrc` (`user-agent`, `extra-headers`) for that URI's origin only;
  redirects to another origin drop them. Gapless via `about-to-finish`
  with the next song's URI already resolved (prefetch keeps the next
  song's URL fresh or its file local). `StreamSource.gain_db` maps to
  playbin `volume` (loudness levelling, off by default).
- The queue, radio extension, sleep timer, skip and error policies,
  prefetch and downloads (so all of them work with the window closed).
- MPRIS2 on `org.mpris.MediaPlayer2.xx_radio` at `/org/mpris/MediaPlayer2`:
  `org.mpris.MediaPlayer2` (Identity "XX-Radio", DesktopEntry
  `io.piercingxx.XXRadio`, CanRaise activates the GUI) and
  `org.mpris.MediaPlayer2.Player` (PlaybackStatus, LoopStatus, Shuffle,
  Metadata with `mpris:trackid`, `xesam:title/artist/album`,
  `mpris:length`, `mpris:artUrl` as a cached `file://` 640 px image,
  Position, CanGoNext/Previous/Play/Pause/Seek, `Seeked`).
- `io.piercingxx.XXAuto.Actions1` on the same path: `ListActions() →
  a(sssb)` from `AppSpec.player_actions` (Like / Dislike with xx-auto's
  thumb icons), `ActivateAction(s)` runs the handler on a worker. A
  `PropertiesChanged` on the Player interface after any rating makes
  xx-auto re-read the actions.
- Its own API, `io.piercingxx.XXRadio.Player1` at
  `/io/piercingxx/XXRadio/Player` (section 5.4), used by the window.

Idle exit: with an empty queue or Stopped for 60 s and no window
connected, the service exits; paused with a queue, it stays 30 minutes,
saves the queue, then exits (the next start restores it paused).

### 2.9 Offline cache, storage cap, metered networks — DECIDED

- One SQLite index (`~/.local/share/xx-radio/music.db`) for downloads,
  prefetch files, play history, local ratings, lyrics and the home cache.
- Downloads (pinned): `~/.local/share/xx-radio/offline/<engine>/<id>.<ext>`.
  Prefetch (evictable): `~/.cache/xx-radio/audio/<engine>/<id>.<ext>`. The
  original container is kept (WebM/Opus or M4A); no remux, no ffmpeg.
- Cap: default 2 GB, choices 1/2/5/10/20 GB, and always leave 1 GB free
  (effective cap = min(cap, used + free − 1 GB)). Eviction: unpinned,
  least recently played first; never pinned, never the current song or the
  prefetch window. A playlist download pins its songs; removing the
  download unpins them.
- Downloads use `Range` resume on `.part` files, re-resolve on 403/expiry,
  verify the length, then rename. "Keep liked songs offline" (off by
  default) plans liked songs into the cap. No other automatic filling.
- NetworkManager (system bus, `org.freedesktop.NetworkManager`): `Metered`
  (1 yes / 3 guess-yes = metered) and `Connectivity` (4 = full), followed
  through `PropertiesChanged`. Metered: prefetch depth 2, Data saver
  formats (on by default), downloads only if "Wi-Fi only" is off. No
  connectivity: offline mode (offline mix, downloads paused). No
  NetworkManager: unmetered, online.
- Refusal handling (protects the account and the IP): 3 bot-check or
  forbidden answers within 15 minutes stop background downloads for 1 hour;
  at most 200 background downloads a day; a song that is gone is never
  retried; a song YouTube refused is retried after 24 h.

---

## 3. The engine contract

`src/xx_music/engine.py` (in the scaffold). An engine is a subclass of
`xx_music.engine.Engine`:

```python
class Engine:
    key: str                 # stable, lowercase; part of every cache key
    name: str                # shown to people ("YouTube Music")
    capabilities: frozenset  # SEARCH SUGGEST HOME MOODS CHARTS LIBRARY RADIO
                             # LYRICS RATE SIGN_IN STREAM
    def status(self) -> EngineStatus
    def sign_in(self, credentials: dict[str, str]) -> str      # account name
    def sign_out(self) -> None
    def search(self, query, kind: ItemKind | None = None, limit=20) -> SearchResults
    def suggestions(self, query) -> list[str]
    def home(self) -> list[Shelf]
    def moods(self) -> list[Mood]
    def mood_shelves(self, mood_id) -> list[Shelf]
    def charts(self, region="") -> list[Shelf]
    def library(self, section: LibrarySection) -> list[Item]
    def album(self, album_id) -> AlbumPage
    def artist(self, artist_id) -> ArtistPage
    def playlist(self, playlist_id, limit=200) -> PlaylistPage
    def radio(self, seed_track_id, limit=25, exclude=frozenset()) -> list[Track]
    def lyrics(self, track_id) -> Lyrics | None
    def rate(self, track_id, rating: Rating) -> None
    def resolve(self, track_id, *, low_data=False) -> StreamSource
    def art_url(self, url, px) -> str
```

Rules:

1. **Plain types only.** Arguments and results are `str`, numbers, enums and
   the frozen dataclasses in `engine.py` (`Track`, `Item`, `Shelf`, `Mood`,
   `SearchResults`, `AlbumPage`, `ArtistPage`, `PlaylistPage`, `Lyrics`,
   `StreamSource`, `EngineStatus`, `Component`). No library object, raw
   dict or HTTP response crosses the interface. `Track` round-trips through
   `to_dict`/`from_dict` (queue file, D-Bus JSON).
2. **Ids are opaque** and unique within one engine. The core keys
   everything by `(engine.key, id)`.
3. **Blocking, thread-safe, cheap to construct.** Every method may block and
   is only called on worker threads. Constructors do no I/O and no heavy
   imports (the window builds the engine on the main loop).
4. **One exception type.** Failures raise `EngineError(kind, detail,
   retry_after_s)`. Kinds: `OFFLINE`, `TIMEOUT`, `AUTH_REQUIRED`,
   `AUTH_EXPIRED`, `NOT_FOUND`, `NOT_AVAILABLE`, `RATE_LIMITED`,
   `SOURCE_CHANGED`, `HELPER_MISSING`, `UNSUPPORTED`, `OTHER`. The UI shows
   `ERROR_TEXT[kind]`; `detail` goes to the log only. Anything else that
   escapes is a bug and is logged as `OTHER`.
5. **Capabilities gate the UI.** A page or button whose capability is
   missing is hidden, never greyed out with an error. The base class
   raises `UNSUPPORTED` for everything not overridden.
6. **`resolve` returns where to fetch, not bytes.** `StreamSource.uri` is
   http(s) or file; `headers` go with every request to that origin only;
   `expires_at` (unix time, 0 = never) drives re-resolution; `gain_db` is
   optional. Downloads and playback both use it, so an engine never
   implements downloading.
7. **`status` never raises** for missing pieces; it reports them as
   `Component` rows (`OK`, `MISSING`, `OUTDATED`, `BROKEN`, `OPTIONAL`)
   with `can_browse` / `can_play`.
8. **Versioned.** `xx_music.ENGINE_API` is bumped on any incompatible
   change; an engine may assert the value it was written for.

`FakeEngine` (`xx_music/fake_engine.py`) implements the whole contract over
an invented catalog, serves `file://` media from a chosen directory, and
can script one failure per method (`fail_next`). It drives every UI,
service and cache test, and the demo screenshots (`XX_RADIO_ENGINE=fake`).

---

## 4. Resilience

### 4.1 One small seam, a health monitor, clear states

- All YouTube knowledge lives in `xx_radio/youtube/` (catalog, parse,
  resolve worker, probe, auth, setup). The core sees only the contract.
- A health monitor in each process counts `SOURCE_CHANGED` and
  `HELPER_MISSING` per capability. Three of the same kind within 10
  minutes raise a banner on Home/Now Playing: "YouTube changed something.
  Search may not work until the engine is updated." with Update engine.
  One success clears it.
- UI states, each one sentence (`ERROR_TEXT`): offline (downloads still
  play), timed out, sign in to see this, sign-in expired, no longer there,
  not available here, limiting requests (with retry time), YouTube changed
  something, playback needs a helper. Page-level errors keep the cached
  content on screen with the sentence as an inline line, never a blank
  page.
- Settings → Playback engine lists the four components by name with state,
  version and what they are for, plus Update engine, Undo update, Token
  helper set-up, and a "Copy diagnostics" action (versions, last 50 log
  lines, redacted).

### 4.2 Classifying yt-dlp failures

`resolve_worker` maps yt-dlp's error text, case-insensitively, before
anything else sees it: "private video", "video unavailable", "removed by"
→ `NOT_FOUND`; "members-only", "join this channel" → `NOT_AVAILABLE`
(never blamed on the account); "confirm your age", "age-restricted" →
`AUTH_REQUIRED` (sign in) or `NOT_AVAILABLE` (signed in already; matched
before the generic sign-in phrases); "sign in to confirm you're not a
bot", "http error 403" → `RATE_LIMITED`; "requested format is not
available", "only images are available" (no audio listed) → `HELPER_MISSING`
when the token helper is not ready, else `SOURCE_CHANGED`; "unable to
extract", "nsig", "signature" → `SOURCE_CHANGED`; timeouts → `TIMEOUT`;
DNS/connection errors → `OFFLINE`. The table lives in one function with a
fixture test per row.

### 4.3 Playback error policy (player service)

| Failure | Action |
|---|---|
| `NOT_FOUND`, `NOT_AVAILABLE`, decode error | Drop the song from the queue (and its broken file), play the next |
| `TIMEOUT`, `OFFLINE`, connection reset | Retry after 0.5 s and 1 s, then skip (keep it in the queue) |
| HTTP 403 / expired URL while streaming | Re-resolve once and resume at the same position, then skip |
| `RATE_LIMITED` | Retry after `retry_after_s` (clamped 15–600 s); background downloads cool down |
| `HELPER_MISSING`, `SOURCE_CHANGED`, `AUTH_EXPIRED` | Pause and show the state; do not walk the queue |
| 3 different songs in a row fail to connect | Offline mode: rebuild the rest of the queue as the offline mix |

Counters reset on the first song that plays. Only one song left: skip
becomes pause.

### 4.4 Updating yt-dlp is supported

Section 2.7. The distribution package stays the baseline; the private venv
is the fast path; both are visible and reversible in Settings.

---

## 5. Architecture

### 5.1 Processes

| Process | Bus name | Owns |
|---|---|---|
| Window (`xx-radio`) | `io.piercingxx.XXRadio` (GApplication) | GTK4 UI, browse/search engine calls, image cache, settings edits |
| Player service (`xx-radio --player-service`) | `io.piercingxx.XXRadio.Player` and `org.mpris.MediaPlayer2.xx_radio` | GStreamer, queue, radio, sleep timer, prefetch, downloads, cache index writes, MPRIS2 + Actions1 |
| Resolve worker (`python3 -m xx_radio.youtube.resolve_worker`) | none | one yt-dlp run, JSON in/out, then exits |

The service is the single writer of the queue, history and cache tables;
the window reads them and asks the service for changes (same single-writer
rule as the other xx apps). Both processes build their own engine from the
same `AppSpec` (`role="ui"` / `role="player"`).

### 5.2 Threading

- Window: GLib main loop. `xx_music.tasks.Worker` (3 threads) for engine
  calls; a separate 4-thread art loader with in-flight de-duplication.
  Results come back via `GLib.idle_add`; each page holds a `Generation` so
  late answers to an old search or a left page are dropped.
- Service: GLib main loop for D-Bus and the GStreamer bus watch; a worker
  pool for engine calls and resolves (child processes); one download
  thread governed by the playback gate. No asyncio.
- `xx-wm/scripts/check_mainloop_blocks.py` style lint is added in M3 to
  flag blocking calls (subprocess, `requests`, `time.sleep`, `Secret.*_sync`)
  reachable from GTK callbacks.

### 5.3 Packages and files

```
xx-radio/
  PORT_SPEC.md  README.md  meson.build  ruff.toml
  data/  xx-radio.in  io.piercingxx.XXRadio.{desktop.in,metainfo.xml,service.in}
         io.piercingxx.XXRadio.Player.service.in (M3)  icons/
  src/xx_music/                 reusable core, no source library, no app import
    __init__.py                 version, ENGINE_API
    engine.py                   contract, models, errors            (scaffold)
    fake_engine.py              offline demo/test engine            (scaffold)
    appspec.py                  AppSpec, Tab, PlayerAction, factories (scaffold)
    paths.py                    XDG layout per app                  (scaffold)
    tasks.py                    Worker, Generation                  (scaffold)
    store.py                    SQLite index, history, ratings, home cache (M5)
    netstate.py                 NetworkManager metered/connectivity (M6)
    queue.py  policy.py         queue model, skip/error/radio/sleep rules (M3)
    cache.py  downloads.py      cap, eviction, prefetch, Range downloads (M6)
    player/  service.py gst.py mpris.py actions.py api.py client.py   (M3)
    ui/      shell.py (scaffold) home.py search.py library.py pages.py
             nowplaying.py queue_page.py miniplayer.py art.py settings.py
             css.py (app CSS from theme tokens)                     (M4-M8)
  src/xx_radio/                 this app
    main.py                     SPEC (AppSpec) + Adw.Application    (scaffold)
    youtube/ engine.py probe.py (scaffold); catalog.py parse.py art.py (M1)
             resolve_worker.py resolver.py auth.py setup.py (M2, M7)
  tests/  conftest.py (XDG isolation + network guard)  fixtures/ytm/*.json
```

XDG layout (`xx_music.paths.AppPaths("xx-radio")`): config
`~/.config/xx-radio/config.json`; data `~/.local/share/xx-radio/` (`music.db`,
`offline/`, `engine/` with `venv/`, `bgutil/`, `bin/`, `plugins/`); cache
`~/.cache/xx-radio/` (`audio/`, `art/`); state `~/.local/state/xx-radio/`
(`queue.json`, log); runtime `$XDG_RUNTIME_DIR/xx-radio/` (cookie jar).
Leaf directories are 0700.

### 5.4 Player1 D-Bus API (window ↔ service)

`io.piercingxx.XXRadio.Player1` at `/io/piercingxx/XXRadio/Player`. Payloads
are JSON strings of contract models, so the API does not change when an
engine does.

| Member | Signature | Notes |
|---|---|---|
| `Play` | `(s tracks_json, i start, s origin_json)` | replaces the queue; origin = album/playlist/radio seed for "Playing from" |
| `StartRadio` | `(s track_json)` | play the song, then its radio |
| `Enqueue` | `(s tracks_json, b next)` | Play next / Add to queue (dedup by id) |
| `PlayAt`, `Remove` | `(i index)` | |
| `Move` | `(i from, i to)` | |
| `SetShuffle`, `SetRepeat` | `(b)`, `(s none\|all\|one)` | |
| `SetSleep` | `(i seconds)` | 0 off, −1 end of song |
| `Rate` | `(s track_id, s rating)` | through the engine; drops a disliked song |
| `Download`, `RemoveDownload` | `(s items_json)`, `(s id)` | songs, albums, playlists |
| `Snapshot` | `() → s` | state, current song, position, sleep, problem |
| `Queue` | `() → (u version, s tracks_json, i index)` | |
| `Changed` | signal `(s snapshot_json)` | coalesced to ≤ 4 per second |
| `QueueChanged`, `DownloadsChanged` | signal `(u version)` | |
| `Problem` | signal `(s kind, s text)` | an `ErrorKind` and its sentence |

### 5.5 Tests

- Headless pytest, no network (socket guard), XDG isolated per test.
- Contract: `FakeEngine` satisfies every method with the base signatures;
  error sentences contain no codes; isolation test keeps `xx_music` free of
  source libraries and of `xx_radio`.
- YouTube engine: recorded ytmusicapi/yt-dlp outputs in
  `tests/fixtures/ytm/` (scrubbed of cookies, account names, visitor ids),
  one parser test per page type plus deliberately broken copies that must
  yield `SOURCE_CHANGED`; resolve worker tested with a fake `yt_dlp`
  module on `sys.path`; the error table (4.2) row by row; probe tests with
  fake runtimes.
- Service: queue/skip/radio/sleep/error-policy units with a fake clock;
  GStreamer with `fakesink` and short local Ogg files generated in the
  test; MPRIS + Actions1 + Player1 on a private `dbus-daemon`.
- UI: Broadway smoke (real window, every tab, no criticals, 360×720);
  `scripts/snap.py` renders for review; 320 px width check.
- Gate: `sh scripts/check-all.sh xx-radio` (pytest + ruff).

---

## 6. UI

GTK4 + `xx_ui` (`common/DESIGN.md`): flat theme tokens, mono theme font,
display titles 30 px regular, 20 px gutter, hairlines, inverted
selection, caps tabs. App-specific CSS (tiles, cover radii, mini player,
the round play button) is rendered from the same theme tokens by
`xx_music/ui/css.py` and re-rendered from `xx_ui.on_change`, so all eight
presets (amoled, graphite, forest, ocean, paper, mist, aura, burgundy)
and custom switch live. Phone canvas 360×720 portrait, nothing wider than
320 px of content, works at 320 px.

Look taken from the earlier Android client and the xx suite: true-black
(per theme) flat surfaces, square-ish art, no spinners (a 2 px sweeping
line under the header and blinking 8 px squares on busy rows), one-line
muted hints for empty states.

### 6.1 Shell

Bottom tabs (`widgets.bottom_tabs`, 4 equal): HOME · SEARCH · LIBRARY ·
SETTINGS. Extra tabs from `AppSpec.extra_tabs` sit before Settings. Above
the tabs, the mini player (when a queue exists): 64 px row on `surface`,
48 px art (radius 4), title 15 px, artist 13 px muted, play/pause and next
icon buttons (44 px targets), a 2 px `foreground` progress line along the
bottom edge; tap anywhere else opens Now Playing. Only the progress line
redraws per tick.

### 6.2 Screens

- **Home**: `page_header("Home")`, then a chip bar (Moods & genres, Charts,
  then the top moods). Shelves: caption section label + horizontal
  carousel of 120 px square tiles (12 px gap), title 13 px two lines, muted
  11 px subtitle and an "On this phone" tag. Empty shelves are hidden.
  Order: local shelves with content first, then YouTube's.
- **Search**: boxed search bar at the top, recent-search chips under it
  when empty, suggestions while typing, results as segmented filter (Songs
  · Albums · Artists · Playlists) over list rows: 48 px art (radius 4 for
  songs/albums/playlists, circle for artists), title 16 px, muted
  "type · artist · On this phone". Long-press menu: Play next, Add to
  queue, Start radio, Go to artist, Go to album, Download / Remove
  download, Like, Dislike.
- **Artist**: compact header with back chevron; 180 px hero (radius 16);
  Play and Radio buttons; Top songs rows; Albums and Singles carousels
  (132 px tiles, "year · type"); Similar artists (64 px avatars).
- **Album / Playlist**: compact header; 160 px cover + title + "Album ·
  2024 · 12 songs" meta; Play, Shuffle, Download buttons; numbered rows.
- **Now Playing** (slides up over the shell): collapse chevron, then
  text-icon actions Sleep, Lyrics, Queue (accent when active; Sleep shows
  "Sleep 12:34" while armed). Cover square, full width minus 2×28 px,
  radius 16. Title display-large wrapped to two lines, artists as
  separate tappable names (open the artist), album caption. Seek slider
  (foreground thumb/active, border inactive) with tabular 12 px times.
  Thumbs row when the engine can rate. Transport: shuffle 24 · previous 36 ·
  64 px round play button inverted (foreground fill, background glyph) ·
  next 36 · repeat 24 (repeat-one glyph in One mode). "Playing from
  <album/playlist/radio>" caption at the bottom.
- **Lyrics** (inside Now Playing): 72 px cover + title row, then lines;
  the current line 18 px foreground, the others 15 px muted; auto-centre
  that pauses for 3 s after a manual scroll; tap a timed line to seek.
- **Queue**: "Up next" header, current song first in `accent` with
  "Playing · artist", rows with art 48 px; tap to jump; menu Remove, Move
  up, Move down, Play next.
- **Library**: segmented Playlists · Songs · Albums · Artists · On this
  phone (horizontal scroll at 320 px). Signed out: Liked, History and On
  this phone, plus "Sign in to see your YouTube Music library." with a
  Sign in button.
- **Settings**: Account (sign in with browser headers, advanced OAuth,
  brand account, sign out); Playback (Data saver on mobile data, loudness
  levelling, charts region); Downloads (Wi-Fi only, storage limit, keep
  liked songs offline, clear prefetched music, space used); Playback
  engine (component rows, Update engine, Undo update, Use system versions,
  Token helper set-up, Copy diagnostics); About (version, the disclaimer,
  open-source licenses via `widgets.show_licenses_dialog`).
- **Sign in (browser headers)**: three numbered steps in plain words,
  a multi-line entry (focused only when the person taps it), Paste and
  Sign in buttons, the result sentence; never echoes the cookies back.

### 6.3 Screenshots

`tools/snap_all.sh OUTDIR` (from M4) renders every screen with
`XX_RADIO_ENGINE=fake` and a seeded HOME in every preset plus custom at
360×720, and the key screens at 320×640; any `snap: WARNING` (phone-fit
breach) fails it. `tools/demo_seed.py` seeds the queue, history and
downloads.

---

## 7. XX-WM integration

- Desktop id `io.piercingxx.XXRadio` (`DBusActivatable=true`,
  `X-Purism-FormFactor=Workstation;Mobile;`, activation file shipped).
- Shade and lock screen media card and the hardware media keys: through
  MPRIS on `org.mpris.MediaPlayer2.xx_radio`; the card's title tap raises
  the window via `Raise` → GUI activation.
- App standby: the shell never freezes a client whose MPRIS player is
  Playing; the player is a separate process anyway, so closing or
  freezing the window never stops music.
- xx-auto: lists the player (suite players sort first because DesktopEntry
  starts with `io.piercingxx.`), drives transport through MPRIS, and shows
  Like/Dislike from `io.piercingxx.XXAuto.Actions1` with its own thumb
  icons.
- Notifications: download batch finished/failed and sign-in expired only.
  No "now playing" notification (MPRIS covers it).
- `docs/dbus.md` gets the Player1 and Actions1 rows in M3.
- Not added to `xx-wm/scripts/install.sh` until the owner says so.

---

## 8. Milestones

Each stage is ≤ ~2000 changed lines, lands green on
`sh scripts/check-all.sh xx-radio`, and ends with the listed renders
reviewed (real windows via `scripts/snap.py`, not unit tests).

| Stage | Scope | Acceptance | Screenshots |
|---|---|---|---|
| **M0 Engine spike** (≤ 300 lines, `tools/` + spec notes) | On aarch64 postmarketOS: install `yt-dlp py3-ytmusicapi`, set up the token helper with Deno, resolve and play one song with `gst-launch-1.0`; measure resolve time cold/warm, peak RSS, token-helper install size; try the Rust provider; record scrubbed fixtures for M1/M2 | A song plays on the phone from a URL the worker resolved; the token-helper route is chosen and written into 2.5; fixtures committed | none |
| **M1 Catalog engine** | `youtube/catalog.py` (client per process, lock), `parse.py` (search, suggestions, home, moods, charts, album, artist, playlist, watch playlist, lyrics, library), `art.py`, capability flags, health counters | Parser test per fixture; broken fixtures → `SOURCE_CHANGED`; contract signature test passes for `YouTubeMusicEngine`; no network | none |
| **M2 Resolver** | `resolve_worker.py`, `resolver.py` (expiry cache, batching, low-data formats, cookie jar), error table 4.2, engine venv + token helper set-up backends (`setup.py`) | Fake-`yt_dlp` worker tests; every 4.2 row; venv promote/undo with a fake pip; set-up step failures leave state unchanged | none |
| **M3 Player service** | `queue.py`, `policy.py` (skip, radio extension, sleep with fade, error policy, offline mix), `player/` (playbin, gapless, MPRIS2, Actions1, Player1, activation file, idle exit), queue persistence, `docs/dbus.md` rows, main-loop lint | Policy units with fake clock; fakesink playback of generated Ogg files; MPRIS/Actions1/Player1 on a private bus; service survives window exit; xx-auto test lists Like/Dislike | VM: shell shade media card and xx-auto drive screen playing the demo engine |
| **M4 Now Playing** | `ui/css.py`, `miniplayer.py`, `nowplaying.py` (incl. lyrics, sleep sheet), `queue_page.py`, `art.py` loader + disk cache, `ui/client.py` (Player1 proxy), `tools/snap_all.sh`, `tools/demo_seed.py` | Broadway: mini player appears with a queue, Now Playing opens/closes, no criticals; art loader dedupe test; 320 px fit | Now Playing, Lyrics, Queue, mini player over Home: all presets + custom, 360×720 and 320×640 |
| **M5 Browse** | `home.py` (shelves, chips, cached paint, loading line), `search.py` (suggest, recents, filters, long-press menu), `pages.py` (artist, album, playlist), `store.py` (history, ratings, home cache, recent searches) | Home paints from cache with the network guard on; stale-search generation test; capability-hidden UI test | Home, Search (empty, typing, results), Artist, Album, Playlist: all presets |
| **M6 Offline** | `netstate.py`, `cache.py` (cap, free-space floor, eviction), `downloads.py` (Range, .part, gate, refusal cool-down, daily cap), prefetch window, offline mode + mix, On this phone, Downloads settings | Eviction and planner units; resume-after-drop; metered blocks downloads; offline mode switch after 3 failures; gate timing with fake clock | Library › On this phone (active, done, failed), Settings › Downloads, offline banner: amoled + paper |
| **M7 Account** | `youtube/auth.py` (header paste + cURL, x-goog-authuser, brand accounts, OAuth device flow with own client), libsecret store, cookie jar lifecycle, Library tab, ratings sync, history | Header/cURL parser fixtures; secrets never on disk (scan the test HOME); sign-out removes the jar; OAuth flow against a fake token endpoint | Sign-in steps, Library signed in/out, Settings › Account: all presets |
| **M8 Resilience + polish** | Health monitor + banners, Settings › Playback engine UI (update, undo, token helper progress), diagnostics copy, logs with redaction, About + licenses, metainfo screenshots, VM run-through | Banner after 3 `SOURCE_CHANGED` and cleared by a success; update flow end to end with a fake pip; full gate; manual VM checklist | Playback engine (all ready, token helper missing, update running), banners: all presets |

---

## 9. Extending xx-radio

xx-radio is built to be the base of other music apps. Another app reuses
the player service, queue, prefetch/offline cache and core UI, and swaps
or adds only what differs.

### 9.1 Two ways to reuse

- **Import**: install xx-radio (its datadir holds `xx_music` next to
  `xx_radio`), put that directory on the new app's `PYTHONPATH`, and write
  a new package with its own `AppSpec`, launcher, `.desktop`, icon and
  activation files. Different `app_id`, `dir_name` and `mpris_suffix` give
  it its own bus names (`<app_id>.Player`, `org.mpris.MediaPlayer2.<suffix>`)
  and XDG directories, so both apps can be installed side by side.
- **Vendor**: copy `src/xx_music/` at a tagged version into the other
  project and check `xx_music.ENGINE_API` at start-up. `xx_music` has no
  dependency on `xx_radio` (enforced by `tests/test_isolation.py`), so the
  copy is self-contained apart from `xx_ui`.

### 9.2 Extension points (all on `xx_music.appspec.AppSpec`)

- **Engine factory**: `engine_factory(context) -> Engine`, called once per
  process with an `EngineContext` (spec, paths, role `"ui"` or `"player"`).
  `engine_env` + `engine_choices` let a developer pick another engine
  (`"fake"`, or any `"module:callable"`) without a code change.
- **Extra top-level tabs**: `extra_tabs=(Tab(key, title, build), ...)`;
  `build(TabContext)` returns a widget and gets the engine, the worker and
  the shell. Tabs sit between Library and Settings. Keys must be new.
- **Extra player actions**: `player_actions=(PlayerAction(id, label,
  icon_name, handler, enabled), ...)`. The service publishes them through
  `io.piercingxx.XXAuto.Actions1` (`ListActions`/`ActivateAction`) for
  xx-auto and the shell, and Now Playing shows them in its action row.
  Handlers run on a worker in the player service with `(engine, track)`.
- **Planned (M8)**: `settings_sections` for engine-specific settings rows,
  and `home_shelves` providers that add local shelves to Home.

### 9.3 A server-backed engine

An engine that gets search/browse results and stream URLs from an HTTP
server instead of from YouTube needs only the contract:

- catalog methods call the server and map its JSON to the contract models;
- `resolve()` returns the server's audio URL with its auth header in
  `StreamSource.headers` (sent to that origin only) and `expires_at` 0;
- `capabilities` lists only what the server offers (the UI hides the
  rest); `status()` reports reachability and sign-in as `Component` rows;
- its credentials go into libsecret under its own app id's schema.

Prefetch, downloads, offline mode, MPRIS, the queue and every screen then
work unchanged.

### 9.4 Rules for core changes

`xx_music` never imports an app, a source library or an HTTP client;
incompatible contract changes bump `ENGINE_API`; every new extension point
gets a test in `tests/test_appspec.py` with a non-xx-radio spec.

---

## 10. Open questions for the owner

1. **Token helper route**: script mode with Deno as decided, unless M0
   shows `canvas` will not install on the phone. If it will not, is the
   Rust reimplementation acceptable, or a one-time native build?
2. **Automatic engine updates**: xx-radio offers updates but never runs
   pip by itself. Do you want an opt-in "update the engine automatically
   on Wi-Fi" switch?
3. **Debian/Mobian stable**: trixie has no `yt-dlp-ejs`, no Deno and Node
   20. Support it through the engine venv plus a user-installed Node ≥ 22,
   or declare Debian testing the floor?
4. **Lyrics fallback**: YouTube only, or also a public lyrics service
   (LRCLIB) when YouTube has none? That adds a second third-party host.
5. **Account guidance**: the README recommends a throwaway account. Keep
   that wording?
6. **Install**: when should xx-radio join `xx-wm/scripts/install.sh`, and
   should the installer add `yt-dlp py3-ytmusicapi` (and `nodejs` on
   armv7) to the apk list?
7. **Bottom tabs**: Settings as the fourth tab (as above) or a header
   action, freeing the slot for a derived app's extra tab within the
   four-tab rule of DESIGN.md?
