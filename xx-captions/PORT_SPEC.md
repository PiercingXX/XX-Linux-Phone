# xx-captions port spec — on-device live captions + call transcription

Android-style Live Caption and call transcription for Linux phones
running the XX-WM shell. Everything runs ON the device: no audio or
text leaves the phone, ever. There is no cloud fallback, no telemetry,
and no network code in the service.

Targets: ARM64 phones with 4–8 GB RAM and 6–8 cores (Furi FLX1:
MediaTek Dimensity 900, 6 GB; Librem 5: i.MX8M Quad, 3 GB — the
performance canary), plus x86_64 test VMs. Shell integration points
below name `ref/xx-wm` files (`launcher/src/...` unless noted).

## 0. Decision summary

| Question | Answer |
|---|---|
| Default engine | sherpa-onnx streaming Zipformer (true streaming, int8 ~73 MB EN, Apache-2.0) |
| Fallback engine | Vosk small model (true streaming, ~40 MB EN, Apache-2.0) |
| Batch/offline option | whisper.cpp `base`/`small` quantized (not streaming; lane 4) |
| Playback capture | PipeWire monitor of the default sink, capture-only stream |
| Call capture | Voice-call sink monitor (downlink) + default source (uplink), resolved at call start |
| Call transcription default | OFF, explicit opt-in per call or per contact, announced |
| Overlay | `gtk4-layer-shell` OVERLAY, keyboard NONE, empty input region except drag handle |
| Shell toggles | Quick-settings tile (primary), volume HUD entry (secondary) |
| D-Bus API | `io.piercingxx.Captions1` (names/signatures pinned in `src/xx_captions/dbus_api.py`) |
| Privacy indicator | Automatic via `privacy_monitor.py` (capture streams are mic-class nodes) |
| Model install | First-run download with SHA-256, into `~/.local/share/xx-captions/models` |

## 1. Speech-to-text engine

Judged on: true streaming latency (first words < ~1 s), phone CPU,
model size, license, and distro packaging on postmarketOS/Alpine,
Debian/Mobian, and Arch (or a vendorable aarch64 wheel).

### 1.1 Candidates

**sherpa-onnx streaming Zipformer (default).** True online
transducer: encoder/decoder/joiner emit partials as audio arrives, no
sliding-window re-decode. The reference EN model
`sherpa-onnx-streaming-zipformer-en-2023-06-26` is ~73 MB on disk
(int8 encoder + fp32 decoder + joiner + tokens), comparable in size
to one small app asset. Published real-time factor is ~0.05 on an
iPhone 15 Pro with ~45 MB runtime RAM for the streaming Zipformer
path, i.e. single-digit percent of one modern phone core; Raspberry
Pi 4/5 class CPUs decode it in real time with no GPU. License is
Apache-2.0 (bundled onnxruntime is MIT). Distribution is via
`pip install sherpa-onnx`: official wheels cover Linux aarch64 +
x86_64 manylinux (glibc 2.28+) including CPython 3.14, but there is
no musl/Alpine wheel — on postmarketOS/Alpine the documented path is
building against Alpine's system `onnxruntime` package (shared-lib
build, same approach as upstream's musl CI). Debian/Mobian and Arch
take the glibc wheel directly.

