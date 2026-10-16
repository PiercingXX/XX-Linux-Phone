# XX-Vitals — Linux Phone Port Spec

Target: postmarketOS / Mobian / FuriOS / PureOS phones running phoc
(XX-WM or Phosh). 360x720 logical px (min 320x640), touch-first, on-screen
keyboard. Stack: Python 3.12+, GTK4 + libadwaita via PyGObject
(`Adw.Application`, `Adw.NavigationView`, responsive), meson install,
stdlib + GI libraries only.

Reference: `ref/xx-vitals/` (Android Kotlin app + FastAPI server, read-only).
Shell conventions: `ref/xx-wm/` (design.md, docs/config.md,
launcher/src/config.py, modem_monitor.py, contacts.py, notif_daemon.py).

## 0. What the Android app is

A cleanroom Google Fit for GrapheneOS, self-hosted on the owner's Synology
NAS. Four tabs (Home, Journal, Browse, Profile), a dual-ring daily summary
(Effort outer, Steps inner), manual weight + manual activity entry, and a
sync engine that treats Postgres-on-NAS as the system of record while the
phone holds a Room replica + offline write queue. No Google account, no Play
Services, no Health Connect (deleted upstream — daily activity metrics
currently have no source; gap days render as gaps, never fake zeroes).

---

## 1. Feature inventory (Android) → Linux implementation

### 1.1 Screens / navigation

| Android (file) | Behavior | Linux implementation | Not portable / equivalent |
|---|---|---|---|
| Single activity + 4-tab nav: `MainActivity.kt`, `ui/navigation/VitalsNavHost.kt`, `ui/components/BottomNav.kt`, `ui/components/Fab.kt` | One activity, bottom bar Home/Journal/Browse/Profile, FAB opens entry sheets, per-tab hide toggles (Home always visible) | `Adw.Application` + `Adw.NavigationView`; bottom tab bar as text-only buttons (PiercingXX: no icon grids); `Adw.BottomSheet`-style dialogs for entry sheets. Module `ui/shell.py`. Tab-visibility toggles persist in app config | Compose `ModalBottomSheet` → `Adw.Dialog`; no direct equivalent of `BackHandler` — use NavigationView pop handling |
| Home: `ui/home/HomeScreen.kt`, `HomeModel.kt`, `GoalCards.kt`, `GoalField.kt`, `StatRow.kt`, `RingPair.kt`, `WeightCard.kt`, `WeeklyTargetCard.kt`, `DailyGoalsScreen.kt`, `WeeklyTargetScreen.kt` | Dual ring (Effort/Steps) vs daily goals, 3-up stat row, "Your daily goals" card, weekly target card, weight card; gap days render as gaps | Custom `Gtk.DrawingArea` ring widget (monochrome, theme colors only) + text cards in `ui/home.py`; pure math stays in `core/metrics.py` (port of `:core` MetricMath/GoalModel). Day-bucket logic ports `core/DayBucketing.kt` | Compose Canvas → Cairo `Gtk.DrawingArea`; Material3 cards → `Adw.Clamp` + `Gtk.Box` rows |
| Journal: `ui/journal/JournalScreen.kt`, `JournalModel.kt`, `ActivityRow.kt`, `DayHeader.kt` | Activities grouped into local calendar days, day header with totals + mini ring, collapsed "— no data —" band for gaps | `ui/journal.py` list over local SQLite rows; grouping helper in `core/journal.py` (port of `JournalModel.groupByDay`) | None — fully portable |
| Browse: `ui/browse/BrowseScreen.kt`, `CategoryRow.kt`, `MetricDetailScreen.kt`, `MetricDetailModel.kt`, `WeightDetailModel.kt`, `charts/MetricChart.kt` | Search over 6 categories (Activity, Body live; Heart Rate, Nutrition, Sleep, Cycle "not tracked"); metric detail + weight detail with charts | `ui/browse.py`; charts via `Gtk.DrawingArea` sparklines/bars (no new dep; Cairo only). "Not tracked" rows stay as honest placeholders | Compose chart code → hand-drawn Cairo; keep the same "not tracked" honesty, never fake series |
| Profile: `ui/profile/ProfileScreen.kt`, `ProfileEditModel.kt`, `SyncSection.kt`, `profile/ProfileModel.kt`, `ProfileRepository.kt`, `ProfileRepositoryProvider.kt`, `UnitFormatter.kt` | Editable goals, height, birth year, metric/imperial toggle, tab-visibility toggles, ingest-batch history (`BatchesRepository.kt`, `BatchesApi.kt`), sync identity + "Sync now" | `ui/profile.py`; preferences in XDG config JSON (not DataStore); batch history from `GET /v1/batches`; `UnitFormatter` → `core/format.py` | DataStore → JSON file; identical semantics (fresh install defaults imperial, saved choice honored) |
| Entry sheets: `ui/weight/WeightEntrySheet.kt`, `WeightEntryModel.kt`, `ui/activity/ActivityEntrySheet.kt`, `ActivityEntryModel.kt` | Manual weight (value + date) and manual activity (type + duration required, steps optional, no GPS in v1) | `ui/entry.py` dialogs with `Gtk.Entry` + date picker (`Gtk.Calendar`); validation mirrors `WeightEntryModel`/`ActivityEntryModel` kind strings (`weight`, `weight.update`, `weight.delete`, `activity`, `daily`) so the offline queue stays wire-compatible | `DatePickerDialog` → `Gtk.Calendar` in a dialog |
| Logs: `log/AppLog.kt`, `log/LogsUi.kt` | On-phone ring-buffer log, copy/share dump, nothing uploaded | `core/applog.py` (stdlib logging to `XDG_STATE_HOME` file + in-memory ring); Profile "Copy logs" uses clipboard portal (`Gdk.Display.get_clipboard()`), no share-sheet — Logs dir path shown as text | `ACTION_SEND` share sheet → clipboard copy + file path; `Toast` → `Adw.ToastOverlay` |

