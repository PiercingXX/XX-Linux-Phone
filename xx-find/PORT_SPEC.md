# xx-find PORT SPEC — self-hosted Find My Device for XX-WM phones

Status: **designed and scaffolded, NOT deployed.** Nothing in this repo
runs, reports, or listens until the owner enables it in Settings AND
pairs it with a server. See "Disabled by default" below.

- Phone agent: `xx-find-agent` (Python, systemd user service, shipped inert).
- Server: `xx-find-server` (Python stdlib + PyNaCl only) for the owner's
  Synology NAS, reachable over the owner's Tailscale tailnet.
- Crypto: X25519 sealed boxes (reports) + Ed25519 signed commands (PyNaCl).
- Map: OpenStreetMap tiles rendered server-side. No Google anywhere.

## 0. Disabled by default (non-negotiable)

1. `data/systemd/xx-find-agent.service` has **no `[Install]` section**, so
   no preset, `enable`, or installer can activate it. `meson.build`
   only installs files; it runs no `systemctl`, creates no symlinks,
   and ships no autostart `.desktop` or D-Bus activation file.
2. `AgentConfig.enabled` defaults to `false`; `erase_enabled` defaults
   to `false` independently.
3. `Agent.poll_once()` / `main()` perform zero I/O unless
   `is_operational()` (enabled AND paired: server URL + device id +
   owner verify key all present).
4. No script in this repo pushes anything to a NAS; the Dockerfile is a
   build recipe the owner starts by hand in Container Manager.
5. Tests never touch the real network, GPS, keyring, or session bus
   (socket constructors are monkeypatched to raise; bus/keyring sit
   behind injected seams).

Static tests in `tests/test_disabled.py` fail the build if any of the
above regresses.

## 1. Phone agent (`xx-find-agent`)

