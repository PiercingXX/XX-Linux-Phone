# M0 — engine spike on real hardware (2026-10-17)

Run on a FuriPhone FLX1: FuriOS "forky" (Debian 14 testing base), aarch64,
Node 24.15, GStreamer 1.28.3. These are measured facts, not plans. Where
they disagree with PORT_SPEC.md, they win, and the spec is updated to match.

## What works

- **Catalog, anonymous:** ytmusicapi search, suggestions, artist, album,
  playlist, watch playlist, lyrics, home and moods all work without sign-in.
  `tests/fixtures/ytmusic/*.json` are real responses recorded on the phone
  with ytmusicapi 1.12.3. In `lyrics.json` the lyric text is replaced by
  placeholder lines (copyright); its structure is real.
- **Stream resolution:** yt-dlp 2026.08.19 with `--js-runtimes node
  --remote-components ejs:github` returns `bestaudio` = format 251
  (Opus in WebM, ~135 kbps). It needs **no PO token** today.
- **Full download:** yt-dlp downloads the whole 5.9 MB track in about 5 s
  (its own chunked requests, `http_chunk_size` 10 MiB). GStreamer 1.28 then
  decodes it with matroskademux + opusdec. gst-libav is not needed.
- **Token helper:** bgutil-ytdlp-pot-provider 1.2.2 in script mode builds
  and runs on the phone with Node 24 (`npm ci`, `npx tsc`). `canvas`
  loads. yt-dlp retrieved a GVS PO token through it. It is not required
  today (see above), so it stays the optional fallback the spec describes.

## What fails, and the decision that follows

1. **The distro packages are too old to stream.**
   - Debian's yt-dlp 2026.06.09 resolves a URL, but every request past about
     256 KB returns 403, token or not. 2026.08.19 works.
   - Debian's ytmusicapi 1.11.5 crashes in `get_watch_playlist`
     (`KeyError: 'endpoint'`, a YouTube response change). 1.12.3 works.

   **Decision:** the private engine venv (`--system-site-packages`, newer
   ytmusicapi + yt-dlp from PyPI) is **required** on Debian-based phones, not
   an option. First run must offer "Set up playback engine" and work without
   root. FuriOS ships neither `python3-venv` nor `pip`, so create the venv
   with a bundled or downloaded `uv` (static binary; `uv venv
   --system-site-packages` + `uv pip install`) or another root-free path.
   Check pmOS and Arch the same way before assuming their packages are fresh
   enough.

2. **Direct URL playback stalls.** `souphttpsrc` on the resolved googlevideo
   URL, even with yt-dlp's headers, either stops with a stream error or hangs
   after the first chunk. googlevideo enforces chunked fetches.

   **Decision:** the player never hands the raw googlevideo URL to playbin.
   The engine serves audio through a local loopback range proxy owned by the
   player service. Its upstream side fetches in ≤ 10 MiB chunks the way yt-dlp
   does (yt-dlp's chunk parameters plus its `http_headers`), and it re-resolves
   on 403/expiry. Its downstream side gives playbin a seekable
   `http://127.0.0.1:<port>/<id>` with Range support. The same proxy writes
   the offline/prefetch cache. A fully cached track is served from disk.

3. **Tooling gaps on FuriOS:** no `curl`, no `pip`, no `python3-venv`, and
   `/usr/bin/time` is missing; `wget` and `git` exist. Anything the installer
   or first-run setup needs must use `python3` stdlib or `wget`.

## Apt packages that are fine as-is (FuriOS forky)

`nodejs` 24, `npm`, `gstreamer1.0-plugins-good/-bad/-base`, `gir1.2-gst*`,
`python3-gi`, libsecret, gtk4-layer-shell, libadwaita.