### 1.2 Background behavior

| Android (file) | Behavior | Linux implementation |
|---|---|---|
| Step sampler: `work/StepWorker.kt`, `work/StepSampler.kt`, `work/StepScheduler.kt`, `data/StepReader.kt`, `data/StepMetricMapper.kt` | Unique periodic work, 15-min floor, NO network/charging constraints; reads cumulative `TYPE_STEP_COUNTER`, rebases across reboots (`StepReader.rebase`), cold start attributes nothing, backward counter = sensor-hub restart; derives distance/calories/active-min/effort from steps + profile height + latest weight, marks derived bits | systemd user service `xx-vitals-collect.service` + timer (15 min) running a headless `xx-vitals-collect` entry point that performs one sample pass and exits. No new deps. See §5 for the sensor strategy |
| Sync: `work/SyncWorker.kt`, `work/SyncRunner.kt`, `work/SyncScheduler.kt`, `work/SyncDecision.kt`, `work/ProductionSync.kt`, `sync/SyncEngine.kt` | Unique 24 h CONNECTED periodic work + expedited one-shot "Sync now"; ordered push drain (oldest-first, delete-on-2xx, 507 pauses, 401 aborts to re-login, dead-letter permanent 4xx, backoff for transient) + always-pull delta via `GET /v1/changes?since=` keyed on raw server `ingest_batch.started_at` ISO | Same `SyncEngine` semantics ported to `sync/engine.py` over stdlib `urllib` (no Retrofit/OkHttp — no pip deps). Scheduling: systemd user timer `xx-vitals-sync.timer` (daily + `OnUnitInactiveSec`), plus in-app "Sync now" button spawning a worker thread. 401 → Profile shows re-login; 507 → keep queue, toast "storage full" |
| Theme sync: `ui/theme/ThemeSyncReceiver.kt`, `ThemeController.kt`, `ThemePreset.kt`, `Color.kt`, `Type.kt`, `VitalsTheme.kt` | Manifest receiver for `xx.launcher.THEME_CHANGED` broadcast (name + ARGB), persists to `vitals_theme` prefs, repaints | No broadcast receiver on Linux. App reads `~/.config/xx-wm/config.json` at startup and watches it with `Gio.File.monitor()` (hot-reload like the shell); preset key + font reused verbatim from `THEME_PRESETS`/`FONT_FAMILIES` in `ref/xx-wm/launcher/src/config.py`. Fall back to libadwaita dark/light when absent |
| Permission: `StepPermission.kt`, manifest `ACTIVITY_RECOGNITION` | Runtime permission gates the step sensor; denial = honest "no step source" copy + Grant button | No runtime permission model on Linux. Equivalent state: sensor unavailable (no iio-sensor-proxy / no accelerometer) → same honest "no step source" copy. No permission prompt code |

