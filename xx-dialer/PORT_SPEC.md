# XX-Dialer on Linux — Port Spec

Target: postmarketOS / Mobian / FuriOS / PureOS phones running phoc
(XX-WM or Phosh). 360x720 logical px (min 320x640), touch-first, on-screen
keyboard. Stack: Python 3.12+, GTK4 + libadwaita via PyGObject
(`Adw.Application`, `Adw.NavigationView`), meson install, stdlib + GI only.

Source: the PiercingXX Android dialer in `ref/xx-dialer/` (package
`com.piercingxx.xxdialer`, GrapheneOS target; the design authority is
`ref/xx-dialer/design.md`, cited as `§N` below). Shell conventions from
`ref/xx-wm/` (`design.md`, `docs/config.md`,
`launcher/src/modem_monitor.py`, `contacts.py`, `notif_daemon.py`,
`call_ui.py`, `dialer.py`, `sms.py`, `default_layout.py`, `config.py`).

Headline: the Android app is a ring-policy engine wearing a phone app
("spam never rings, starred always rings, everyone else only in their
window"). The Linux port keeps that identity: a **pure-Python policy
engine** (ported from `core/` verbatim in behavior) plus a full dialer UI
on ModemManager Voice, with the XX-WM shell defined as the lock-screen /
incoming-call surface and this app owning everything else.

## 1. Feature inventory of the Android app

Every item cites the Kotlin file(s) that implement it. `app/` paths below
are under `ref/xx-dialer/app/src/main/java/com/piercingxx/xxdialer/`,
`core/` paths under `ref/xx-dialer/core/src/main/kotlin/com/piercingxx/xxdialer/`.

### 1.1 Screens

| Screen | Kotlin | What it does |
|---|---|---|
| Recents (home tab) | `ui/RecentsActivity.kt`, `ui/RecentsMerge.kt`, `ui/RecentsFilterIntent.kt` | Starred strip + call log with All / Missed / Silenced / Blocked chips; every screened row shows its verdict (`✓` rang, `→ Silenced · reason`, `✗ Blocked · pattern`); row actions call back / add to contacts / block / "Ring next time" (star) / "Add to Business"; consecutive same-number grouping is a setting (default on) |
| Keypad (center tab) | `ui/KeypadActivity.kt`, `ui/T9.kt`, `ui/NumberDisplay.kt`, `ui/MaskBuilder.kt` | Space Mono digits, match-as-you-type against the contact mirror (digits + T9 letters); long-press 1 dials voicemail; `SupervisoryTone.kt` for DTMF |
| People | `ui/PeopleActivity.kt`, `ui/PeopleGroupPicker.kt`, `ui/GroupGlyphs.kt` | Contact mirror sectioned ★ Starred / Business / Everyone; per-contact sheet with number(s), tier controls, custom-ringtone note, call/history actions; states plainly why it is empty under Contact Scopes |
| Rules | `ui/RulesActivity.kt`, `ui/RulesFocus.kt`, `ui/RulesScreenVoicemail.kt`, `ui/PrecedenceRows.kt`, `ui/WindowChips.kt`, `ui/VerdictLines.kt` | The product screen: enforcement switch (Enforcing / Observing + banner), Expecting-a-call button with countdown, precedence list in evaluation order with live windows, window editors (time + days), unknown-tone picker, hidden-caller policy, STIR action, repeat-caller toggle, silenced-notification policy, pattern rules + mask builder + neighbor-spoof preset, link to system blocked-numbers screen, **Test a number**, screening log + weekly tallies |
| Setup | `ui/SetupActivity.kt` | Role request → Restricted-Settings refusal walkthrough → verify role held → channel creation → contacts access → start in observe mode; each step shows actual state |
| Incoming call | `ui/IncomingCallActivity.kt`, `ui/CallGrid.kt`, `ui/DisconnectNotice.kt`, `ui/Notice.kt` | Huge caller name/number + context line (tier + why it rang, CNAP name); full-width Answer block, hairline Decline block, reply-with-text; two-button tap (default) or pill slider; `showWhenLocked`, own task affinity `…​.call` |
| In-call | `ui/InCallActivity.kt` | Tabular duration, Keypad · Mute · Speaker/route · More; Hold / Add call; `CallEndpoint` + legacy `CallAudioState` routing; call-waiting heads-up, hold-and-answer, swap banner; status-bar chip via CallStyle |
| Voicemail | `ui/VoicemailActivity.kt`, `vvm/*` (below) | Carrier visual-voicemail inbox + player; long-press-1 dial-in fallback |
| Tab bar | `ui/TabBar.kt` | Four tabs; tab-hiding is a setting |
| Logs/diagnostics | `log/LogsUi.kt`, `log/AppLog.kt`, `log/LogDumpProvider.kt` | In-app log view + `ContentProvider` log dump for the suite |

### 1.2 Ring-policy engine (the core feature)

Pure JVM, no `android.*` imports — `core/core/`:

- `RingPolicy.kt` — `decide(now, facts, rules)`: first-match-wins table
  (§6 rows 1–12) plus overrides (repeat-caller pierce, Expecting-a-call
  bypass, clock-backwards guards). Observe mode is a gate in callers, never
  a branch in `decide()`.
- `Rules.kt` — `Rules` (unknown/business windows, hidden-caller policy,
  STIR action, repeat toggle, `bypassUntil`, block/silence pattern lists),
  `PatternRule` (E.164 digit prefix + wildcard count, no regex,
  length-exact match), `HiddenCallerPolicy`, `StirAction`.
- `Window.kt` — daily window: start-inclusive/end-exclusive, `end <= start`
  wraps past midnight, days mask bit 0 = Monday, arrival day governs.
- `Verdict.kt` — `Block` / `Ring(DEFAULT|UNKNOWN)` / `Silence(Reason)` +
  `CallerFacts` (12 fields: number, saved, starred, bizTier,
  sendToVoicemail, userBlocked, stirFailed, repeatCaller, recentOutgoing,
  cnapName, emergencyWindow, withheld, groupBlocked) + stable wire tokens.
- `RingRepeat.kt` — ringtone plays once / twice / until-voicemail.
- `util/E164.kt` — libphonenumber E.164 normalization; garbage → null →
  classifies unknown (noisy, never lossy).

Callers of the engine: `telecom/XxCallScreeningService.kt` (pre-ring,
**block verdicts only**, 5 s deadline, never silences a contact),
`telecom/XxInCallService.kt` + `ring/RingRouter.kt` + `ring/CallRinger.kt`
+ `ring/RingTonePlayback.kt` (ring time, every call: verdict → channel),
`telecom/FactSource.kt` (builds `CallerFacts` from mirror + CallLog +
screen log), `telecom/EmergencyMarker.kt` + `EmergencyWindow.kt`
(24 h post-emergency bypass), `telecom/ProximityGuard.kt` +
`ProximityPolicy.kt` (screen-off at the ear), `telecom/CallManager.kt` +
`DetailsCodec.kt` + `LogRows.kt` (place/receive, log rows),
`telecom/XxInCallService.kt` also owns hold/swap, audio routing
(`CallEndpoint` + `CallAudioState` paths), call waiting.

### 1.3 Background behaviors

| Behavior | Kotlin | Notes |
|---|---|---|
| Pre-ring screening | `telecom/XxCallScreeningService.kt` | `CallScreeningService`; Disallow/allow within 5 s; platform fail-open on timeout |
| Per-tier ringing | `ring/ChannelRegistry.kt`, `ring/ChannelIds.kt`, `ring/RingRouter.kt`, `ring/CallRinger.kt` | One notification channel per outcome (`ring_default_v2`, `ring_unknown_v1`, `ring_silent_v1`, `ongoing_v1`); channel sound immutable → versioned IDs; default tier points at `DEFAULT_RINGTONE_URI` indirection |
| Silenced/missed notifications | `ring/SilencedNotifier.kt`, `ring/SilenceBatcher.kt`, `ring/MissedCallReceiver.kt`, `ring/MissedNotifContent.kt`, `ring/MissedCallsClear.kt`, `ring/NotifIds.kt`, `ring/CallPerson.kt` | Batched ("3 silenced calls"), inline actions Call back · Ring next time (★) · Block, three-state policy (immediately / daily summary / never); owns the `SHOW_MISSED_CALLS_NOTIFICATION` duty |
| Block actions | `ring/BlockActionsReceiver.kt` | Non-exported receiver: notification actions write the blocklist |
| Callback trampoline | `ring/CallbackTrampolineActivity.kt` | Notification "Call back" taps route through `placeCall()` |
| Expecting-a-call tile | `ring/XxTileService.kt`, `ring/TileCountdown.kt` | QS tile with minutes-remaining label; bounded 2 h (configurable 30 min / 2 h / 8 h) |
| Theme sync | `theme/ThemeSyncReceiver.kt`, `theme/ThemePreset.kt`, `theme/ThemeGroundStore.kt`, `theme/ThemeGroundApplier.kt`, `theme/ThemeNightMode.kt` | Receives `xx.launcher.THEME_CHANGED` (name + ARGB), resolves to preset, persists ground, repaints; Custom via raw ARGB, persists nothing without a background |
| Visual voicemail | `vvm/` (24 files: `XxVisualVoicemailService.kt`, `VvmImapClient.kt`, `VvmImapSyncWorker.kt`, `VvmSmsParser.kt`, `VvmCarrierConfig.kt`, `VvmCredentialStore.kt`, `VvmActivationController.kt`, `VvmGate.kt`, `VvmAudioPlayer/Fetcher/Downloader/FetchPolicy.kt`, `VvmListQuery/State.kt`, `VvmNotifPolicy.kt`, `VvmVoicemailNotifier.kt`, `VvmNetworkRevoked.kt`, `VvmImapHostPolicy/ImapPolicy/ImapTransport.kt`, `VvmMailboxUploader.kt`, `VvmUploadPolicy.kt`, `VvmFetchHandler.kt`, `VvmDetailPlayer.kt`, `VvmRuntimePermissions.kt`) + `docs/vvm-carrier-config.txt` | Opt-in IMAP to carrier mailbox, host from STATUS SMS only, no greeting/PIN/transcription cloud; live-SIM STATUS transcript still open (V0) |
| Contact mirror refresh | `data/ContactMirror.kt` (+ observer, foreground sweep) | Warm mirror of starred/saved/tone/`LOOKUP_KEY`; serves the 5 s screener budget and the People tab |
| Intents/routing | `ring/Intents.kt`, `XxApplication.kt`, `ServiceLocator.kt`, `AppContextHolder.kt` | App wiring; `ACTION_DIAL` (bare + `tel:`) / `ACTION_VIEW tel:` for role eligibility |

### 1.4 Data stores (Room, design §11)

`data/XxDatabase.kt`, `data/XxDb.kt`, entities + DAOs:
`tier_member` (LOOKUP_KEY → biz tier), `pattern_rule` (prefix/wildcards/
action/preset), `contact_mirror` (lookupKey, e164, displayName, starred,
customRingtone, sendToVoicemail), `screen_log` (at, e164, verdict, reason,
tier, stir, cnap, mode, answered; capped 1000 rows), `channel_registry`
(purpose → channelId/tone/version), `setting` (all keys in
`SettingsRepository.kt`: enforcement_mode, observe_week_end, bypass_until,
bypass_duration, unknown/business windows, hidden_caller_policy,
stir_action, repeat_caller, silence_notif_policy, answer_interaction,
group_recents, hidden_tabs, visual_voicemail(+activated), ring_repeat),
`emergency_marker` (single row). System blocklist is **not** mirrored —
`BlockedNumberContract` is the source of truth.
`data/BlockedGroupSync.kt`, `data/StealthBlock.kt` — Blocked-group sync.
`data/TierExport.kt` + `TierExportProvider.kt` — Business tier + windows
for TxxT (signature permission).

### 1.5 Permissions and integrations (manifest)

`app/src/main/AndroidManifest.xml`: `READ/WRITE_CONTACTS`,
`READ/WRITE_CALL_LOG`, `READ_PHONE_STATE`, `CALL_PHONE`,
`POST_NOTIFICATIONS`, `USE_FULL_SCREEN_INTENT`, `FOREGROUND_SERVICE` (+
`_PHONE_CALL`), `VIBRATE`, `WAKE_LOCK`; VVM-gated `INTERNET` +
`ACCESS_NETWORK_STATE` (socket sites exist only in `vvm/`, guarded by
`VvmGate`/`VvmImapPolicy`), `ADD/READ/WRITE_VOICEMAIL`, `SEND_SMS`;
`THEME_SYNC` uses-permission; `TIER_SYNC` signature permission.
Providers: suite backup (`backup/DialerBackupProvider.kt` over
`suite/backup/SuiteBackupProvider.kt` + `TarStream.kt` + `Snapshot.kt`),
`TierExportProvider`, `LogDumpProvider`. Backup JSON:
`backup/CallLogJson.kt` (`export/calllog.json`, schema 1),
`backup/RingPolicyJson.kt` (`export/ring_policy.json`: mode, windows,
hidden/STIR/patterns, tiers — never stars), `data/BackupJson.kt`,
`backup/CallLogRestore.kt`. Utilities: `util/BlocklistImport.kt`
(one-number-per-line import), `util/StarContact.kt`.
Probe APK (`probe/`, `PROBE.md`, `RingPoster.kt`, `ProbeLog.kt`,
`ProbeInCallService.kt`, `ProbeCallScreeningService.kt`,
`MainActivity.kt`) is test-only scaffolding — not ported.

## 2. Linux implementation per feature

General substitutions used everywhere below: Room → SQLite (stdlib
`sqlite3`, one file, WAL mode); DataStore/`setting` table → JSON config
file + SQLite for the log tables; `ContactsContract` → EDS address book
(see 2.2); `CallLog` provider → own SQLite call/screen log (see 2.6);
notification channels → `org.freedesktop.Notifications` via the shell's
`notif_daemon.py` plus in-app sound via the feedbackd/GStreamer path the
shell's `call_ui.py` `Ringer` already uses; `CallStyle` FSI → shell
surfaces (see §3); `RoleManager` roles → D-Bus name ownership (see §3).

### 2.1 Ring-policy engine — port fully, pure Python

`core/RingPolicy.kt`, `Rules.kt`, `Window.kt`, `Verdict.kt`,
`RingRepeat.kt`, `util/E164.kt` → one pure module (no `gi`, no D-Bus
imports) exposing `decide(now, facts, rules)`, `Window.contains`,
`PatternRule.matches`, E.164 normalization, and the observe-mode gate.
Behavior stays identical, including: precedence order;
blocked-beats-starred with the loud `⚠ starred number blocked` flag;
starred-beats-Business; contacts exemption on patterns (non-optional);
repeat pierces every Silence but never a Block or a voicemail divert;
bypass bounded (clock-backwards cancels); emergency window bypasses
everything; fail-open (any exception in fact gathering → Ring). The JVM
test suite (`core/src/test/` — `RingPolicyTest`, `RingRepeatTest`,
`PatternRuleTest`, `WindowTest`, `E164Test`) is re-expressed as pytest
cases against the Python engine. E.164 uses an offline digit-normalization
routine (`+`/leading-`00`/national trunk handling with the
ModemManager SIM country as the default region); libphonenumber has no
distro-packaged Python port worth depending on, and exact-validity checks
are out of scope — validity is not a verdict input, only identity ("one
caller, one identity") is.

The engine is also the artifact the **shell consults over D-Bus before
ringing** — see §3.3 for that API. The scaffold in this lane already
carries the engine skeleton; later lanes flesh out facts providers around
it without changing `decide()` semantics.

### 2.2 Contacts (People tab, tiers, stars) — EDS

`ui/PeopleActivity.kt`, `data/ContactMirror*`, `util/StarContact.kt`,
`data/BlockedGroupSync.kt`, `data/TierMember*` → Evolution Data Server
address book (`EBook`/`EDataServer` over GI): saved-ness, starred
(equivalent flag on the EDS contact), per-contact ringtone note, and
display names come from EDS; the Business tier stays app-local in SQLite
keyed by EDS UID (the `LOOKUP_KEY` analog). Contact Scopes has no Linux
analog — EDS is either reachable (all contacts) or not (empty address
book); both states reuse the Android empty-mirror degradation (classify
unknown → noisy, never lossy; People tab states why it is empty).
Writes: star toggling writes back to EDS when writable, else degrades to
an app-local star overlay that the engine treats identically (the tier UI
is the only label surface on account-free setups, same rationale as D4).
Monogram avatars stay (no photo dependency). T9 search (`ui/T9.kt`) is pure
logic and ports unchanged. `BlockedGroupSync`/`StealthBlock` have no EDS
equivalent — **not portable**; the closest equivalent is an app-local
"Blocked" tier in SQLite enforced by the engine's `userBlocked` path
(plus pattern rules), documented in the UI as app-local rather than
system-wide.

### 2.3 Call placement / answering / in-call — ModemManager Voice

`telecom/CallManager.kt`, `XxInCallService.kt` (placement half),
`ui/KeypadActivity.kt`, `ui/InCallActivity.kt`, `ui/IncomingCallActivity.kt`
(in-app surfaces), `telecom/ProximityGuard.kt`, `ui/SupervisoryTone.kt`,
`DetailsCodec.kt` → `org.freedesktop.ModemManager1`
`Modem.Voice.CreateCall` + `Call.Start/Accept/Hangup/Hold/Unhold` and
`Call.SendDtmf` for keypad tones, `Modem.Voice`
`CallAdded/CallDeleted` + `Call.StateChanged` signals via the same async
`Gio` D-Bus pattern `modem_monitor.py` uses (tuple-unpack replies, no
`user_data` on callbacks, worker thread + `GLib.idle_add` back to the
GTK loop). Placement mirrors the shell's `dialer.py place_call()` but
against `Voice.CreateCall` rather than `mmcli` scraping. Audio routing
reuses the stack `call_ui.py` already proved: callaudiod when it owns its
bus name, else the `pactl` fallback (same worker-thread discipline).
Proximity screen-off: no `PROXIMITY_SCREEN_OFF_WAKE_LOCK` on Linux —
closest equivalent is the existing shell behavior (display blanking is
suppressed while a call is non-terminated; `docs/config.md`
`auto_lock_timeout`: "Never locks while a call is non-terminated"). Hold/
swap and call-waiting map to MM1 `Hold`/`Unhold`; conference merge was
already deferred on Android and stays deferred — MM1 exposes no merge
primitive on most modems either.

### 2.4 Ringing / ringer ownership — notifications + shell split

`ring/ChannelRegistry.kt`, `RingRouter.kt`, `CallRinger.kt`,
`RingTonePlayback.kt`, `RingRepeat.kt`, `ChannelIds.kt` → no notification
channels exist on Linux, so the mapping is: verdict →
(`org.freedesktop.Notifications` Notify with per-tier sound hint + loop
flag `ring_repeat`) + lock-screen / incoming surface per the §3 split.
Per-tier distinct tones (the whole point of D2) become per-tier sound
files played through the same path the shell's `Ringer` uses (feedbackd
event when present, sound-file fallback); `sound_ringtone` config
semantics are inherited from the shell. Channel sound immutability (hence
`ChannelRegistry` versioning) has no analog — tone changes are just
file/config swaps, so the registry collapses to two config keys
(default-tone path, unknown-tone path). `RingRepeat`
(once/twice/until-voicemail) ports as loop-count policy on the sound
playback. Silence means: no sound, notification still raised (answerable
while ringing), reason string attached, obeying the three-state
silenced-notification policy. DND layering carries over unchanged: the app
never implements quiet-hours itself; the shell's DnD (`dnd.py`) filters
the pipeline and the engine logs its own computed verdict regardless.

### 2.5 Screening / blocking / patterns / STIR — engine + local lists

`telecom/XxCallScreeningService.kt` has no Linux analog (no screening-role
pipeline; MM1 delivers no pre-ring hook and no 5 s deadline). Its function
splits: **block verdicts** are enforced by immediately hanging up
(`Call.Hangup`) on `CallAdded` in RINGING_IN state before the shell rings
(see §3.3 ordering); silence verdicts become silent notifications.
`BlockedNumberContract` (D6) does not exist — replaced by the app-local
block table in SQLite (exact numbers) + `pattern_rule` table (prefix
masks, contacts exemption intact) + one-number-per-line import
(`util/BlocklistImport.kt` ports as a file picker over `Gtk.FileDialog`).
STIR/SHAKEN (`getCallerNumberVerificationStatus`) is **not portable**:
MM1 exposes no caller-verification status; the `stirFailed` fact is then
always false and the STIR setting hides itself (spec the hiding; do not
ship a dead toggle). The `stir` column stays in the schema for a future
modem/QMI source. Neighbor-spoof silence preset ports unchanged (own
six-digit prefix rule, silence only, off by default).

### 2.6 Call log / Recents / screen log — own SQLite

`ui/RecentsActivity.kt`, `RecentsMerge.kt`, `RecentsFilterIntent.kt`,
`telecom/LogRows.kt`, `data/ScreenLog*`, `ring/MissedCallReceiver.kt` →
`~/.local/share/xx-dialer/calls.db` (SQLite): a `call_log` table (number,
direction, date, duration, cached name — the `CallLogEntrySnapshot` shape)
plus the `screen_log` table (verdict, reason token, tier, mode, answered).
No system call-log service exists on Linux; GNOME Calls keeps its own
SQLite — the import path reads it when present (see §4.2). Recents UI
(chips, verdict lines `ui/VerdictLines.kt`, grouping setting, `Ring next
time`/`Add to Business` row actions) ports to GTK list rows. Missed-call
duty: no Telecom handover — the app raises its own missed-call
notification with the reason string when a ringing call goes unanswered,
and clears it on callback (`MissedCallsClear.kt` analog).

### 2.7 Rules screen, windows, bypass, observe mode — direct port

`ui/RulesActivity.kt` + `PrecedenceRows.kt`, `WindowChips.kt`,
`RulesFocus.kt`, `data/Setting*`/`SettingsRepository.kt` →
Adw preferences page(s): enforcement switch with observe banner,
Expecting-a-call button with countdown (`bypass_until` in config;
countdown via `GLib.timeout_add`, no alarms needed), precedence list in
evaluation order, window editors (time + days mask), hidden-caller policy,
repeat-caller toggle, silenced-notification policy, pattern mask builder
(`MaskBuilder.kt` logic ports pure), neighbor preset, blocklist import,
Test-a-number (same `decide()`, "right now"), screening log + tallies.
`XxTileService.kt` (QS tile) is **not portable** — no per-app QS-tile API
on Phosh/XX-WM; closest equivalent is a shell quick-settings tile shipped
in a later lane via the shell's tile extension point, plus the in-app
button (which is the primary surface anyway).

### 2.8 Setup — Linux first-run

`ui/SetupActivity.kt` → first-run page: modem present? (MM1 reachable +
Voice interface), EDS reachable?, notification server reachable?,
notification sounds working (test-ring button), then **start in observe
mode for a week** with enforcement offered, never flipped. The Android
Restricted-Settings gate has no analog; the Linux analogs it guards
against are "looks configured but silent": muted notification sounds,
missing modem, EDS unreachable — each step shows actual probed state.

### 2.9 Voicemail — dial-in now, VVM later

`ui/VoicemailActivity.kt`, all of `vvm/`: dial-in voicemail (long-press 1)
ports in the keypad lane — it is just placing a call. Carrier visual
voicemail (IMAP client, STATUS-SMS parsing, activation controller, upload
policy, credential store) is **deferred**: it needs mobile-data IMAP +
SMS-filter plumbing (`Modem.Messaging`, `VvmSmsParser`) that is a whole
lane by itself, and upstream the Android client itself still awaits a
live-SIM STATUS transcript (todo.md V0). `VvmCredentialStore` →
libsecret (`Secret` over GI) when the lane is built. The Rules-screen
voicemail row (`RulesScreenVoicemail.kt`) hides until the lane lands.
Nothing about the VVM deferral affects the ringer.

### 2.10 Theme sync, backup, misc — direct ports

- Theme (`theme/*`): `xx.launcher.THEME_CHANGED` does not cross into Linux
  the same way — instead the app reads `~/.config/xx-wm/config.json`
  directly (same keys as `docs/config.md`: `theme`, `custom_background`,
  `prefer_dark`, `font`, `custom_font_family`, `text_size_scale`) and
  hot-reloads it (file monitor, debounced; invalid file keeps last good
  config — the shell's own rule). Eight presets + custom + the same font
  families as `config.py` `THEME_PRESETS`/`FONT_FAMILIES` (this scaffold
  already vendors them). Monochrome-per-theme, no hardcoded colors outside
  the semantic constants.
- Backup (`backup/*`, `data/BackupJson.kt`): versioned JSON export
  (tiers, patterns, settings, windows; never stars, never the log/mirror)
  + `export/calllog.json` (`CallLogJson`, schema 1) — same shapes, so an
  Android export restores on Linux (field mapping in §4.2). Suite
  tar-provider plumbing has no Linux analog; export is a plain file the
  user moves via USB/xx-drive.
- `ring/CallbackTrampolineActivity.kt` → notification "Call back" action
  places via `Voice.CreateCall` directly (no trampoline activity needed;
  actions dispatch in-process).
- `log/*` → `~/.local/share/xx-dialer/logs/` rotated text logs.
- `probe/` → not ported (was throwaway APK scaffolding).
- Call recording: already permanently out on Android (GrapheneOS
  allowlists only the stock dialer); on Linux PipeWire tap recording is
  technically possible but stays out — same product decision, not a
  platform gap. SMS/MMS stays out (non-goal on Android; Chatty owns it on
  Linux). Cloud caller-ID / spam DBs stay out (R8: the Linux app makes no
  network connections at all outside the deferred VVM lane; stated in the
  metainfo).

## 3. Integration with XX-WM

### 3.1 What the app feeds or replaces

- **Shell built-in dialer fallback** (`dialer.py`, seeded via
  `default_layout.py` Comms folder): once `io.piercingxx.XXDialer` is
  installed, the shell's seeding prefers it as the Phone binding (same
  rule that prefers GNOME Calls/Chatty today: real telephony app wins,
  built-in dialer is the fallback). The shell's dialer stays as the
  no-app fallback; nothing is deleted.
- **Caller ID lookup**: `contacts.py lookup_number()` stays the shell's
  lookup for its own surfaces, but the dialer feeds it — the dialer owns
  the richer store (EDS + Business tier + recent-outgoing memory). Later
  lane: the dialer exposes `LookupNumber` on its D-Bus name (see §3.3)
  and the shell prefers it when owned, falling back to its local
  `contacts.json`/VCF path otherwise.
- **SMS ownership**: untouched — Chatty owns SMS; this app never takes an
  SMS role (same non-goal as Android). The deferred VVM lane only
  *reads* carrier STATUS SMSes via `Modem.Messaging`, never the user SMS
  store.
- **Notifications**: the app is a client of `org.freedesktop.Notifications`
  (served by the shell's `notif_daemon.py` under XX-WM, or the Phosh
  notification server under Phosh). Silenced-call batching and the
  three-state policy live in the app; "Disable for…" mutes (`muted_apps`)
  are honored by the server side, not fought.
- **Comms/Calendar home slots** (`.desktop` id `io.piercingxx.XXDialer`):
  the app ships exactly this app id (desktop file, D-Bus name, metainfo
  all agree), so seeding, search, rename labels, and folder membership
  resolve it like any seeded app. Calendar slot is unaffected (GNOME
  Calendar/EDS `ECal` lane is separate work).

### 3.2 The split: shell keeps lock-screen/incoming, dialer owns the rest

Decision (this spec fixes it; either option was permitted):

- **The shell keeps the lock-screen + incoming-call surface**
  (`call_ui.py`): it is the surface that must work when the dialer is
  closed, crashed, or updating, and it already handles modem-wedged
  states, the Close fallback, and DND/starred exceptions (`dnd.py`,
  `dnd_starred_numbers`, repeat-callers-always-ring). The dialer owns
  **history, keypad, contacts/People, Rules, voicemail UI, and the
  policy engine itself**.
- **Ownership rule (the deferral mechanism): the shell defers when the
  dialer owns its bus name** `io.piercingxx.XXDialer` on the session bus
  (same `bus_name_has_owner()` pattern `modem_monitor.py` already uses
  for `org.gnome.Calls`/`sm.puri.Chatty`). Concretely:
  - Incoming call arrives → the shell's incoming surface rings/silences
    per the verdict from `CheckNumber` (§3.3) rather than its own
    `dnd_starred_numbers` list (the dialer's starred set is a superset
    fed from EDS; the shell's list remains the fallback when the dialer
    is absent).
  - **Block verdicts**: the dialer hangs up (`Call.Hangup`) immediately
    on `CallAdded`/RINGING_IN. The shell must therefore consult before
    its first ring and tolerate the call vanishing (it already handles
    modem-side disappearance — the "modem stopped answering" path).
  - Dialer absent/closed → shell behaves exactly as today (own ringer,
    own starred list). Installing the dialer never makes the phone
    *less* able to ring: fail-open is preserved end to end.
- Outgoing placement has no conflict: both the shell dialer and this app
  place via `Voice.CreateCall`; two placers are fine because placement
  is user-initiated. Answering from the lock screen calls
  `Call.Accept` on the MM1 path directly (shell already does this via
  `accept_call()`); answering from the app's Recents/incoming window
  does the same. No exclusive-answering lock is needed.

### 3.3 D-Bus API: the policy engine the shell consults

Bus name: `io.piercingxx.XXDialer` (session bus; also the app id, so
D-Bus activation works). Object path: `/io/piercingxx/XXDialer`.
Interface: `io.piercingxx.XXDialer.Policy1`.

Methods (all answered from warm SQLite/EDS-mirror data — never a cold
EDS round trip on this path, same 5 s-budget discipline as the Android
screener):

- `CheckNumber (in s e164, out s verdict, out s reason, out s tone)` —
  verdict ∈ `ring|silence|block`, reason is the stable `Reason` token
  (`Verdict.token()` vocabulary: `Block|Ring|Silence` + reason label),
  tone ∈ `default|unknown|none`. Pure function of the number + "now";
  used by the shell before its first ring and by any future family app.
- `LookupNumber (in s e164, out s display_name, out s tier)` — caller-ID
  feed for shell surfaces; tier ∈ `starred|business|saved|unknown`.
- `NoteCall (in s e164, in s direction, in b answered, in s verdict,
  in s reason)` — shell reports call outcomes so repeat-caller (15 min)
  and recent-outgoing (48 h) queries stay correct no matter which
  surface placed/answered the call. Fire-and-forget.
- `GetStats (out u silenced_week, out u blocked_week)` — tallies for the
  shell or widgets; cheap query over `screen_log`.

Signals:

- `VerdictDecided (s e164, s verdict, s reason)` — emitted for every
  screened incoming call so the shell can update its surface without
  polling.
- `BypassChanged (b active, t expires_unix)` — Expecting-a-call state so
  a future shell QS tile can reflect it.

Ownership semantics: the shell probes with `bus_name_has_owner()` (500 ms
timeout, exactly as `modem_monitor.py` does for Calls/Chatty) and caches
the verdict path per incoming call — one probe per call, not per ring.
If the dialer does not own the name, the shell uses its own DnD/starred
logic and logs nothing to the dialer. The dialer's `NoteCall` from the
shell is what keeps cross-surface repeat detection honest; if the shell
never calls it (older shell), the dialer's own observations still cover
calls it saw — degraded, never wrong-direction (fail-open: missing data
can only make verdicts *ring more*).

Autostart: the policy service is D-Bus activatable
(`io.piercingxx.XXDialer.service` in the session bus services dir), so
the shell's `CheckNumber` wakes it on demand — no daemon must be
pre-running for screening to work. The full UI launches separately via
the `.desktop` file. First-run (no config yet) answers `ring` to
everything and starts the observe week — identical to Android R11.

## 4. Data model + storage paths + import

### 4.1 XDG paths (all under the user tree; nothing system-wide)

| Content | Path | Format |
|---|---|---|
| Config (mode, windows, policies, tones, UI prefs) | `~/.config/xx-dialer/config.json` | JSON, tolerant parse (missing keys → defaults, unknown keys ignored, corrupt file → defaults + backup of the bad file; the shell's config rule) |
| Call + screen log | `~/.local/share/xx-dialer/calls.db` | SQLite WAL: `call_log`, `screen_log`, `pattern_rule`, `tier_member`, `blocked_number`, `contact_mirror`, `emergency_marker` |
| Export / import | `~/Documents` (user-chosen via `Gtk.FileDialog`) | `ring_policy.json` + `calllog.json` (Android-identical shapes), `contacts.vcf` handled by EDS/GNOME Contacts, not this app |
| Logs | `~/.local/share/xx-dialer/logs/` | Rotated text |
| Desktop entry | `/usr/share/applications/io.piercingxx.XXDialer.desktop` | Installed by meson |
| Metainfo | `/usr/share/metainfo/io.piercingxx.XXDialer.metainfo.xml` | Installed by meson |
| D-Bus activation | `/usr/share/dbus-1/services/io.piercingxx.XXDialer.service` | Installed by meson |

SQLite schema mirrors design §11 one-to-one (same table/column names
where SQLite allows), plus `blocked_number (e164 TEXT PK)` for the
app-local blocklist (the `BlockedNumberContract` replacement) and a
`star_overlay (uid TEXT PK)` for stars when EDS is read-only. `setting`
keys keep the Android names (`enforcement_mode`, `bypass_until`, …) so
`RingPolicyJson` restores field-for-field. `channel_registry` is dropped
(no channels on Linux; two tone-path keys in config instead).

### 4.2 Import paths

1. **From the Android app (primary)**: the Android backup already
   exports `export/ring_policy.json` (`RingPolicySnapshot`: mode,
   windows, hidden/STIR/patterns, tiers) and `export/calllog.json`
   (`CallLogEntrySnapshot` list, schema 1). The Linux importer reads both
   shapes verbatim: windows → config, patterns → `pattern_rule`, tiers
   (lookupKeys unresolvable on Linux) → matched against EDS contacts by
   number, unmatched tier rows reported and kept as number-keyed
   memberships; call-log entries → `call_log` (type/date/duration/
   cachedName map directly). Stars are intentionally absent from the
   export (owned by xx-contacts' `contacts.vcf`) — import those via EDS.
   Garbage/future schema → empty + warning, never a crash (the Android
   codec rule).
2. **vCard**: any `contacts.vcf` goes to EDS/GNOME Contacts, not this app.
3. **GNOME Calls history**: when present, `~/.local/share/calls/calls.db`
   (GNOME Calls' own SQLite) is read once at first run into `call_log`
   (number, direction, timestamp, duration); failures ignored silently —
   history is a nice-to-have, and the recent-outgoing/repeat queries work
   from the day of install regardless.
4. **Blocklist file**: one number per line (the Android offline import),
   normalized through the same E.164 routine, duplicates skipped.
5. **Standard SMS backup XML** (e.g. SMS Backup & Restore): out of scope —
   this app never owns SMS.

## 5. Architecture

### 5.1 Packages/modules

```
src/xx_dialer/
├── main.py       Adw.Application (id io.piercingxx.XXDialer), NavigationView,
│                 pages: Recents / Keypad / People / Rules / Setup (later lanes)
├── policy.py     PURE: Window, PatternRule, CallerFacts, Rules, Verdict,
│                 decide(), normalize_number(), observe gate  (this scaffold)
├── theme.py      PURE: THEME_PRESETS/FONT_FAMILIES (vendored from shell
│                 config.py values), load_xx_wm_config(), theme_css()
│                 (this scaffold)
├── config.py     PURE: JSON config load/save/defaults/validation (this scaffold)
├── store.py      SQLite schema + DAO helpers (lane 1)
├── contacts.py   EDS access + warm mirror + tier membership (lane 2)
├── modem.py      MM1 Voice/Call async D-Bus client, place/answer/hangup/hold,
│                 CallAdded/StateChanged subscriptions (lane 3)
├── facts.py      CallerFacts builder: mirror + call_log + screen_log queries (lane 3)
├── service.py    session-bus Policy1 endpoint (CheckNumber/LookupNumber/
│                 NoteCall/GetStats + signals), D-Bus activatable (lane 3)
├── ringer.py     verdict → notification + per-tier tone playback, silence
│                 batching, three-state policy (lane 4)
└── ui_*.py       one module per tab/surface (lanes 4–6)
```

Dependency rule (the Nope-Mode discipline, kept): `policy.py` imports
nothing but stdlib; `theme.py`/`config.py` import stdlib only; everything
GI/D-Bus lives in `modem.py`/`service.py`/`ringer.py`/`contacts.py`/`ui_*`
and is injectable/fakeable for headless tests.

### 5.2 Threading — never block the GTK main loop

Same rule as the shell (XXWM_RULES.md #9): no `subprocess.run`,
no `time.sleep`, no `call_sync` with long timeouts on the main thread.
MM1 and EDS calls use async `Gio` D-Bus calls (tuple-unpack replies per
trap #1, no trailing `user_data` per trap #2, 7-arg signal handlers per
trap #3) or a worker thread posting back with `GLib.idle_add`. The
`CheckNumber` path answers only from warm SQLite data so it can serve on
the main thread in microseconds; any cold fact source degrades the
verdict toward Ring rather than stalling the reply (fail-open, R9).

### 5.3 Names, activation, autostart

- App id / session-bus name: `io.piercingxx.XXDialer`. Object path
  `/io/piercingxx/XXDialer`, interface `io.piercingxx.XXDialer.Policy1`.
- `.desktop` file carries the app id, `DBusActivatable=true`, and the
  `tel:` URI scheme (`x-scheme-handler/tel`) so other apps can hand
  numbers to the dialer (the Linux analog of the `ACTION_DIAL` role
  activities).
- `io.piercingxx.XXDialer.service` (D-Bus activation) execs the
  `--service` entry point (policy endpoint only, no windows).
- No autostart `.desktop` in v1: activation-on-demand covers screening,
  and starting the full UI unasked would violate the minimalism
  directive. The deferred VVM lane may add an opt-in background sync
  entry, off by default.

## 6. Milestone plan (build lanes)

Each lane is a self-contained unit of ≤ ~1500 lines of change with its
acceptance tests. Order matters: data → engine consumers → UI → services
→ polish (same reasoning as design §17: the logic that must be correct
has no toolkit dependencies — build it against tests first).

| Lane | Scope | Acceptance |
|---|---|---|
| 0 (this lane) | **Spec + runnable scaffold**: `PORT_SPEC.md`, meson install, `Adw.Application` placeholder applying the XX-WM theme, pure `policy`/`theme`/`config` modules, headless pytest smoke tests, ruff gate | `pytest -q tests` + `ruff check src tests` pass; `PYTHONPATH=src python3 -m xx_dialer` starts (with display) |
| 1 | **Data layer**: `store.py` (§11 schema + blocklist/star-overlay tables), config↔DB wiring, Android `ring_policy.json` + `calllog.json` + GNOME Calls importers | Round-trip tests; Android export fixtures restore exactly; corrupt inputs degrade per spec |
| 2 | **Contacts**: EDS access + warm mirror, tier membership, star write-back/overlay, T9/mask pure logic, `LookupNumber` dataset | Headless tests with fake EDS backend; EDS-absent degradation covered |
| 3 | **Telephony + policy service**: `modem.py` (place/answer/hangup/hold, CallAdded/StateChanged), `facts.py`, `service.py` Policy1 endpoint, block-hangup path, `NoteCall` ingestion, D-Bus activation file | Fake-MM1 tests (Gio-pattern traps respected); on-device: place/answer/hangup a real call; shell `CheckNumber` consult returns the verdict the engine computes |
| 4 | **Recents + Keypad + notifications**: call/screen log UI with chips + verdict lines + grouping, keypad with T9 + long-press-1, silenced batching + three-state policy + row actions, missed-call flow | Headless UI-logic tests; on-device: screened call shows its reason; silenced burst batches |
| 5 | **Rules + People + Setup**: Rules screen (switch, bypass countdown, precedence, editors, patterns, Test-a-number, tallies), People sections + sheets, first-run Setup with real probed state | Full §6 truth-table pytest (ported from `RingPolicyTest` incl. boundaries, DST, 14:59/15:01, 47:59/48:01); on-device observe week → offered enforcement |
| 6 | **Polish + VVM decision**: lock-screen handoff verification with the shell, QS-tile equivalent, backup export UI, logs view, metainfo screenshots; VVM lane scoped separately (STATUS-SMS capture → IMAP fetch → Secret credential store → inbox UI) | Dogfood gate: replace GNOME Calls for a week in observe mode, then enforcing; zero missed-legitimate-call reports |

Out of scope for all lanes (carried over from Android non-goals):
SMS/MMS, call recording, cloud caller-ID/spam DBs, conference merge,
per-app QS tiles, `BlockedGroupSync` semantics (app-local block tier
instead), STIR UI (hidden until a modem source exists).

No pip dependencies are required by any lane: stdlib (`sqlite3`, `json`,
`re`, `pathlib`) + GI (`Gtk`, `Adw`, `Gio`, `GLib`, `EBook`/`EDataServer`,
`Secret` for the later VVM lane, `Gst` for tone playback if feedbackd is
absent) + ModemManager over D-Bus. If a later lane wants anything else,
it must justify a package present on every target distro.

## Review notes (orchestrator, 2026-10-15) — these override the text above

1. SMS is owned by the TxxT port (`io.piercingxx.TxxT`), not Chatty, on XX-WM devices. The dialer still never touches SMS; where this spec says "Chatty owns SMS", read "TxxT (or Chatty when TxxT is absent)".
2. The shell already ships a feedbackd-backed `Ringer` (launcher/src/call_ui.py). Note: feedbackd's TriggerFeedback timeout must be **0** (loop until EndFeedback) for ringing; `-1` plays the event's default length once.
3. App standby (shell feature in progress) never freezes apps owning `io.piercingxx.XXDialer` or `org.gnome.Calls`; the dialer may also rely on D-Bus activation for CheckNumber.
4. Contact tags (star/business/blocked) have ONE source of truth suite-wide: `~/.local/share/piercingxx/contact-tags.json`, owned/specified by the xx-contacts port (see its review note 2). This app's `tier_member` / `blocked_number` / `star_overlay` tables are a warm cache rebuilt from that file (watch it with Gio.FileMonitor); edits made in the dialer write through to the file.
5. **Supersedes note 4 (owner directive 2026-10-15):** xx-contacts now OWNS the contacts in its own database and exposes `io.piercingxx.Contacts1` on the session bus (LookupNumber / SetTags / List(since_revision) / Changed — see xx-contacts PORT_SPEC "Revision 2"). There is no contact-tags sidecar file. The dialer's star/business/blocked come from that API (cache kept warm via `Changed` + `List(since_revision)`); dialer edits go through `SetTags`. When xx-contacts isn't installed, fall back to the dialer's local tables.
6. **Lane 3 decision (2026-10-16, supersedes the §2.5/§3.2 block wording):** when the engine says BLOCK for an incoming call the shell asked about, the SHELL does the hangup (`Call.Hangup` on the vanishing path it already owns); the dialer never hangs up incoming calls itself — its `modem.py` hangup stays user-initiated only. Pinned contract: `CheckNumber (s number) -> (ss verdict, reason)` with verdict `ring|silence|block`; `NoteCall (s number, s direction, x started_ms, x duration_ms, s disposition)` ingests shell outcomes into the call log. The shell consults once per incoming call (500 ms owner probe, async call, 2000 ms timeout, fail-open to ring); the handler answers from warm SQLite/EDS-mirror data only and never calls the contacts service synchronously.
7. **Bus-name split (2026-10-16, supersedes the §3.3/§4.1/§5.3 bus-name lines):** the policy service owns **`io.piercingxx.XXDialer.Policy`** at **`/io/piercingxx/XXDialer/Policy`** (interface unchanged: `io.piercingxx.XXDialer.Policy1`; activation file `io.piercingxx.XXDialer.Policy.service`). The GTK app id stays `io.piercingxx.XXDialer` (desktop file, metainfo, `Adw.Application`). Background: the service on the app id made the GUI register as a secondary instance and exit with "Failed to register: No such interface `org.gtk.Actions`". Wherever §3.3/§5.3 say the bus name is the app id, read the `.Policy` name; the §4.1 activation path is `/usr/share/dbus-1/services/io.piercingxx.XXDialer.Policy.service`.
8. **One owner for silenced-call notifications (2026-10-16):** the silenced-record batching (`SilenceBatcher` burst collapse, the three-state `silenced_notif_policy`, the daily digest, the Call back / Ring next time / Block actions) lives in the **policy service process** (`src/xx_dialer/service.py` + `src/xx_dialer/ringer.py`), triggered by `NoteCall` for an unanswered incoming whose screen row says Silence. The GUI never posts these cards — it only shows the Recents rows and the unread badge. Shell rule (`docs/shell-consult.md`): when the consult reached the service, the shell posts no silenced notification of its own; it posts its own "Silenced: \<reason\>" card only on fail-open (service unreachable) paths.
