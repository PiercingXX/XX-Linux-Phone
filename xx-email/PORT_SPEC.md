# XX Email — Linux Port Spec (`io.piercingxx.XXEmail`)

Target: postmarketOS / Mobian / FuriOS / PureOS phones running phoc
(XX-WM or Phosh). 360x720 logical px (min 320x640), touch-first, on-screen
keyboard. Stack: Python 3.12+, GTK4 + libadwaita via PyGObject
(`Adw.Application`, `Adw.NavigationView`, responsive), meson install,
stdlib + GI libraries only.

Source app: `ref/xx-email/` — XX Email v0.1.1, an original, telemetry-free
Android Gmail client (Kotlin 2.0, Jetpack Compose, `dev.xxemail`). It speaks
the official Gmail REST API directly from the device (Retrofit/OkHttp),
authenticates with AppAuth + PKCE (bring-your-own OAuth client ID, scopes
`openid email profile` + `gmail.modify` only), caches mail in Room, and
drives undo-send / scheduled send / snooze through WorkManager. No Firebase,
no Play Services, no analytics SDKs (`README.md`, `PRIVACY.md`).

Shell conventions: `ref/xx-wm/` — `design.md` (UI contract), `docs/config.md`
(public config API), `launcher/src/config.py` (`THEME_PRESETS`,
`FONT_FAMILIES`), `launcher/src/notif_daemon.py` (owns
`org.freedesktop.Notifications`, backs off when another daemon owns it),
`launcher/src/modem_monitor.py` (ModemManager Voice/Messaging over D-Bus),
`launcher/src/contacts.py` (VCF + local JSON contact book).

This lane delivers the spec + a runnable scaffold only. Later lanes build
features in the milestone order of §7.

## 1. Feature inventory of the Android app

Every item cites the Kotlin file(s) that implement it. Screen names match
`ui/nav/Routes.kt`: `SETUP`, `MAILBOX`, `THREAD`, `SEARCH`, `SETTINGS`,
`COMPOSE`.

### 1.1 Setup / first run

- `ui/setup/SetupScreen.kt` — first-run page: paste-your-own OAuth client ID
  (validated to end in `apps.googleusercontent.com`), then the Google consent
  screen in any browser via AppAuth. On success navigates to the new mailbox.
- `data/auth/OAuthConfig.kt` — endpoints
  (`accounts.google.com/o/oauth2/v2/auth`,
  `oauth2.googleapis.com/token`), custom-scheme redirect
  `dev.xxemail:/oauth2redirect`, scope string
  `openid email profile https://www.googleapis.com/auth/gmail.modify`.
- `data/auth/AuthRepository.kt` — builds the auth intent (PKCE via
  `CodeVerifierUtil`), validates redirect `state` against the outstanding
  request, exchanges the code, extracts the account email locally from the
  ID token (no extra round-trip), owns the shared refresh service and the
  `reauthNeeded` flow.
- `MainActivity.kt` — `singleTask` activity; OAuth redirects land in AppAuth's
  `RedirectUriReceiverActivity` and are funneled here via activity result or
  `onNewIntent` (including a redirect that arrives before the callback is
  registered).
- `docs/oauth-setup.md` — the ~5-minute BYO-client-ID walkthrough.

### 1.2 Mailbox

- `ui/mailbox/MailboxScreen.kt` + `MailboxViewModel.kt` — conversation list
  with five inbox tabs (`PRIMARY`, `SOCIAL`, `PROMOTIONS`, `UPDATES`,
  `FORUMS`, mapped from Gmail `CATEGORY_*` labels) plus a folder sheet:
  Starred, Snoozed, Sent, Outbox (local queued + failed sends), Drafts
  (browse-only), Spam, Trash, All mail. Account switcher, add-account entry,
  pull-to-refresh, app-open sync, per-folder "load more" pagination,
  long-press multi-select with a selection bar for bulk actions,
  configurable swipe left/right, "sign in again" banner (from
  `reauthNeeded`), failed-send banner, sync-error snackbar, corrupted-token
  store banner.
- `domain/Folders.kt` — `MailboxFolder` enum (titles, `CATEGORY_*` mapping,
  `labelId` per non-inbox folder), `INBOX_TABS`, `serverLabelFilter`,
  `SnoozePresets` (later-today / tomorrow-morning / next-week / weekend math).
- `data/repo/MailRepository.kt` — `observeFolder`, `ensureHydrated`,
  `hasMorePages`/`loadMore` (server pagination cursors persisted per folder),
  `sync` (full first pass, `historyId` delta after; checkpoint never advances
  ahead of processed work), `hydrateThreads`, label ops below.
- Thread actions, each returning an `Undoable` reverted from the snackbar
  while the window is open (`MailRepository.kt`): `archive` (remove `INBOX`),
  `trash` (`threads.trash`), `toggleStar`/`toggleStars`, `markRead`,
  `reportSpam`, `applyLabel`, `snooze` (server-side `INBOX` removal + durable
  local wake row), `unsnooze`/`restoreFromSnooze` (re-add `INBOX`).
  Local-first: the Room row is updated immediately, then the API call runs.
- `data/repo/AccountRepository.kt` — `register`, `remove` (restores
  still-snoozed threads to `INBOX` server-side *before* tokens drop,
  best-effort with user-visible warnings; cancels scheduled device work;
  deletes token, cache rows, payload files, then the account row; server data
  untouched).

### 1.3 Thread view