Python 3.12+, same flat style as the shell where practical. Runs as a
systemd **user** service (`Type=simple`, `Restart=on-failure`,
hardened: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`,
`ReadWritePaths` for its own config/state only).

### 1.1 Location: GeoClue2 first, ModemManager fallback

**GeoClue2 (primary).** System bus `org.freedesktop.GeoClue2`, Manager at
`/org/freedesktop/GeoClue2/Manager`. The agent creates a Client object,
sets `DesktopId`, calls `Start`, and reads the `Location` properties
(Latitude, Longitude, Accuracy, Timestamp). Authorization is per-app:
unless `/etc/geoclue/geoclue.conf` already pins the app
(`allowed=true/false` skips the agent entirely), the daemon calls the
registered agent's `AuthorizeApp(app_id, req_accuracy)` and enforces
its `MaxAccuracyLevel`
([geoclue.conf man page](https://manpages.ubuntu.com/manpages/focal/en/man5/geoclue.5.html)).
GeoClue serves **exactly one agent system-wide**, and XX-WM already
registers one (`LocationState` in `ref/xx-wm/launcher/src/quick_actions.py`,
same `AuthorizeApp(s,u)->u` / `MaxAccuracyLevel` shape). xx-find therefore
**reuses the shell's agent** instead of registering a second: the shell
is the single writer of location policy, and the agent only ever
requests `EXACT` fixes while handling a `locate` command or heartbeat.
On postmarketOS/Mobian the stock config whitelists the shell
agent; adding xx-find means adding its desktop id to the local
`geoclue.conf` `[agent] whitelist` at image build time, never at
runtime from this repo.

**ModemManager fallback.** When GeoClue has no fix (GPS off, denied, or
indoors), the agent reads the modem's
`org.freedesktop.ModemManager1.Modem.Location` interface: `Setup`
the `3gpp`/`gps-nmea` sources, then `GetLocation` returns 3GPP cell
(MCC/MNC/LAC/CI) and GPS NMEA traces
([mmcli man page](https://www.freedesktop.org/software/ModemManager/man/1.0.0/mmcli.8.html)).
`src/xx_find/location.py` already implements the pure parts: NMEA
GGA/RMC parsing and 3GPP cell normalization; live bus calls stay
behind an injectable provider. Selection rule: GeoClue > modem GPS >
modem cell; ties break on freshest epoch, then smallest accuracy
radius (`pick_fix`, tested).

Every location read emits a `location` event to the shell's privacy
monitor (`privacy_monitor.py`: `refresh_location` / `UsageLog`), so the
indicator and the 24 h "recently used" log stay honest.

### 1.2 Transport: poll, never listen

Phones cannot receive inbound connections reliably (NAT, suspend,
changing tailnet address), so the agent **polls**: `GET
/v1/commands?device=<id>&since=<counter>` over HTTPS to the tailnet
address, plus `POST /v1/report` with the sealed fix. Default cadence:
**15 min idle poll**, **6 min while a command is active**
(ring/message pending), location fix only on `locate` or a **6 h
heartbeat**. Long-poll (60 s server hold) is a later optimization, not
the default: holding the radio awake costs more than a short poll on
these intervals.

**Battery budget (targets, verified on-device in lane 4):** idle cost
< 1 %/day (one ~10 s radio wake per 15 min poll, no GPS); active-locate
session < 3 %/h; low-battery mode (<= 15 %): send one last sealed
report, then stop polling until charging. The agent never holds its
own wake lock; the systemd unit has no `WakeSystem` property.

### 1.3 Actions

- **locate**: take a fix (1.1), seal to the owner key, POST it.
- **ring**: full volume even when silenced/DnD. Path: feedbackd
  `org.sigxcpu.Feedback.TriggerFeedback(app_id, "phone-incoming-call",
  {}, 0)` on the session bus — timeout 0 loops until `EndFeedback`
  ([feedbackd README](https://github.com/PhoshMobi/feedbackd)) — exactly
  how the shell's own `Ringer` in `ref/xx-wm/launcher/src/call_ui.py`
  works, with the `paplay`/`pw-play` loop (`sound.py`) as fallback. DnD
  only silences notification sounds (`banner.py`); alarm-class events
  like the ringer always play, and the find ring reuses that class.
- **message**: text + callback number rendered on the lock screen (4).
- **lock**: lock now via the shell's lock path.
- **erase (OPTIONAL, double-gated)**: stays inert unless the owner
  separately enables it in Settings AND the command's `confirmation`
  field repeats the device id verbatim (typed confirmation, checked in
  `Agent._erase_confirmed` / `erase.validate_erase_confirmation`).
  "Erase" on a Linux phone cannot mean factory reset: it means wipe
  exactly the owner-reviewable list in `src/xx_find/erase.py`
  (`plan_erase`, dry-run only in tests/docs), then power off:
  XX-suite user data: shell + agent dirs (`~/.config/{xx-wm,xx-find}`,
  `~/.local/share/{xx-wm,xx-find}`, `~/.local/state/xx-find`,
  `~/.cache/{xx-wm,xx-find}`) plus every suite app's dirs (txxt
  messages + MMS store, xx-email DB + drafts + outbox, xx-vitals
  metrics, xx-contacts DB + photos, xx-dialer history, xx-calendar,
  xx-share, xx-captions transcripts; entries without an in-tree
  source stay flagged as owner-reviewable TODO lines in the dry-run
  plan), browser profiles **if present**
  (`~/.mozilla/firefox`, `~/.config/chromium`,
  `~/.config/google-chrome`, `~/.config/epiphany`), login keyring
  items owned by the user via per-item Secret Service `Delete` on the
  default collection (xx-find accounts through the `keystore` seam,
  xx-email OAuth tokens and the xx-vitals NAS token by their app
  attribute schemas;
  `~/.local/share/keyrings` files are never removed directly: file
  removal races the daemon's open handles and bypasses its lock, while
  per-item `Delete` stays scoped, locked, and auditable), and queued
  credentials. It never touches the OS, bootloader, modem firmware,
  other users, or removable media (`/media`, `/mnt`, `/run/media`):
  every target must resolve inside `$HOME` (`is_safe_target`), and
  anything else is refused. Dry-run mode only lists what WOULD be
  deleted; it deletes nothing and is the only mode tests and docs
  exercise.

### 1.4 Low-battery last location

At <= 15 % (or on `PrepareForShutdown` with no charger), the agent
takes one final fix, seals and POSTs it with `battery_pct`, and backs
off. The server flags the device "low battery" in the UI.

## 2. Server (`xx-find-server`)

Python stdlib (`http.server` adapter) + **one dependency: PyNaCl**
(needed anyway for owner-side unseal/verify tooling; the store path
itself is pure stdlib). Go was considered and rejected: a single-owner
server needs no concurrency beyond threads, and one language for agent
plus server halves the audit surface.

- **Devices**: id, pairing-token hash, last monotonic counter, last
  **ciphertext** report + epoch. Pairing tokens are shown once.
- **Reports**: stored exactly as received (`{v, device_id, counter,
  epoch, box}`); `submit_report` **rejects** any envelope containing
  `lat/lon/...` keys, so plaintext can never be persisted by accident.
- **Command queue**: owner-signed envelopes per device, monotonic
  counters enforced on queue and on consume.
- **Web UI**: login (PBKDF2 password + random session token), device
  list with last-report time and a server-rendered OSM tile `<img>`
  with a positioned marker (`latlon_to_tile`; no JS map library, no
  Google). Location decryption happens owner-side (a small local
  `xx-find-decrypt` helper in lane 6); the NAS never sees coordinates.
- **Synology**: `server/Dockerfile` (slim Python, distro `python3-nacl`,
  non-root user, listens on 8080) is started **by hand** in Container
  Manager with the published port bound to the NAS **Tailscale IP**
  only — bridge mode reaches `100.x` tailnet addresses through the
  host's `tailscale0` route, so no public port is opened. Plain-Python
  fallback: `python3 src/xx-find-server [store.json]` on any host with
  PyNaCl. TLS terminates at Tailscale (tailnet encryption) plus the
  end-to-end sealed boxes; nothing here provisions certificates.

## 3. Security end to end

- **Reports**: `crypto.seal_report` = libsodium sealed box (X25519 +
  XSalsa20-Poly1305, anonymous sender) to the owner's box public key.
  Only the owner opens them. Chosen library: **PyNaCl**, packaged as
  `python3-nacl` (Debian/Mobian), `python-nacl` (Arch), `py3-nacl`
  (Alpine/postmarketOS — confirm exact name on device); no pip needed.
  `cryptography` remains an untested fallback, not a dependency.
- **Commands**: canonical JSON (see below), Ed25519-signed by the
  owner's signing key. The agent checks signature + `counter >
  last_counter` + `now - skew <= exp <= now + max_age` (defaults
  60 s / 1500 s, above the owner UI TTL of 1200 s: one 900 s idle
  poll plus skew/jitter margin) + device match, then **advances
  and durably persists
  `last_counter` (atomic tmp + fsync + rename) BEFORE dispatching**;
  persist failure refuses the action. Replays, backdated and
  far-future commands are dropped before dispatch; wrong-device
  commands are ignored without consuming the counter; every command
  that passes signature/freshness/device checks consumes its counter
  even when the action itself fails (tested: a failed command cannot
  be replayed; a crash after persist does not re-run on restart).
- **Canonical command bytes** (must match byte-for-byte between the
  Python agent and the JavaScript signer in the server web UI):
  `json.dumps(cmd, sort_keys=True, separators=(",", ":"),
  ensure_ascii=True, allow_nan=False)` encoded as UTF-8/ASCII. That is:
  keys sorted lexicographically by Unicode code point at every nesting
  level; no whitespace; non-ASCII escaped as `\uXXXX` (astral planes as
  surrogate pairs); `/` unescaped; control characters as `\b \f \n \r
  \t` or `\u00XX`. Command `args` is a JSON object whose values are
  **strings, integers and booleans only (no floats, no null)** —
  rejected at build and at validation, including nested values;
  integers must be JS-safe (`-(2**53-1)..2**53-1`); `counter`/`exp`
  must be plain integers (never bool or float). Top-level keys sort to
  `args, counter, device_id, exp, type, v`. Fixed test vectors (exact
  canonical bytes + Ed25519 signature) live in
  `tests/test_protocol.py` (`CANONICAL_VECTORS`) for the JS side to be
  checked against.
- **Pairing**: Settings shows a one-time code (QR + short numeric);
  scanning/entering it on the owner's already-trusted device binds
  `device_id`, server URL, owner verify key, and the device pairing
  token. QR text (`pairing.build_pairing_payload`) is compact JSON
  `{"v":1,"device_id":...,"server_url":...,"verify_key":...,
  "token":...}` (sorted keys, `http(s)://` URL, 32-byte hex verify
  key); unknown fields or versions are rejected on scan. The short
  numeric (`pairing.short_code`, 6 digits) is
  `sha256("xx-find-pairing-code:"+token)[..4] mod 10**6`, a
  confirmation check, not a credential. The signing key never touches
  the phone; the phone's verify key and the device token live in
  **libsecret** (SecretStorage), never in `agent.conf`.