**Vosk (fallback).** Kaldi-based, genuine zero-latency streaming API
(`accept-waveform` with partial/result/final + word times). The small
EN model (`vosk-model-small-en-us-0.15`) is ~40 MB with published WER
9.85 on LibriSpeech test-clean; peak RAM is ~0.2 GB and decode runs
at ~10x real time even on weak CPUs. Library and small models are
Apache-2.0 (verify each model's LICENSE at download time). The
`vosk` pip package ships aarch64 glibc wheels; like sherpa-onnx it
has no musl wheel, but the C library is small and builds cleanly for
vendoring. Output is bare lowercase words without punctuation, so
lane 1 pairs it with a tiny on-device punctuator or accepts
unpunctuated call transcripts (documented limitation, not a silent
regression: accuracy/legibility is a listed lane-0 measurement).

**whisper.cpp (offline/batch only, not the live path).** MIT
licensed, ggml quantized models: `tiny` ~75 MB fp16 (~31 MB Q5_1),
`base` ~142 MB (~57 MB Q5_1), `small` ~244 MB; runtime memory about
273 MB / 388 MB / 852 MB respectively before quantization trims it.
The `stream` example is sliding-window re-decode, not true
streaming: first-words latency is seconds, not sub-second, and the
constant re-decode burns phone CPU for zero benefit over a real
online model. It stays valuable as the lane-4 offline fallback
(voicemail-length files, failed-stream re-decode) and as the x86 VM
test oracle. Packaging: an AUR package exists
(`whisper-cpp-sdl 1.9.1-1`, provides `whisper-cpp`); on Alpine the
documented path is building from source; no Debian/Mobian package
was confirmed in this research, so do not depend on one.

### 1.2 Verdict

Default **sherpa-onnx streaming Zipformer**, fallback **Vosk small**,
offline re-decode **whisper.cpp quantized base**. Rationale: only the
first two give true sub-second partials at phone CPU budgets; both
are Apache-2.0 with ~40–73 MB models; both reach every target distro
(wheel on glibc, source build on musl). whisper.cpp loses the live
path on latency/CPU shape, not on quality.

Lane 0 must still measure on-device RTF, first-partial latency, and
peak RSS for all three on the FLX1, the Librem 5, and an x86 VM
before lane 1 cuts the adapter to the winner; the interface in
`src/xx_captions/engine.py` is backend-agnostic so a measurement
upset costs a config change, not a rewrite.

### 1.3 Model install (first run, no network in tests)

1. On first `Start()`, the service checks
   `~/.local/share/xx-captions/models/<name>/` for the catalog's
   file list (`MODEL_CATALOG` in `src/xx_captions/config.py`).
2. Missing files download over HTTPS from the catalog URL with a
   progress callback (overlay shows "Downloading caption model…").
3. Every file is SHA-256 verified before use (`verify_model`);
   mismatch deletes the file and surfaces "model download failed",
   never a half model. Checksums ship in the release manifest and
   are fail-closed: no checksum on file means no load.
4. Models are per-user, read-only after install, and shared by both
   engines' adapters; switching language downloads that language's
   entry only.

## 2. Audio capture on PipeWire

### 2.1 Live captions (system playback)

Capture-only stream on the default sink's monitor, resolved at
session start (pure logic in `src/xx_captions/audio.py`,
`resolve_capture`): `pw-record --target <sink> -P
'{"stream.capture.sink": true, ...}' --rate 16000`
or GStreamer `pipewiresrc target-object=<sink>
stream.capture.sink=true` at 16 kHz mono s16. A sink's monitor is
ports on the sink node itself, not a separate `<sink>.monitor` node
(that name exists only in the PulseAudio view, so targeting it fails
with "no target node available" — verified on PipeWire 1.6.9). A
capture stream never moves, re-links, or re-profiles existing nodes,
so routing is untouched and a captions crash cannot mute the phone.
The default sink name comes from `pactl get-default-sink` at start
(never a numeric id: under pipewire-pulse ids shift between boots —
same rule as `call_ui.py::_pactl_card_names`).

### 2.2 Call transcription (both directions)

On these phones call audio is routed by callaudiod
(`org.mobian_project.CallAudio`, session bus; see
`call_ui.py::CallAudioRouter` with `SelectMode`/`EnableSpeaker`/
`MuteMic`, pactl fallback otherwise) driving an ALSA UCM
voice-call profile. The capture plan does not fight that stack:

- At call start (ModemManager state active, same snapshot the call
  UI renders from), resolve the CURRENT default sink and take the
  sink itself (with `stream.capture.sink=true`) as the
  **downlink** (far end), and the current default source as the
  **uplink** (mic). callaudiod switches profiles
  underneath, so names are re-resolved per call, never cached across
  calls.
- Both are capture-only streams with `stream.capture.sink=true` /
  plain source capture; no loopback, no virtual sink, no profile
  change. If either node is absent the session refuses to start
  (`resolve_capture` returns `{}`) instead of transcribing half a
  call.
- Speaker/earpiece/Bluetooth switches mid-call only change which
  sink is default; lane 2 re-resolves on graph change and notes the
  gap in the transcript ("audio route changed") rather than
  silently attributing voices to the wrong side.

### 2.3 Consent and legal note

Call recording/transcription consent law varies (one-party vs
all-party jurisdictions, plus carrier/region rules the modem stack
cannot know). Therefore: call transcription **defaults OFF**; it
starts only from an explicit per-call or per-contact opt-in; while
active the shell shows a persistent on-screen indicator (the caption
overlay's recording row plus the privacy dot, section 5) and the
user can enable an audible "this call is being transcribed" tone at
call start. Transcripts are per-user files under
`~/.local/share/xx-captions/transcripts/` (mode 0700 dir), never
uploaded, and deleting a transcript is one tap in call details.

## 3. UI integration with XX-WM

### 3.1 Caption overlay

Small always-on-top `gtk4-layer-shell` surface, following the
`hud.py` patterns that already work on phoc 0.56 (layer-shell v3):

- Layer OVERLAY, `KeyboardMode.NONE` (`ON_DEMAND` does not exist on
  v3 — same constraint as every shell surface), exclusive zone 0,
  anchored bottom-left/right, 2–3 wrapped/ellipsized lines at
  360x720 with no horizontal overflow.
- Input: empty input region so touches pass through everywhere
  EXCEPT a 48 px drag handle (the recording row), which keeps a
  region so the user can drag the overlay; position persists in
  `~/.config/xx-captions/config.json`.
- Theme: palette resolved from `~/.config/xx-wm/config.json`
  (`theme` key → preset; unknown key falls back to `amoled`),
  exactly like `hud.Hud`/`quick_actions` resolve through
  `window.resolve_theme`. All colors come from the palette; the only
  fixed color is the recording indicator, which uses the shell's
  semantic danger red. See `src/xx_captions/theme.py`.
- Never grabs focus at show time (no on-screen-keyboard pop, per
  shell rules); all engine/audio work is a worker thread posting
  back with `GLib.idle_add`.

### 3.2 Shell extension points (shell-side lanes, service side ready)

- **Quick settings:** add a `captions` `_TileDef` to
  `quick_actions.py::_TILES` (tier 2), gated like `record` is gated
  by `_recorder_available`: visible only while
  `io.piercingxx.Captions1` owns its bus name; `get_state` reads the
  `Active` property, `set_state` calls `Start("playback")`/`Stop()`
  over async D-Bus. No new tile framework needed.
- **Volume HUD:** `hud.Hud` gains a caption affordance next to the
  volume row (same toggle, smaller footprint) for the "turn on
  captions for this video" moment without opening quick settings.
- **Dialer call details:** the dialer (`dialer.py`) shows a
  transcript row for ended calls that have one, rendered from
  `GetTranscript`; transcripts are opt-in, so most calls show
  nothing and the row hides entirely (no "coming later" placeholders
  anywhere user-facing).

### 3.3 D-Bus API: `io.piercingxx.Captions1`

Bus name `io.piercingxx.Captions1`, object path
`/io/piercingxx/Captions1`, interface `io.piercingxx.Captions1`.
Pinned in `src/xx_captions/dbus_api.py` (introspection XML is the
normative copy):

- `Start(in s source)` — begin capture+transcription.
  `source ∈ {"playback", "call", "mic"}`; anything else is
  `org.freedesktop.DBus.Error.InvalidArgs`. `"call"` additionally
  requires the call-transcription opt-in, else
  `io.piercingxx.Captions1.Error.NotPermitted`.
- `Stop()` — end session, flush final text, idempotent.
- `ListTranscripts(out a(ssxx) transcripts)` — `(id, peer,
  started_unix, ended_unix)` newest first.
- `GetTranscript(in s id, out s peer, out x started, out x ended,
  out a(sx) lines)` — `(text, offset_ms)` per final line;
  unknown id is `NotFound`.
- Signal `Text(s text, b final)` — partials (`final=false`) then
  exactly one final per utterance; clients render partials muted
  and replace on final.
- Properties `Active(b)`, `Source(s)` (read-only).
- Client-side rule (shell trap, see XXWM_RULES.md): every reply
  unpacks to a TUPLE of out-args — `reply.unpack()[0]`, and
  `reply_type` is `GLib.VariantType.new("(…)")` or `None`, never a
  plain string. `dbus_api.unpack_single` encodes the first half.

## 4. Privacy indicator

No new indicator protocol is needed. `privacy_monitor.py` classifies
every running `Stream/Input/Audio` node from `pw-dump --monitor` as
mic use (`classify_node`, `PwDumpParser`, `format_indicator`). Both
capture modes ARE such nodes, so captions appear in the shell
privacy dot and tap-through detail automatically — provided the
service sets honest stream properties: `application.name =
"xx-captions"`, `application.id = "io.piercingxx.Captions1"`,
`media.role = "Accessibility"`. Lane 2's acceptance includes a
`pw-dump`-fixture test that a captions capture node classifies as
`mic` under the REAL `classify_node`, plus a live check that the
dot lights within the monitor's poll interval. If a future
PipeWire gives capture streams a distinct class, lane 2 extends the
shell's class sets rather than forking the monitor.

## 5. Milestone lanes (each ≤ ~1500 lines, with acceptance tests)

- **Lane 0 — device baselines (no ship code).** Measure RTF,
  first-partial latency, peak RSS for sherpa-onnx int8 EN,
  Vosk small EN, whisper.cpp base Q5_1 on FLX1, Librem 5, x86 VM;
  record `pw-dump` graphs idle / playing / in-call per device.
  Done when the numbers confirm or overturn section 1.2 in a dated
  table in this spec's appendix.
- **Lane 1 — engine adapters + model manager (~1200 lines).**
  sherpa-onnx online adapter, Vosk adapter, first-run downloader
  with SHA-256, `engine.py` interface conformance tests, offline
  WAV fixture test per backend (fixedword list, WER ceiling),
  no-network unit gate with injected bytes.
- **Lane 2 — capture + service + store (~1400 lines).**
  PipeWire capture (playback + call pair), D-Bus service
  implementing section 3.3, JSONL transcript store with 0700 dirs,
  consent gate for `"call"`, `dbus-run-session` integration test
  against the real XML, privacy-monitor classification test.
- **Lane 3 — overlay + shell wiring (~1300 lines).**
  Layer-shell overlay per section 3.1, quick-settings tile, HUD
  entry, dialer transcript row, GTK4 API-guard clean
  (`test_gtk4_api_guard.py` equivalent), phone-viewport layout test.
- **Lane 4 — hardening + packaging (~1000 lines).**
  Announce tone, route-change handling, whisper.cpp offline
  re-decode, per-distro install (postmarketOS/Alpine source-build
  note, Debian/Mobian wheel, Arch wheel/AUR), on-device soak
  (1 h captions: battery delta, peak temp, no audio glitches).

## 6. Scaffold in this repo (what runs today)

```
meson.build                  install package + xx-captions launcher + D-Bus service file
src/xx_captions/__init__.py  version (0.1.0)
src/xx_captions/__main__.py  python -m xx_captions --version
src/xx_captions/config.py    pure config/model-catalog/checksum store
src/xx_captions/theme.py     pure XX-WM palette resolution + overlay CSS
src/xx_captions/engine.py    Engine interface + FakeEngine
src/xx_captions/audio.py     pure PipeWire target resolution
src/xx_captions/dbus_api.py  bus names, introspection XML, validators
tests/                       headless pytest (no display, no network, no gi)
```

Gate: `python -m pytest -q tests` and `ruff check src tests` with
the xx-wm venv python. First-run directories are created 0700 before
use; config writes are atomic 0600; D-Bus client code unpacks
`reply.unpack()[0]`; layer work uses keyboard NONE on phoc v3.

## 7. Sources researched

- sherpa-onnx streaming Zipformer size/license/packaging: k2-fsa
  releases via community model indexes (~70 MB EN int8; ~60 MB KO
  int8; bilingual encoder ~50 + decoder ~12 + joiner ~10 MB);
  Apache-2.0 license text in wheels; manylinux aarch64 glibc wheels
  incl. CPython 3.14; musl/Alpine has no compatible wheel (source
  build against system onnxruntime required).
- Streaming Zipformer real-time factor ~0.05, ~45 MB RAM on iPhone
  15 Pro class hardware; real-time on Raspberry Pi 4/5 CPUs, no GPU.
- Vosk small EN: 40 MB, Apache-2.0, WER 9.85 LibriSpeech
  test-clean, true streaming API with partials and word times.
- whisper.cpp models: tiny ~75 MB / base ~142 MB fp16 (Q5_1 ~31 /
  ~57 MB); runtime memory ~273 / 388 / 852 MB tiny/base/small;
  `stream` example is sliding-window, not true streaming; AUR
  `whisper-cpp-sdl 1.9.1-1`; Alpine path is source build; MIT
  license.
- Call audio: callaudiod `org.mobian_project.CallAudio`
  (`SelectMode`/`EnableSpeaker`/`MuteMic`) over ALSA UCM voice-call
  profiles, pactl fallback by card NAME (from `ref/xx-wm`
  `call_ui.py`); Sxmo/postmarketOS migrating audio-mode handling
  toward wireplumber (same capture-only principle applies).
- Shell seams: `quick_actions.py` `_TILES` + `_recorder_available`
  gate + `_tiles()` filtering; `hud.py` OVERLAY/NONE/empty-input
  overlay; `privacy_monitor.py` `classify_node` mic classification;
  `config.py` `THEME_PRESETS` + 0600 atomic save; phoc 0.56 =
  layer-shell v3 only.