- `ui/thread/ThreadScreen.kt` + `ThreadViewModel.kt` — messages in date
  order; marks the thread read on open; lazily fetches full bodies +
  attachment metadata (`loadFullThread`); attachment chips with download;
  reply / reply-all / forward entry points carrying `threadId`,
  `quoteMessageId`, `mode`; archive/trash/spam/star/read toggles with undo;
  failed actions surface as a snackbar and the user stays put.
- `data/api/MessageParts.kt` — pure recursive walkers: `hasAttachment`,
  `findBody` (by MIME), `attachments` (filename + attachment id at any
  nesting depth).
- `data/api/ThreadAggregation.kt` — pure thread → denormalized row mapping:
  labels are unioned over *all* messages (a replied thread stays in the inbox
  while any message keeps `INBOX`), newest-message subject/from/date,
  unread/starred/attachment flags.
- `domain/Recipients.kt` — pure reply / reply-all set computation with a
  lenient RFC 5322 parser (quoted display names with commas stay one
  address); unparseable lists degrade to bracket/bare-address salvage, never
  to dropping every recipient.

### 1.4 Compose / send pipeline

- `ui/compose/ComposeScreen.kt` + `ComposeViewModel.kt` — new / reply /
  reply-all / forward with prefill (identical args keep user edits across
  config changes; fresh args reset the draft so bodies never leak between
  drafts); To/Cc/Bcc/Subject/body fields; staged attachments with MIME types;
  forward-attachment offers pulled from the original message; schedule-send
  presets; send enqueues to the outbox and reports "Queued", never a failed
  send, including with no network (airplane-mode contract).
- `data/repo/MailRepository.kt` — `enqueueSend` (MIME compose → file-backed
  payload → `OutboxEntity` → WorkManager request with undo/schedule delay),
  `cancelQueuedSend`, `retryFailedSend(s)`, `discardOutbox`,
  `observeFailedSends`/`observeOutbox`, `archiveAfterSend` (send-and-archive
  setting; `SENT` is recorded before archiving so a crash never resends).
- `sync/OutboxSend.kt` — pure enqueue math: `SEND` fires at
  now + undo-seconds, `SCHEDULED_SEND` at the chosen time; connected-network
  constraint; queued/snackbar labels.
- `sync/OutboxWorker.kt` — sends queued RFC822 payloads; the undo window is
  the WorkRequest's `initialDelay`, so cancelling the unique work *is* undo;
  atomic `QUEUED→SENDING` claim races undo's `QUEUED→CANCELLED` flip
  (two-sided CAS — whichever wins, the other deterministically loses);
  threaded sends use the multipart upload (the media upload cannot carry
  `threadId`); a 2xx with an undecodable body still counts as sent; process
  death mid-send surfaces the row as `FAILED` for banner retry, never as a
  silent duplicate.
- `sync/SendRetryPolicy.kt` — pure table: 2xx → sent (always wins, even on
  decode failure); transport-unknown / 429 / 5xx → retry (max 5 attempts);
  other 4xx → failed.
- `sync/SendCancelPolicy.kt` — pure undo gate: only a still-`QUEUED` row may
  be cancelled; `SENDING` must be refused.
- `sync/OutboxFiles.kt` — file-backed payloads under `files/outbox/{id}.eml`
  (tmp-file + rename; relative paths validated on every read) so Room never
  pulls megabytes of RFC822 through a cursor window; legacy v0.1 base64 rows
  are migrated to files on first send attempt.