- **Key storage**: libsecret on the phone behind the `keystore`
  seam (`Keystore` interface; `LibsecretKeystore` in production,
  `MemoryKeystore` fake in tests; `SecretRef` is the pointer-only
  account reference). Accounts: `xx-find/device-token`,
  `xx-find/owner-verify-key`. The backend is imported lazily, so
  import and construction never touch D-Bus; owner keys live on the
  owner's machines.

**Threat model:**

- *Stolen phone (locked)*: finder sees only the lock-screen message;
  location history is sealed boxes they cannot open; without the owner
  key, commands cannot be forged. Risk: thief powers off / Faraday bag
  — mitigated only by the low-battery last report.
- *Stolen phone (unlocked) / coercion*: attacker can open Settings and
  disable the agent — this tool is theft-recovery, not coercion-proof.
  Erase is the recourse, and it needs its own prior enablement.
- *Compromised server (NAS breach)*: attacker gets ciphertext reports,
  device ids, and timing — no locations, no command forgery (no
  signing key). They can drop/delay commands (availability only).
- *LAN attacker (same tailnet segment / local network)*: tailnet
  encryption plus sealed/signed payloads; worst case is traffic
  analysis (when the phone polls). Poll jitter (±25 %) is lane-4 work.
- *Someone who knows the server URL*: login is password + session
  token with rate-limited attempts (lane 6); device endpoints need the
  per-device pairing token; both fail closed to 401.