### 1.3 Data stores (Android → Linux)

| Android (file) | Store | Linux path / tech |
|---|---|---|
| Room replica `data/VitalsDatabase.kt`, `DatabaseProvider.kt`, DAOs in `data/daos/` (entities `DailyMetric.kt`, `Activity.kt`, `WeightSample.kt`, `PendingWrite.kt`, `SyncState.kt`, `DailyRows.kt`, `DailyEntryModel.kt`) | SQLite replica: daily_metric, activity, weight_sample, pending_write, sync_state | SQLite via stdlib `sqlite3`, same table/column names + UNIQUE constraints (D8 idempotence preserved): `~/.local/share/xx-vitals/vitals.db`. `sync_state` single row id=1 with `lastPullIso` raw server ISO (never millis round-trip) |
| `ProfileRepository.kt` (DataStore prefs) | Goals, height, birth year, units (default IMPERIAL), tab visibility | `~/.config/xx-vitals/config.json` (plain JSON, atomic replace) |
| `sync/SyncCredentialsStore.kt` | NAS host URL + fabric token (re-login flag) | NAS host in config.json; token in Secret Service via `Secret` (libsecret) `password_store`/`password_lookup_sync` under `io.piercingxx.XXVitals` schema — never in config.json |
| Step baseline (SharedPrefs via `StepSampler.kt` store) | Last counter reading (3 numbers) | Same JSON config file, `step_baseline` object — not worth a table |
| `VitalsApplication.kt` providers | Process-wide singletons | Module-level lazy singletons constructed after `Gio.Application` startup, torn down on shutdown |
| Server Postgres (`server/db.py`, alembic 0001–0007) + `daily_merged` matview + `merge_rule.py` | System of record (unchanged) | Unchanged — the Linux client speaks the same protocol (§4). No server changes in this port |

### 1.4 Permissions and integrations