- `data/api/MimeComposer.kt` — RFC 822 composition with Angus Mail
  (Thunderbird Android's stack): UTF-8 subject, `In-Reply-To`/`References`
  chaining, multipart for attachments, 18 MB total cap accounting for base64
  inflation, base64url `raw` output. Delivery is REST-only; SMTP is never
  used.
- `data/api/GmailUploads.kt` — `uploadType=media` body for fresh sends,
  `uploadType=multipart` (`{threadId}` JSON + raw octets, static boundary,
  thread id JSON-escaped) for replies into an existing thread.

### 1.5 Snooze

- `MailRepository.snooze/unsnooze/restoreFromSnooze/scheduleWake` — snoozing
  removes `INBOX` server-side immediately and records a durable wake
  (`snooze_wakes` table + WorkManager wake); unsnooze cancels the work and
  restores `INBOX`; wake restores without cancelling its own running work.
- `sync/SnoozeWorker` (in `OutboxWorker.kt` file) — the `snooze_wakes` table
  is the source of truth: input data is a fast path, and with empty input
  the worker drains all due rows; requires network and retries until every
  wake succeeds (an unwoken snooze is mail silently stuck out of the inbox).
- Honest trade-off carried from the Android README: while snoozed, mail
  leaves *every* client's inbox; wake state lives only on this device, so
  uninstalling while mail is snoozed strands it in All Mail / search until
  manually unsnoozed elsewhere.

### 1.6 Sync / new-mail detection / notifications

- `sync/SyncWorker.kt` + `SyncScheduler` — polling engine (no FCM by design,
  see `docs/architecture.md`): periodic poll with a 15-minute floor and a
  `CONNECTED` constraint; manual kicks and delta-overflow follow-ups as
  one-shot work; per-account `sync()`; success with ≥1 account syncs, else
  retry ≤3 times. README marks device behavior unproven (🚧).
- `sync/NewMailDetector.kt` — pure gate: only a pass that resumed from an
  existing checkpoint may notify (first sync and forced rebuilds see the
  whole mailbox and must never fire a wall of alerts); "new" means a
  currently-unread inbox thread absent from the pre-sync snapshot.
- `notify/Notifier.kt` — per-account notification groups (max 5 individual
  rows + a summary); tap carries account + thread id; `POST_NOTIFICATIONS`
  is checked on Android 13+; channel `new_mail`.
- `ui/nav/NotificationTapNav.kt` — pure tap routing: already on the target
  mailbox → push the thread; elsewhere → land on the target mailbox first
  (cold start and warm `singleTask` taps both honored).

### 1.7 Search

- `ui/search/SearchScreen.kt` — server search with operator hints
  (`from:`, `has:attachment`, `after:`/`before:`, `subject:`, `is:unread`,
  `newer_than:`) passed through as Gmail `q`; offline search over the local
  FTS index.
- `domain/Fts.kt` (`FtsEscaper`) — raw input never reaches `MATCH`: every
  token is double-quoted as a literal phrase with a trailing prefix `*`, so
  `AND`/`OR`/`NOT`/punctuation cannot throw malformed-match errors.
- `MailRepository.searchServer/searchLocal` — server `q` passthrough;
  local FTS over subject/snippet/from/to only (never bodies, by design).
- `domain/LabelCsv.kt` — exact-token matching/editing for comma-joined label
  lists (`,SENT,` never matches `,CONSENT,`); Room queries mirror this with
  `instr(',' || labelsCsv || ',', …)` instead of `LIKE`.

### 1.8 Settings

`ui/settings/SettingsScreen.kt` over `data/repo/SettingsRepository.kt`
(DataStore `settings` — nothing here ever leaves the device):

| Setting | Key | Default |
|---|---|---|
| OAuth client ID | `oauth_client_id` | unset (setup required) |
| Swipe right / left | `swipe_{right,left}_action` | DELETE / ARCHIVE (`SwipeAction`: ARCHIVE, DELETE, MARK_READ, STAR, SNOOZE) |
| Undo window | `undo_send_seconds` | 10 s, clamped 5–30 |
| Sync interval | `sync_interval_minutes` | 15 min, floor 15 |
| Theme mode / preset / dynamic colors | `theme_mode` / `theme_preset` / `dynamic_colors` | DARK / family preset / on |
| Send-and-archive | `send_and_archive` | off |
| Notifications | `notifications_enabled` | on |
| Remote images | `remote_images_allowed` | off (tracking pixels stay dead) |
| Last-used account | `last_account` | cold start returns here |

Plus: per-account remove flow with the snooze-restore warning dialog,
"Revoke Google access" link, Logs viewer entry.

### 1.9 Theme / family sync

- `theme/FamilyTheme.kt` — pure: seven named presets (AMOLED Night,
  Graphite, Forest Night, Ocean Drift, Burgundy, Paper, Mist) + Custom;
  display names match the launcher broadcaster verbatim; luminance-based
  foreground choice; channel-wise surface mixing so raised surfaces keep the
  preset's hue; `resolveSyncedTheme` (named preset wins; Custom requires the
  `BACKGROUND` extra; unknown → null).
- `theme/ThemeSyncReceiver.kt` — exported receiver for
  `xx.launcher.THEME_CHANGED` (`THEME_NAME` / `BACKGROUND` extras), gated by
  the launcher-owned `THEME_SYNC` permission which the app uses but never
  declares (a second declaration breaks installation).
- `theme/ThemeStore.kt` + `ThemeController.kt` — persist last broadcast;
  apply it on first frame; live-restyle Compose on broadcast; in-app picker
  persists and restyles exactly like a broadcast.
- `ui/theme/Theme.kt` — Material3 scheme derived from the family palette.
- `MainActivity.kt` theme wiring — window/status/navigation bars painted with
  the family ground.

### 1.10 Logs

- `log/AppLog.kt` — on-device ring/file log (errors stay on-device; no crash
  reporting SDK); `log/LogsUi.kt` — in-app viewer reachable from Settings.

### 1.11 Data stores (Android)

- Room `XxEmailDb` v3 (`data/db/XxEmailDb.kt`, `Entities.kt`, `Daos.kt`;
  exported schemas `app/schemas/…/1.json…3.json`): `accounts` (email,
  displayName, `historyId` uint64-as-string, `lastSyncAt`), `labels`,
  `threads` (denormalized aggregates + `snoozedUntil`), `messages`
  (headers, snippets, bodies, `attachmentsJson`, `bodyFetched`), FTS4
  `messages_fts` over subject/snippet/from/to, `outbox`, `snooze_wakes`,
  `folder_pages` (per-folder pagination cursors). Migrations v1→v2 (historyId
  INTEGER→TEXT via copy, file-backed outbox columns, `snooze_wakes` table)
  and v2→v3 (`attachmentsJson`, `folder_pages`); never destructive — queued
  sends and snoozes survive upgrades (`MigrationTest`).
- `TokenStore` (`data/auth/TokenStore.kt`; `TokenStoreTest`,
  `androidTest …TokenStoreTest.kt`): one serialized AuthState per account in
  `authstates.bin`, AES-256-GCM via AndroidKeyStore (`xxemail_master` key),
  atomic tmp+rename persist with fsync, unreadable-store flag (never pretend
  a lost key means "no accounts" — surface re-auth instead).
- `TokenCache.kt` — in-memory per-account sessions, change-detecting
  persistence, per-account serialized refresh (no duplicate bursts),
  generation-guarded `clear` (a stale in-flight refresh never overwrites
  fresh tokens), permanent failures (`invalid_grant`) surface as re-auth.