## 4. XX-WM integration

- **Settings section** (`settings_find.py` lane, following the
  `settings_<feature>.py` pattern in `ref/xx-wm/launcher/src/`):
  master toggle **off by default**, pairing button (QR/OTC), server
  URL, device id, last-report time, erase sub-toggle with typed
  confirmation. Follows `settings_page.py` card/row helpers, theme CSS
  from the active preset, no focus stealing.
- **Lock-screen message surface**: reuse the emergency/medical card
  area pattern in `lock_screen.py` (persistent owner message +
  callback number, visible without unlock, styled from the preset).
- **Privacy indicator**: every agent location read calls into the
  shell's `PrivacyMonitor.refresh_location` path, so the `loc`
  indicator lights and `UsageLog` records it like any other app.
- The agent never owns the shell's D-Bus names (cf. XXWM_RULES
  traps: a background service must use its own bus name, e.g.
  `io.piercingxx.Find.Agent`, or the GTK app will refuse to start).

## 5. Milestone plan (lanes of <= ~1500 lines, each with acceptance tests)

| Lane | Scope | Acceptance |
|---|---|---|
| 1 | Spec + scaffold (this lane): `PORT_SPEC.md`, `xx_find` package (`config`, `crypto`, `protocol`, `location`), meson install-but-disabled, Dockerfile, headless pytest + ruff | `pytest -q tests` and `ruff check src tests` green; disabled-by-default static tests present |
| 2 | Transport + server store: long-poll/queue protocol, `Store` persistence, pairing-token flow, owner login sessions | simulated owner/phone round-trip with fakes; replayed/duplicated envelopes rejected |
| 3 | Agent location providers: GeoClue client + MM fallback over injected bus, `pick_fix` on real `mmcli` sample output, privacy events | Broadway-free bus-fake tests; privacy indicator lights in shell harness |
| 4 | Actions: ring via feedbackd path, message/lock surfaces, poll cadence + jitter, battery budget measured on device | on-device power measurement meets 1.2 budgets; DnD ring-through verified |
| 5 | Erase + low battery: wipe list confirmed with owner, typed-confirmation UX, shutdown hook, keyring purge | dry-run erase in VM wipes exactly the listed dirs; low-battery report observed |
| 6 | Server UI + owner tooling: OSM map page, `xx-find-decrypt`, Container Manager doc, rate limiting | UI reviewed in browser; NAS receives ciphertext only (store dump asserted) |
| 7 | XX-WM integration: Settings section, lock-screen message, privacy wiring | Broadway smoke test of Settings + lock screen; no D-Bus name clash test |
| 8 | Hardening audit: fuzz envelopes, skew/counter edge cases, dependency review, on-device soak | fuzz run clean; soak week with no radio/battery anomaly |

## 6. Scaffold map (what this repo contains today)

- `src/xx_find/`: `config.py` (disabled-default config + XDG paths,
  atomic fsync save), `crypto.py` (PyNaCl sealed box + Ed25519
  interface), `protocol.py` (schemas + signature/expiry/counter
  validation, JS-safe canonical args), `location.py` (NMEA/cell
  parsing + fix selection), `agent.py` (gate + persist-before-dispatch
  command handling), `server.py` (ciphertext store, command queue, pure
  HTTP handler, OSM tile math), `pairing.py` (QR bind bundle + short
  numeric check code), `keystore.py` (libsecret seam + memory fake).
- `src/xx-find-agent`, `src/xx-find-server`: launcher scripts.
- `data/systemd/xx-find-agent.service`: installed, no Install section.
- `server/Dockerfile`: Container Manager recipe + plain-Python fallback.
- `tests/`: 80 headless tests; socket constructors raise, no bus/GPS/
  keyring/network. Crypto vectors: RFC 8032 s.7.1 TEST 1, verified
  against both PyNaCl and `cryptography`, plus fixed canonical-bytes +
  signature vectors for the JS signer cross-check.
- Gate: `python -m pytest -q tests`, `ruff check src tests` with the
  xx-wm venv Python.