| Android | Linux |
|---|---|
| `INTERNET` + `ACCESS_NETWORK_STATE` (design D11: talks to exactly one user-configured host) | No manifest; same policy enforced in `sync/http.py`: HTTPS always allowed; cleartext http only when host:port matches a user-configured host (port of `CleartextGuardInterceptor.kt`); otherwise fail fast |
| `ACTIVITY_RECOGNITION` + `android.hardware.sensor.stepcounter required=false` | No equivalent permission; sensor presence probed at runtime via iio-sensor-proxy D-Bus (§5) |
| `allowBackup=false` (Room replica excluded from cloud backup) | Default posture kept: DB lives in `~/.local/share` (not backed by the shell's backup unless the user opts in); document exclusion in `docs/` |
| Takeout importer (`server/importer/parse.py`, `ingest.py`, Takeout notifier `takeout_notifier/`) | Server-side, unchanged. Linux client only needs the import path (§6): user drops archive where the existing server flow expects it, or a future lane adds a client "Import Takeout" picker via the FileChooser portal |
| Fabric auth (`server/auth.py`, `fabric_auth.py`, `session_keys.py`; client `FabricLoginClient.kt`) | Same protocol: login through xx-chat `POST /api/v1/fabric/login`, Bearer on every `/v1/*`, local validate-only. Client stores token in libsecret; no vendored keyring on the phone (validate-only lives server-side) |

## 2. XX-WM integration

XX-Vitals is a health app: it replaces no shell telephony surface and feeds no
caller-ID/SMS path. The integration points are:

- **Home slot**: the app appears in the drawer/home like any app via its
  `.desktop` id **`io.piercingxx.XXVitals`**. It does not claim a seeded
  Comms/Calendar-folder binding (those belong to Calls/Chatty/calendar apps
  per design.md); a user may pin it to any of the 8 home slots manually.
- **Notifications**: the app MUST NOT own `org.freedesktop.Notifications`
  (the shell's `notif_daemon.py` owns that name, backing off only for
  dunst/mako/fnott). The app is a pure *sender* via `Notify` (goal met,
  sync failed, storage full). It therefore never conflicts with the shell's
  mute list (`muted_apps`), DND, or shade — those keep working unchanged.
- **Ownership conflicts**: the shell defers to apps that own their function
  D-Bus name (as with `org.gnome.Calls` / `sm.puri.Chatty` in
  `modem_monitor.py`). XX-Vitals owns **`io.piercingxx.XXVitals`** (its
  `Adw.Application` id, D-Bus activatable) and nothing else on the session
  bus, so there is no conflict class: no ModemManager claim, no EDS claim,
  no notification-daemon claim. Background collection runs as a systemd user
  service with no bus name.
- **Theme/font match**: read the user's XX-WM theme from
  `~/.config/xx-wm/config.json` (`theme`, `custom_background`,
  `prefer_dark`, `font`) and apply it; watch the file for changes. Fall back
  to libadwaita dark/light otherwise. No theme broadcast needed.
- **Contacts/calendar/EDS**: not used by this app (no caller-ID surface, no
  calendar writes). Listed only to state the negative: no `EBook`/`ECal`
  dependency.

## 3. Pedometer strategy (Linux sensors differ)

Most Linux phones have no hardware step counter; the accelerometer is exposed
via **iio-sensor-proxy** (`net.hadess.SensorProxy` on the system bus,
`Accelerometer` property) or, where absent, raw `iio` sysfs nodes.

- **Lane 1 (this spec's scaffold + lane 2–3): honest degradation.** Ship the
  manual activity logging + daily goals first; steps show "not tracked" when
  no source exists — the same honest gap-day rule the Android app uses after
  Health Connect removal. Never synthesize steps.
- **Lane 4: battery-safe software pedometer (opt-in, off by default).**
  A `StepSampler` equivalent runs inside the `xx-vitals-collect` systemd
  service on a 15-minute timer tick, NOT a continuous poll: each tick takes a
  short (~10 s, 50 Hz) accelerometer burst via iio-sensor-proxy, runs a
  peak-detection stride filter with a per-device noise floor learned on first
  run, and attributes the burst-extrapolated delta with the same rebase rules
  as `StepReader.rebase` (cold start attributes nothing; reboot/suspend
  detected via monotonic-clock discontinuity → rebase, attribute nothing that
  tick). Continuous sampling is explicitly rejected: it costs ~5–10 % battery
  and duplicates what a watch does better.
- **Better source when present**: if the device exposes a hardware step
  counter (some Qualcomm SoCs via `step_counter` iio channel) or the user
  pairs a watch that syncs to the NAS, the collector prefers the cumulative
  counter with the Android rebase logic verbatim.
- **Derivation unchanged**: distance/calories/active-min/effort derive from
  steps + profile height + latest weight via the ported `Derive` functions,
  always flagged derived (`~` prefix, `derived_mask` bits), exactly as
  `StepMetricMapper.kt` / `core/Derive.kt` do.

## 4. Synology sync protocol (must stay compatible)

Base URL: user-configured host (no default), e.g. `https://nas:8443`.
Auth: `Authorization: Bearer <fabric token>` (xx-chat `POST
/api/v1/fabric/login`; client `FabricLoginClient.kt` → `sync/auth.py`).
Bearer on every `/v1/*`; 401/403 = re-login required (never silently
re-queued).

| Method + path | Body | Meaning (server file) |
|---|---|---|
| `POST /v1/weight` | `{measured_at: ISO, weight_kg}` | Insert manual sample; replay dedupes on UNIQUE(user, source, measured_at) → `rows_skipped` (`routes_write.py`) |
| `PATCH /v1/weight/{id}` | `{measured_at?, weight_kg?}` | Edit; server opens a NEW ingest_batch so the edit re-surfaces in delta pulls |
| `DELETE /v1/weight/{id}` | — | Idempotent delete (200-on-repeat = success) |
| `POST /v1/daily` | `{date, steps?, distance_m?, active_min?, effort_points?, calories_kcal?, derived_mask}` | Upsert phone daily row (`DailyEntryModel.KIND_DAILY`) |
| `POST /v1/activity` (see SyncApi; server sync/healthconnect family) | `{type, started_at, ended_at, distance_m?, calories_kcal?, effort_points?, active_min?, steps?}` | Insert manual session |
| `GET /v1/weight?from=&to=`, `GET /v1/activities?from=&to=` | inclusive dates, half-open `[from 00:00, to+1d 00:00)` | Range reads (`routes_read.py`) |
| `GET /v1/daily?from=&to=` | — | Merged view reads, never raw rows (`routes_daily.py`) |
| `GET /v1/changes?since=` | raw server `ingest_batch.started_at` ISO cursor | Delta pull; merge then advance cursor to the delta's max-batch stamp (`SyncEngine.pull/merge/watermark`) |
| `GET /v1/batches` | — | Ingest history for Profile + watermark lookup (`routes_batches.py`) |
| `GET /healthz` | — | Liveness only |

Wire rules the port must preserve: push in queue order, delete queue rows
only on 2xx; PATCH/DELETE resolve the server integer id via
`GET /v1/weight` ±1-day exact-timestamp match first; 507 pauses the drain;
unknown kinds / unparseable payloads dead-letter (visible queue depth, never
silent loss); `since` cursor is the raw server ISO string, never client
`now()` and never millis-truncated.

## 5. Data model + storage paths (XDG)

SQLite `~/.local/share/xx-vitals/vitals.db` (stdlib `sqlite3`), tables mirror
Room entities 1:1 (column names identical so the sync payload builders port
verbatim):

- `daily_metric(source_id, date, steps, distance_m, active_min, effort_points, calories_kcal, hr_avg/min/max, hr_sample_count, weight_avg/min/max_kg, derived_mask, batch_id)` — UNIQUE(source_id, date)
- `activity(source_id, type, started_at, ended_at, distance_m, calories_kcal, effort_points, active_min, hr_avg/max, steps, track_geojson, batch_id)` — UNIQUE(source_id, started_at, ended_at, type)
- `weight_sample(source_id, measured_at, weight_kg, batch_id)` — UNIQUE(source_id, measured_at)
- `pending_write(id, kind, payload_json, created_at, attempts, last_error)` — FIFO by `created_at`
- `sync_state(id=1, last_pull_at, last_push_at, hc_change_token→lastPullIso)`

Config `~/.config/xx-vitals/config.json`: `{server_url, allowed_cleartext_hosts, units, goals{steps,effort,weeklyEffort}, height_metres, birth_year, show_{journal,browse,profile}_tab, step_baseline{counter,elapsed_ms,wall_ms}, theme_override?}`.
Secrets (fabric token): libsecret `io.piercingxx.XXVitals` schema.
Logs: `~/.local/state/xx-vitals/xx-vitals.log` + in-memory ring.
Cache: `~/.cache/xx-vitals/` (chart render caches, none yet).

### Import path from Android

1. **Server-mediated (primary, no client work):** the Takeout importer and
   merge rule live server-side (`importer/parse.py`, `ingest.py`,
   `merge_rule.py`: MAX-never-SUM per day, ±120 s activity dedupe, per-row
   SAVEPOINT dedupe). Any client — Android or Linux — that syncs after an
   import receives the backfill through the normal `GET /v1/changes` delta.
   The Linux port gets pre-2026 history for free by syncing.
2. **Direct device export (later lane, optional):** the Android app produces
   no self-export file today; if one is added, accept its JSON/CSV via the
   FileChooser portal and feed it through the same validation as manual entry
   (never raw-SQL import). Standard fallbacks already covered by the server
   importer: Takeout `Daily activity metrics.csv` (header-driven parse),
   `Activities/*.tcx`, `raw_com.google.weight_*.json`.

## 6. Architecture

Packages (`src/xx_vitals/`):

- `core/` — pure logic, no GI, no I/O at import: `metrics.py` (port of
  `:core` MetricMath/GoalModel/Derive/UnitConvert), `journal.py` (port of
  `JournalModel`), `format.py` (port of `UnitFormatter`), `applog.py`
- `store/db.py` — sqlite3 schema + DAO functions (one function per DAO
  method; connection per call, WAL mode)
- `store/prefs.py` — config.json load/save (atomic), defaults (imperial,
  10000/90/150 goals, all tabs visible)
- `sync/` — `api.py` (SyncApi interface + urllib implementation),
  `engine.py` (push/pull/merge/watermark state machine), `auth.py` (fabric
  login POST, token → libsecret), `http.py` (cleartext guard)
- `collect/` — `sampler.py` (pure rebase + burst step detection),
  `service.py` (`xx-vitals-collect` one-shot entry: sample → upsert →
  enqueue daily → exit)
- `ui/` — `app.py` (Adw.Application), `home.py`, `journal.py`,
  `browse.py`, `profile.py`, `entry.py`, `theme_apply.py`
- `theme.py` — XX-WM theme preset table (copied values from
  `ref/xx-wm/launcher/src/config.py`), `load_xx_wm_config()`,
  file-monitor hookup

Threading: never block the GTK main loop. Sync/collect run in worker threads
(`threading.Thread` + `GLib.idle_add` for result delivery) or as separate
one-shot processes (collector); no `subprocess.run`, no `time.sleep`, no
blocking Gio calls on the main thread. HTTP uses `urllib` with timeouts in
worker threads only.

D-Bus: app owns `io.piercingxx.XXVitals` (application id, D-Bus activatable
via the `.desktop` `DBusActivatable=true`). Collector owns no name.
`Gio.FileMonitor` on `~/.config/xx-wm/config.json` replaces the Android
theme broadcast receiver.

Install (meson, mirroring XX-WM's `launcher/meson.build`): Python sources →
`datadir/xx-vitals`, launcher `xx-vitals` → `bindir`, `.desktop`
(`io.piercingxx.XXVitals.desktop`) → `applications`, metainfo →
`metainfo`, `xx-vitals-collect` → `bindir`,
`xx-vitals-collect.service` + `xx-vitals-sync.service/.timer` → systemd
user dir. Autostart: timers + `Graphical-session` wanted-by, enabled
explicitly by the user (never auto-enabled at install).

## 7. Milestone plan (build lanes, each ≤ ~1500 lines + acceptance tests)

1. **Core data layer** — `core/metrics.py`, `store/db.py`, `store/prefs.py`
   ports of `:core` + Room schema. Tests: unit parity with
   `core/src/test` + `DailyEntryModelTest`/`WeightRepositoryTest`
   equivalents; schema UNIQUE-dedupe tests. (~1000 lines)
2. **Manual weight + activity flows** — `ui/entry.py` dialogs, queue
   enqueue, offline-first round trip into local DB. Tests: headless model
   tests (`WeightEntryModelTest`, `ActivityEntryModelTest` ports) + queue
   order test. (~1200 lines)
3. **Home/Journal/Browse/Profile UI** — the four tabs, rings, gap-day
   rendering, "not tracked" placeholders, responsive 320–360 px layout.
   Tests: `HomeModelTest`, `JournalModelTest`, `MetricDetailModelTest`,
   `ProfileEditModelTest` ports; GTK widget tests under a virtual display
   in CI only, never in the headless gate. (~1500 lines)
4. **Pedometer + collector service** — `collect/` sampler with burst
   detection, `xx-vitals-collect.service`, honest no-source UI copy.
   Tests: rebase unit tests (`StepReaderTest`, `StepSamplerTest` ports),
   service one-shot test with fake sensor. (~1300 lines)
5. **Sync engine + Profile sync UI** — `sync/` engine, auth, cleartext
   guard, batches view, "Sync now", 401/507 surfacing. Tests:
   `SyncEnginePushTest`/`PullTest`/`SyncEndToEndTest` ports against a local
   stub HTTP server + `CleartextPolicyTest` port. (~1500 lines)
6. **Polish** — theme hot-reload, logs view, metainfo/screenshots, Takeout
   file-picker import, backup exclusion docs. Tests: theme/file-monitor
   test, log-ring test. (~800 lines)

## Review notes (orchestrator, 2026-10-15) — these override the text above

1. `Adw.PreferencesWindow` is deprecated; use `Adw.PreferencesDialog`.
2. The collector service is a systemd user unit (pmOS v26.06 runs systemd); also ship an OpenRC-free fallback note: if `systemctl --user` is unavailable, the GUI runs the same tick in-process while open (documented degradation).
3. The NAS bearer ("fabric") token and any login credential must be stored with libsecret (Secret Service; attributes {application: io.piercingxx.XXVitals, host: <nas host>}), never in config.json or the SQLite file. libsecret `*_sync` calls only from worker threads. Tests must never touch a real keyring or session bus (isolate with a fake backend / unreachable `DBUS_SESSION_BUS_ADDRESS`).