- `GmailApiFactory.kt` — OkHttp auth interceptor (Bearer from the cache) +
  single-retry 401 authenticator that routes through the cache so revoked
  grants raise the existing "sign in again" banner instead of failing
  silently.
- `files/outbox/{id}.eml` payloads; attachment downloads in the app-private
  cache; `SharedPreferences xx_email_theme`.
- Backup explicitly disabled: `allowBackup=false`, `backup_rules.xml`,
  `data_extraction_rules.xml` — credentials and cache never leave the device
  via cloud backup.

### 1.12 Permissions and integrations (Android)

From `AndroidManifest.xml` + `PRIVACY.md`: `INTERNET` (Google endpoints
only), `POST_NOTIFICATIONS` (runtime on 13+), launcher-owned `THEME_SYNC`
use-only, `queries` for Custom Tabs browsers, AppAuth redirect scheme,
FileProvider (`file_paths`) for attachment viewing. Nothing else — no
contacts, location, storage, phone, or camera permissions. Egress limited to
`accounts.google.com`, `oauth2.googleapis.com`, `gmail.googleapis.com`
(+ optional HTTPS image hosts when the user enables remote images).
Dependencies: Retrofit/OkHttp, kotlinx.serialization, AppAuth, Room,
WorkManager, DataStore, Angus Mail (jakarta.mail + jakarta.activation).

## 2. Linux implementation per feature

General rules: stdlib + GI only (no pip dependency — every target distro
packages Python 3.12, GTK4, libadwaita, sqlite, and the Secret Service; §2.13
justifies the zero-pip stance). GTK main-loop rules from `XXWM_RULES.md`
apply throughout: no blocking calls on the main thread — network and sqlite
I/O run on worker threads posting back via `GLib.idle_add`; D-Bus uses async
calls; replies unpack as tuples (`reply.unpack()[0]`); signal callbacks take
`(conn, sender, path, iface, signal, params)` with no trailing `user_data`.

### 2.1 Gmail REST client (follows the Android app — REST, not IMAP)

Keep the Android architecture: Gmail REST over HTTPS with Bearer tokens.
Implement with **stdlib `urllib` + `json`** as the primary transport (not
libsoup-via-GI): `urllib` is always present, its behavior is identical on
every target distro, and it is unit-testable without a session bus. Port the
full `GmailApi.kt` surface (`getProfile`, `listLabels`, `listThreads`,
`getThread`, `modifyThread`, `trashThread`/`untrashThread`, `getMessage`,
`modifyMessage`, `sendRaw`/`sendMultipart`, `getAttachment`, `listHistory`,
`getVacation`, `listSendAs`): one thin function per endpoint in a
`gmail_client` module, each taking an access token and returning plain
dicts/dataclasses. Timeouts mirror the factory (20 s connect, 60 s read).
Port the 401 authenticator logic exactly: one retry with a fresh token, then
surface re-auth (raise a typed `ReauthNeeded` the UI already handles via the
sign-in-again banner); a second 401 on the retried request must not loop.
`historyId` stays an opaque string — never arithmetic (uint64 overflow).
What changes: Retrofit/OkHttp → `urllib`; kotlinx.serialization DTOs →
dataclasses with `ignore-unknown-keys` semantics (never crash on new Google
fields). IMAP/SMTP with XOAUTH2 is *not* built (the Android app never uses
SMTP — delivery is REST-only — and IMAP would lose tabs/snooze/labels
semantics); it stays a roadmap item exactly as on Android.

### 2.2 OAuth installed-app flow (loopback, not custom scheme)

The Android custom scheme (`dev.xxemail:/oauth2redirect`) is NOT portable:
on Linux there is no reliable per-app scheme claim (registration varies by
launcher/portal and collides across installs). Use the RFC 8252
**loopback redirect**: open `https://accounts.google.com/o/oauth2/v2/auth`
(`access_type=offline`, `prompt=consent`, PKCE S256 via `hashlib`+`secrets`,
`state` validated like `AuthRepository.pendingAuthRequest`) with
`Gtk.UriLauncher` in the user's browser, and receive the code on a
one-shot `http.server` bound to `127.0.0.1` with an OS-chosen port in a
worker thread. Exchange the code at `oauth2.googleapis.com/token` over
`urllib`; parse the account email locally from the ID token payload
(base64url, no extra round-trip). Consequence the spec must state: the user
creates a **Desktop-type** OAuth client (loopback-capable), not the Android
type — update `docs/oauth-setup.md` accordingly in a later lane. Refresh
uses the same endpoint with the stored refresh token; `invalid_grant`
surfaces as re-auth, exactly like `TokenCache`. BYO-client-ID setup and the
"no middleman server" posture are unchanged.

### 2.3 Token storage (Secret Service, not KeyStore)

Port `TokenStore.kt` to **libsecret via GI `Secret`**: one item per account
in the default collection, attributes
`{application: io.piercingxx.XXEmail, account: <email>}`, storing refresh
token + client id + refresh-time metadata as JSON (never the access token
alone — it must be refreshable). Atomicity comes from the Secret Service
itself; keep the Android guarantees that matter: an unreadable/locked store
is flagged, never mistaken for "no accounts" (surface re-auth); removal
deletes the item. Port `TokenCache` semantics in-process: in-memory
sessions, per-account serialized refresh, change-detecting persistence,
generation-guarded clear. The KeyStore hardware-backing claim becomes the
login-keyring equivalent (unlocked at login on Phosh/FuriOS); document that
a locked collection at sync time means "retry later", not failure.

### 2.4 Storage (sqlite3 + FTS5, not Room)

Port `XxEmailDb` to **stdlib `sqlite3`** with the same 8 tables and column
semantics (`Entities.kt` maps 1:1; `historyId TEXT`; outbox `path`/`size`
file-backed from day one — no legacy base64 column). Port the DAO query set
(`Daos.kt`), including the exact-token `instr(',' || labelsCsv || ',', …)`
label matching and the inbox-category queries. FTS4 → **FTS5** external-content
over the same 4 columns (subject/snippet/from/to — never bodies);
`FtsEscaper` quoting ports verbatim (FTS5 accepts the same `"token"*`
shape). Schema version in `PRAGMA user_version` with the Android v1→v2→v3
migrations as the baseline (fresh installs start at v3; the migration code
exists so a future Android-export import of a v1/v2-era dump still applies).
Concurrency: one connection per thread (or a single worker-thread owner),
WAL mode, parameterized queries only. SQLCipher stays roadmap, as Android.

### 2.5 Settings, theme, logs

- Settings → JSON object in `config.json` with the same keys/defaults as
  §1.8 (`SettingsRepository` ports 1:1, including 5–30 s undo clamp and the
  ≥15 min sync floor). Missing keys fall back, unknown keys are ignored, an
  invalid file keeps the last good config — the `docs/config.md` contract.
- Family theme → read the user's XX-WM theme from
  `~/.config/xx-wm/config.json` (`theme`, `custom_background`,
  `prefer_dark`, `font`, `text_size_scale`) and map it onto the same 8
  presets + custom in this repo's `THEME_PRESETS`/`FONT_FAMILIES` (scaffold
  `theme.py` already does this). Live restyle via `Gio.FileMonitor` on the
  shell config (there is no broadcast receiver on Linux); in-app override
  persists like `ThemeController.applyManual`. Android→Linux preset map:
  AMOLED Night→`amoled`, Graphite→`graphite`, Forest Night→`forest`,
  Ocean Drift→`ocean`, Burgundy→`burgundy`, Paper→`paper`, Mist→`mist`,
  Custom→`custom` (+background); `aura` is Linux-only (no Android source).
  Fall back to libadwaita dark/light (`prefer_dark`) when no XX-WM config
  exists (e.g. under Phosh without XX-WM).
- Logs → text file in `XDG_STATE_HOME` + in-app viewer page (`AppLog`/
  `LogsUi` port); still no crash-reporting SDK.

### 2.6 Sync engine, outbox, snooze, notifications

- WorkManager → **systemd user units**: `xx-email-sync.service` (oneshot
  worker) + `xx-email-sync.timer` (15 min floor, `Persistent=true`,
  `RandomizedDelaySec` so a fleet of phones does not thunder Google
  simultaneously). The worker does one pass per account: labels → full or
  delta sync → outbox drain → due snooze wakes, then exits. Manual
  pull-to-refresh/app-open sync call the same code in-process (immediate
  kick also pokes the service over D-Bus, §5). `NewMailDetector`,
  `SendRetryPolicy`, `SendCancelPolicy`, `OutboxSend`, `OutboxFiles`
  (paths under the XDG data dir) port verbatim — including the two-sided
  CAS undo race, "2xx always wins", SENDING-after-death → FAILED, and the
  snooze table as source of truth with retry-until-woken.
- Battery/standby: the timer carries no wakefulness guarantee, so the worker
  checks `GNetworkMonitor` connectivity and UPower battery state before a
  poll (skip non-urgent polls when offline; back off while the battery is
  critically low unless the user just pulled to refresh). It additionally
  honors the shell's app-standby/focus state through the shell's documented
  IPC when present (paused apps do not poll) — and never implements a
  parallel standby tracker. Polling latency stays ~15 min like Android; there
  is no Doze equivalent to fight on Linux, but there is also no batching
  guarantee, hence the randomized delay.
- Notifications → `Gio.Application.send_notification` on the session bus
  (grouped per account, same max-5 + summary shape, tap opens the thread via
  the app's `open-thread` action with account+thread target, warm and cold).
  No permission prompt exists on Linux; muting/DnD/focus/lock-screen policy
  is the shell's (`muted_apps`, schedules, `lock_screen_notifications`) and
  applies automatically because delivery goes through the shell's daemon.

### 2.7 UI (GTK4 + libadwaita, not Compose)

One `Adw.Application` (`io.piercingxx.XXEmail`), `Adw.NavigationView` stack
mirroring `Routes.kt` (setup → mailbox → thread / compose / search /
settings), responsive to 360x720 (min 320x640): single-column pages, bottom
sheet for the folder list, search field reachable at the bottom, no
horizontal overflow, long labels ellipsize or wrap. Text-first PiercingXX:
no icon grids, monochrome from the active preset, large type, same fonts.
Per-screen ports: mailbox tabs + folder sheet + swipe gestures
(`Gtk.GestureSwipe`, same 5 actions) + multi-select bar; thread page with
plain-text bodies (see §2.9); compose with attachment picker; search with
operator hints; settings rows from §1.8; logs page. State updates flow from
the sqlite layer into list models on `GLib.idle_add` — never a blocking call
on the main thread.

### 2.8 Compose addressing and contacts (EDS)

Recipient autocomplete and sender display names read the **Evolution Data
Server address book** (`EBook`/`EDataServer` via GI) — the platform contact
store on GNOME-based phone distros. Reply math (`Recipients.kt`) ports to
stdlib `email.utils.getaddresses` (lenient, keeps quoted-comma names
intact) with the same salvage fallback. MIME compose/parse (Angus Mail →
stdlib `email` package: `EmailMessage`, `email.parser`, `email.policy`):
same headers (`In-Reply-To`/`References` chaining), same 18 MB cap, same
base64url `raw`, same multipart-vs-media transport choice. Attachment
filenames pass through the `SafePaths` port (strip components, reject
blank/`.`/`..`/NUL, 200-char cap, canonical `is-inside` check).

### 2.9 Bodies and attachments (plain text + portals, not WebView/FileProvider)

WebKit is outside the allowed stack, so thread bodies render as **sanitized
plain text** in a text view: decode the `text/plain` part when present,
else convert `text/html` with `html.parser` (strip `script`/`style`, inline
event handlers, `javascript:` URLs — scripts could never run here anyway).
This makes the remote-image policy stronger than Android's default: no remote
fetch happens during render at all. An explicit per-message "load remote
images" affordance may download HTTPS images to cache (2 MB cap, same as
`RemoteImagePolicy`) for viewing only — a later lane, default off.
Attachments: picking via `Gtk.FileDialog` (portal-backed file chooser),
saving via the Save portal path, opening via `Gtk.UriLauncher` with the
system handler (replaces FileProvider grants). Upload size accounting keeps
the ~33% base64 inflation margin.

### 2.10 Accounts, backup, import

`AccountRepository.remove` ports step-for-step (snooze restore first, cancel
scheduled work, delete token/cache/payloads, server untouched, warnings
surfaced). Backup: versioned JSON export of settings + snooze wakes + outbox
metadata (never tokens, never mail bodies — mirroring the shell's
`backup.py` exclusions); restore validates before writing. Import from
Android: the Android app currently produces **no export** (cloud backup is
disabled by design), so migration v1 is server re-sync (mail, labels) plus
manual re-snooze; the spec reserves a JSON handoff (`snooze_wakes` +
`folder_pages` + settings, schema versioned) for a future Android export,
and accepts standard **vCard** for contacts if the user moves any.

### 2.11 Push, ML, permanent delete — NOT portable (by design, as on Android)

- Push (FCM / Gmail Pub/Sub relay): excluded — needs a relay server and a
  persistent connection; polling stands in, exactly like Android.
- Smart Reply/Compose/nudges/Gemini: excluded — server ML, intentionally out.
- Permanent delete: excluded — `gmail.modify` cannot bypass trash
  (server-enforced); trash-only stands.
- Drafts editor and vacation-responder editor: roadmap parity with Android
  (Drafts folder browsable; `getVacation` mapped, editor pending).

### 2.12 Android-only mechanisms and their Linux equivalents

| Android mechanism | Linux equivalent | Why it changes |
|---|---|---|
| AppAuth + custom scheme | Loopback redirect + `Gtk.UriLauncher` (§2.2) | No reliable per-app scheme claim on Linux |
| AndroidKeyStore AES-GCM | Secret Service via GI `Secret` (§2.3) | Platform credential store |
| Room + FTS4 | stdlib sqlite3 + FTS5 (§2.4) | No AndroidX; same queries |
| DataStore settings | `config.json` (§2.5) | Shell config contract |
| WorkManager (poll/outbox/wake) | systemd user service + timer (§2.6) | Platform scheduler |
| `NotificationManager` channels | `GNotification` on session bus (§2.6) | Shell daemon renders policy |
| Theme broadcast receiver | `Gio.FileMonitor` on shell config (§2.5) | No broadcasts on Linux |
| FileProvider | XDG portals + `Gtk.UriLauncher` (§2.9) | Platform sharing |
| Angus Mail / jakarta.mail | stdlib `email` (§2.8) | Zero-dep stack |
| Compose HTML view | Sanitized plain-text view (§2.9) | No WebKit in allowed stack |
| `POST_NOTIFICATIONS` prompt | None (bus-mediated) | No runtime-permission model |

### 2.13 Dependency justification (no pip)

The port needs no pip package: `urllib`/`json`/`sqlite3`/`email`/
`hashlib`/`secrets`/`http.server` (stdlib) cover network, storage, MIME,
and PKCE; `Gtk`/`Adw`/`Gio`/`GLib`/`Secret`/`EBook` (GI system libraries)
cover UI, D-Bus/activation, credential storage, and contacts. Adding any pip
dependency would break installability on image-based phone distros and
complicate offline builds, so none is specified. If a later lane wants
`libsoup` via GI, it must prove a need `urllib` cannot meet; `urllib` stays
the default.

## 3. Integration with XX-WM

The app id is `io.piercingxx.XXEmail` (desktop id `io.piercingxx.XXEmail`,
D-Bus names `io.piercingxx.XXEmail*`). This id is the integration contract —
the shell keys home slots, search, mute rules, and focus rules off it.

### 3.1 What the app feeds or replaces

- **Comms home folder → Email slot.** Per `design.md`, the Comms folder holds
  Phone, Text, Email, Chat, SoftPhone; Phone/Text bind to the device's real
  telephony apps (GNOME Calls / Chatty) with the shell dialer/SMS as fallback.
  Email has **no shell fallback** — when `io.piercingxx.XXEmail` is installed
  it fills the Email member (resolved by app name like the other seeded
  members); when absent the member is skipped, exactly like other missing
  members. No shell code changes are needed beyond the seed list already
  handling "Email".
- **Notifications.** The app is a pure *client* of
  `org.freedesktop.Notifications`, which the shell's `notif_daemon.py` owns
  (with `DO_NOT_QUEUE`: it backs off if another daemon owns the name). The
  app never owns that name, so there is no fight. Shell policy —
  `muted_apps` ("Disable for…"), DnD (with starred/repeat-caller exceptions,
  which do not apply to mail), focus-mode holds, `lock_screen_notifications`
  summary/count/off — applies to mail notifications with zero app code.
- **Contacts.** Complementary, not shared: `contacts.py` serves telephony
  caller-ID/SMS from VCF + local JSON; the app reads recipient/sender names
  from EDS (§2.8). No shared store, no sync, no conflict.
- **Telephony (ModemManager Voice/Messaging).** Untouched. Email is IP-only;
  `modem_monitor.py`, `call_ui.py`, `dialer.py`, `sms.py` neither feed the
  app nor defer to it.
- **Theme.** The app follows the shell (§2.5); the shell never follows the
  app. Under Phosh without XX-WM the app falls back to libadwaita
  dark/light and its own preset picker.

### 3.2 Ownership conflicts and how they are avoided

The lane rule: *the shell defers when the app owns its D-Bus name*. For
email the deferral surface is small but explicit:

- `io.piercingxx.XXEmail` (UI) and `io.piercingxx.XXEmail.Sync` (background)
  are owned solely by the app; the shell must never own, queue for, or
  spoof these names. Like `notif_daemon.py`'s `_on_name_lost`, any shell
  component that ever probes these names backs off silently when owned.
- The app must never own `org.freedesktop.Notifications` (it is a client)
  and never drives modem/call/SMS interfaces.
- New-mail polling pauses while the shell reports the app paused/focus-held
  via its documented IPC (§2.6); the app never invents its own standby
  signal, so two trackers cannot disagree.
- Account removal while the shell shows a notification for that account:
  removal cancels scheduled work first (`AccountRepository.remove` order),
  and the sync service withdraws that account's notifications on its next
  pass; stale taps land on the account switcher, never a dead mailbox
  (mirrors the Android F2 post-removal navigation).

## 4. Data model + storage paths (XDG)

Base dirs honor `XDG_CONFIG_HOME` / `XDG_DATA_HOME` / `XDG_CACHE_HOME` /
`XDG_STATE_HOME` (defaults `~/.config`, `~/.local/share`, `~/.cache`,
`~/.local/state`):

| Path | Contents |
|---|---|
| `~/.config/xx-email/config.json` | Settings (§1.8 keys; client ID lives here — public, not secret) |
| `~/.local/share/xx-email/xxemail.db` | sqlite, §2.4 schema, `user_version=3` |
| `~/.local/share/xx-email/outbox/{id}.eml` | File-backed RFC822 payloads (§2.6) |
| `~/.cache/xx-email/attachments/` | Downloaded attachments (evictable) |
| `~/.cache/xx-email/images/` | Opt-in remote-image cache (later lane) |
| `~/.local/state/xx-email/xx-email.log` | On-device log (§2.5) |
| Secret Service (`org.freedesktop.secrets`) | Per-account OAuth JSON (§2.3) — never on disk otherwise |

sqlite tables mirror `Entities.kt` 1:1: `accounts(email, displayName,
historyId TEXT, lastSyncAt)`, `labels(accountEmail, id, name, type,
colorBg, colorText)`, `threads(accountEmail, id, snippet, subject,
fromAddress, fromName, date, messageCount, unreadCount, hasAttachments,
starred, inInbox, categories, labelsCsv, snoozedUntil)`,
`messages(accountEmail, id, threadId, subject, fromAddress, toCsv, ccCsv,
date, snippet, read, starred, hasAttachments, labelsCsv, messageIdHeader,
bodyHtml, bodyPlain, bodyFetched, attachmentsJson)`, FTS5
`messages_fts(subject, snippet, fromAddress, toCsv)`, `outbox(id, accountEmail,
kind, threadId, path, size, subject, targetAt, state, attempts, error,
createdAt)`, `snooze_wakes(accountEmail, threadId, targetAt, createdAt)`,
`folder_pages(accountEmail, folderKey, nextPageToken)`. Bodies stored as
text (converted per §2.9); `bodyHtml` keeps the original HTML only for
messages with no text part (source for the future image-consent lane,
capped in size at sync time).

Import path from Android (§2.10): v1 = server re-sync + reserved
versioned-JSON handoff for snooze wakes/settings; vCard for contacts.

## 5. Architecture

Packages (under `src/xx_email/`, flat imports like the shell):

- `main.py` — `Adw.Application` (`io.piercingxx.XXEmail`,
  `DBusActivatable`), single `Adw.NavigationView`, theme application,
  `Gio.FileMonitor` on the shell config, `open-thread` action.
- `ui/` (later lanes) — one module per route: `setup`, `mailbox`, `thread`,
  `compose`, `search`, `settings`, `logs`; responsive helpers shared.
- `config.py` — settings load/save/validate (scaffolded).
- `theme.py` — presets, shell-config read, CSS generation (scaffolded).
- `gmail_client.py` — endpoint functions (§2.1).
- `auth.py` — loopback flow + refresh + `ReauthNeeded` (§2.2).
- `token_store.py` — Secret Service wrapper + in-memory cache (§2.3).
- `db.py` — schema, migrations, DAO functions (§2.4).
- `repos.py` — account/mail/settings repositories (§1.2, §1.8, §2.10).
- `sync_service.py` — one-pass worker used by the timer unit and manual
  kicks (§2.6); outbox drain; snooze wakes.
- `notify.py` — `GNotification` helpers + tap targets (§2.6).
- `domain.py` — pure ports: folders/snooze presets, recipients, safe paths,
  FTS escaper, label CSV, remote-image policy, message parts, aggregation,
  uploads, retry/cancel policies, outbox math, new-mail detector.
- `log.py` — on-device logging (§2.5).

Threading: the GTK main loop runs UI only. Every network call, sqlite
write, and Secret Service round-trip runs on a worker thread
(`concurrent.futures.ThreadPoolExecutor`, one shared instance) or an async
Gio call; results return via `GLib.idle_add`. The sync systemd unit is a
separate process and never touches GTK. D-Bus names/paths:

- `io.piercingxx.XXEmail` — UI application (D-Bus activatable via
  `.desktop` `DBusActivatable=true`; `GApplication` owns the name while
  running, backed off otherwise — same pattern as `notif_daemon.py`).
- `io.piercingxx.XXEmail.Sync` + object
  `/io/piercingxx/XXEmail/Sync`, methods `SyncNow()`, `OutboxKick()`,
  `CancelSend(id)` — owned by the background service while it runs.
- Notifications are sent, not owned, on `org.freedesktop.Notifications`.

Background parts: `xx-email-sync.service` / `xx-email-sync.timer`
(systemd user units, installed to `systemd/user/`, enabled by a setup step
documented in README — no XDG autostart for the worker; the GUI itself does
not autostart). D-Bus activation file for the UI (`D-Bus session
services/`). No root, no system bus, no PolicyKit.

## 6. Non-goals (this lane and the port overall)

Push relay, server ML features, permanent delete, IMAP/JMAP backends,
OpenPGP/OpenKeychain, SQLCipher, per-label notification rules, vacation
editor, drafts editor, HTML rendering/WebKit, any pip dependency, any
telemetry/crash-reporting SDK, any scope beyond `gmail.modify` (+ identity).
Each is rejected the same way the Android TODO rejects it — out of scope,
not deferred tech debt.

## 7. Milestone plan (build lanes, each ≤ ~1500 lines + acceptance tests)

1. **Scaffold** (this lane): meson install, `Adw.Application` placeholder
   applying the XX-WM theme, pure `theme` + `config`, headless pytest,
   ruff gate. Accept: gate commands in §8 pass; window opens on-device.
2. **Pure domain ports**: `domain.py` (§2.6–§2.8 list) ported from the
   Kotlin + JVM unit tests ported to pytest (snooze-preset math, recipients
   edge cases, FTS escaping, label CSV, retry/cancel tables, outbox math,
   new-mail detector, safe paths, aggregation). Accept: ported tests green;
   no GTK/GI imports in the module.
3. **Storage**: `db.py` schema + migrations + DAO parity + settings store.
   Accept: golden queries against a seeded db; migration v1→v3 walk;
   FTS5 search returns the same rows as the Room FTS4 semantics.
4. **Auth**: loopback flow, Secret Service store, refresh/cache semantics.
   Accept: real sign-in in a browser, restart persistence, revoke → banner,
   locked-collection retry; no secret ever on disk.
5. **Gmail client + sync engine**: endpoint coverage, 401 handling, full +
   delta sync, history-overflow rebuild, offline error surfacing. Accept:
   first sync hydrates tabs; airplane refresh errors then recovers; second
   sync is delta-only (request log asserts).
6. **Mailbox / thread / search UI**: NavigationView stack, tabs, folders,
   swipe + multi-select, thread view, operator + offline search. Accept:
   360x720 and 320x640 render with no horizontal overflow; keyboard flows;
   mark-read/archive/trash/undo work against a test account.
7. **Compose / outbox / schedule / attachments**: prefill modes, offline
   queue, undo race, portals. Accept: airplane compose → queued → sent once
   online (server-side dedup check); undo inside window cancels; 3 MB
   attachment sends; forward keeps threading.
8. **Background + notifications + snooze**: units/timer, battery/standby
   behavior, grouped notifications with tap routing, snooze/wake round-trip
   incl. kill-and-relaunch. Accept: 15-min poll loop on-device; snooze
   1–2 min restores in inbox + on Gmail web; uninstall-warning text shown.
9. **Settings / accounts / logs / backup / polish**: remove flow with
   warnings, revoke, logs page, JSON export/import, DnD/mute interplay with
   the shell verified. Accept: full lane-2→8 regression green on-device.

## 8. Scaffold gate (this lane)

```sh
../xx-wm/.venv/bin/python -m pytest -q tests
../xx-wm/.venv/bin/ruff check src tests
```

`ref/` trees are read-only reference and are never modified.


## Review notes (orchestrator, 2026-10-15) — these override the text above

1. Contacts (owner directive): recipient autocomplete and sender display names come from xx-contacts' D-Bus API `io.piercingxx.Contacts1` (`Search`, `LookupNumber`-style lookups by email via `Search`), which owns the user's contacts; fall back to EDS read-only, then to the raw header name, when xx-contacts isn't installed.
2. `Adw.PreferencesWindow` is deprecated; use `Adw.PreferencesDialog`.
3. App standby (XX-WM) may freeze the UI process; new-mail polling must live in the separate systemd user service, which the shell exempts by not launching it (only shell-launched apps are frozen).
4. libsecret `*_sync` calls (store/lookup/clear) can block on a keyring unlock prompt: call them only from worker threads, never from the GTK main loop (the L1 TokenStore is sync by design; the sync engine and UI lanes must wrap it).
5. The OAuth loopback `state` check should use `hmac.compare_digest`; the listener must stop after the first valid callback (or the 300 s timeout) — verify in the UI lane.
