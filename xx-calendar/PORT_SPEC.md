# XX-Calendar — Linux Phone Port Spec

Port of the PiercingXX Android app `xx-calendar` (Kotlin, GrapheneOS-targeted,
`ref/xx-calendar/`) to postmarketOS / Mobian / FuriOS / PureOS phones running
phoc (XX-WM or Phosh). Stack: Python 3.12+, GTK4 + libadwaita via PyGObject,
meson install, stdlib + GI libraries only.

This lane produces the SPEC and a runnable SCAFFOLD only. Later lanes build
features from this spec. The scaffold in `src/xx_calendar/` already applies the
XX-WM theme and runs headless tests; every lane below extends it.

Source conventions followed: `ref/xx-wm/design.md` (UI contract),
`ref/xx-wm/docs/config.md` (config API), `ref/xx-wm/launcher/src/config.py`
(`THEME_PRESETS` / `FONT_FAMILIES`), and the ModemManager/D-Bus patterns in
`ref/xx-wm/launcher/src/modem_monitor.py`, `contacts.py`, `notif_daemon.py`.

## 0. Target platform and hard constraints

- 360x720 logical px, minimum 320x640. Touch-first, on-screen keyboard
  (squeekboard / GNOME OSK). No horizontal overflow; long labels ellipsize
  or wrap.
- phoc 0.56 / wlroots 0.19: layer-shell v3 only where used; this app is a
  normal toplevel app, so no layer-shell code is needed.
- Never block the GTK main loop: EDS queries and D-Bus calls go over async
  Gio calls or a worker thread posting back with `GLib.idle_add`. No
  `subprocess.run`, no `time.sleep`, no `call_sync` with long timeouts on
  the main thread (per `XXWM_RULES.md`).
- Text-first PiercingXX design: no icon grids, monochrome per theme, every
  color from the active `ThemePreset`. The app reuses the user's XX-WM theme
  from `~/.config/xx-wm/config.json` when present and falls back to
  libadwaita dark/light otherwise. Same preset keys and font families as
  `ref/xx-wm/launcher/src/config.py`.
- No pip dependencies. GI libraries allowed: `Gtk`, `Adw`, `Gio`, `GLib`,
  `EBook`/`EDataServer`/`ECal` (EDS), `Secret` (libsecret). `GWeather`,
  `Geoclue`, `Gst` are permitted but this app needs none of them.
- App id: `io.piercingxx.XXCalendar`. D-Bus name: same.

## 1. Feature inventory of the Android app

Every item cites its Kotlin source. "Linux mapping" in section 2 refers back
to these IDs (F1..F12, S1.., B1..).

### F1. Four calendar views (Schedule / Day / Week / Month)

- `MainActivity.kt` — app chrome: top bar with tappable month/year opening a
  mini-month picker, Today button shown only when off today, one FAB with one
  action (new event), overflow menu for view switching / calendars / settings.
  No bottom navigation, no swipe-open drawer. Navigation via Jetpack
  Navigation routes (`schedule`, `day`, `week`, `month`, `detail/{id}`,
  `editor/...`).
- `ui/schedule/ScheduleScreen.kt` + `ScheduleWindowState.kt` — agenda list,
  the DEFAULT view (D10: answers "what is next").
- `ui/day/DayScreen.kt` + `DayWindowState.kt` + `TimeGrid.kt` + `GridModel.kt`
  — scrolling time grid.
- `ui/week/WeekScreen.kt` + `WeekWindowState.kt` — 7-column grid.
- `ui/month/MonthScreen.kt` + `MonthGrid.kt` + `DayPeek.kt` — month grid with
  peek into a day's events.
- `ui/gesture/SwipeNavigate.kt` — horizontal swipe moves the visible
  day/week/month window; a held drag is reserved for grid create/move/resize.
- `ui/day/GridWrites.kt` — drag-to-move/resize writes from the day grid.
- Day/week grids support tap-empty-cell create and drag gestures;
  `MainActivity.kt` (15.7) also handles `.ics` VIEW intents and
  `content://com.android.calendar/time/<millis>` deep links.

### F2. Event editor (create / edit / duplicate, recurrence scopes)

- `EditorActivity.kt` (manifest INSERT of `vnd.android.cursor.dir/event`,
  VIEW/EDIT item) + `ui/editor/EditorScreen.kt` + `EditorState.kt` —
  fields: title, all-day flag, start/end date+time, timezone, RRULE,
  calendar, location, description, reminder minutes, busy/free.
- `ui/editor/RepeatBuilder.kt` — recurrence rule builder UI.
- `ui/editor/ScopePrompt.kt` — This instance / This and following / All
  prompt for recurring edits.
- `core/ScopeResolver.kt` — pure scope resolution (`ThisInstance`,
  `ThisAndFollowing`, `AllEvents` → `UpdateParentRow`, `InsertExceptionRow`,
  `SplitParent`, `DeleteParentRow`, `SetUntil`, `DeleteInstanceUri`,
  `Refusal`).
- `calendar/RecurrenceEditor.kt` — executes resolutions against the provider.
- `core/RRuleModel.kt` — RFC 5545 RRULE build/parse/serialize round-trip.
- `core/TimeMath.kt` — all-day UTC-midnight conversion, DST handling,
  per-event timezone rendering.
- Detail sheet: `ui/detail/DetailSheet.kt` — read view, edit/duplicate
  entry points, scoped delete with undo snackbar, conferencing URL rendered
  as tappable text, attachment count only.

### F3. Provider layer (system of record, no local event copy)

- `calendar/CalendarRepository.kt` — the whole provider surface (D1):
  `queryInstances(range)`, `loadEvent`/`saveEvent`/`deleteEvent`,
  `calendars()`, `reminders()`, `changes` flow, `lastProviderChange()`.
  Always a normal client, never `CALLER_IS_SYNCADAPTER`, so writes are
  marked dirty for the sync adapter (design §4.1).
- `calendar/InstanceQuery.kt` — `Instances` expansion queries over the
  visible window plus margin, off the main thread.
- `calendar/CursorRead.kt`, `calendar/Models.kt` (`CalendarSummary`,
  `CalendarInstance`, `EventReminder`, `EventDraft`, `LoadedEvent`).
- `calendar/OpaqueColumns.kt` — D8: unmodeled columns read on load, held
  untouched, written back verbatim; modeled whitelist is exactly design §6.2.
- `calendar/AutoAddedDetector.kt` — render-time hiding of Gmail auto-added
  events (source-calendar + metadata stages, fail-closed).
- `calendar/LocalCalendarBootstrap.kt` — creates one `ACCOUNT_TYPE_LOCAL`
  calendar on first run when no writable calendar exists (§4.4).
- `core/AgendaGrouping.kt` — day boundaries, multi-day span, all-day
  placement for the agenda list.

### F4. Reminders (the one hard reliability requirement, R7)

- `alarm/ReminderPlanner.kt` — pure desired-set computation over the next
  48h (`AlarmKey(eventId, instanceStart, triggerAt)`, `Plan(toSet, toCancel)`).
  Quiet-biased: declined instances never alarm; nothing without a reminder
  alarms; all-day anchor configurable (default 18:00 day-before).
- `alarm/ReminderReconciler.kt` — recompute-diff-apply loop, single-flight
  mutex. Triggers: boot, time/timezone/package-replaced broadcasts, provider
  change (2s debounce), daily 03:00 heartbeat, app start.
- `alarm/AlarmScheduler.kt` — `setExactAndAllowWhileIdle`, inexact fallback
  with logging when the grant is missing.
- `alarm/BootReceiver.kt`, `alarm/ReminderReceiver.kt`,
  `alarm/ProviderObserver.kt`, `alarm/HeartbeatClock.kt` (03:00 math),
  `alarm/AlarmKeys.kt`, `alarm/ScheduledAlarmRegistry.kt`.
- Exact-alarm permission surfaced in Settings as the single warn-colored row.

### F5. Settings (sixteen survivors + filter-fidelity switch)

- `settings/SettingsStore.kt` — DataStore `settings`:
  `default_view` (also = last view; the top-bar switcher writes it),
  `start_day_of_week`, `week_numbers`, `show_declined` (off),
  `dim_past` (on), `density`, `default_duration_min` (30),
  `default_notification_min` (10), `all_day_notify_hour/days_before`
  (18:00, day before), `lock_screen_title` (off), `daily_agenda` (off),
  `heads_up` (off), `hide_auto_added` (on), `background`, `font`,
  `text_size_scale`, `auto_added_filter_mode` (`OFF/CALENDAR/METADATA`).
- `ui/settings/SettingsScreen.kt` + `CalendarsScreen.kt` (drawer calendar
  list with visibility toggles) — sections VIEW / EVENTS / NOTIFICATIONS /
  CALENDARS / APPEARANCE / SYNC / DATA, plus a logs row.
- `settings/SigilStore.kt` — calendar→sigil map persisted as
  `calendar:<id>:<accountName>` → tier ordinal.
- `core/SigilAssigner.kt` + `CalendarKey` — stable sigil allocation, lowest
  free tier first, overrides win, ceiling behavior past the tier count
  (todo S1 collapses six tiers to four perceptual tiers).
- SYNC section: "last change X ago" + one DAVx⁵ row (deep-link to DAVx⁵,
  shown once, never a nag).

### F6. Backup / import / export

- `settings/BackupJson.kt` — deterministic hand-rolled JSON, `app:
  "xx-calendar"`, `version: 1`: the DataStore settings (key names are the
  preference names, doubling as documentation) plus the sigil map as
  `{calendarId, accountName, tier}` rows with tier ordinals. Total reader:
  missing keys fall back, unknown keys ignored, corrupt input returns
  `Corrupt`, never a half-applied restore. Restore is wholesale replacement.
- `settings/IcsCodec.kt` — pure-JVM RFC 5545 subset: export UID, DTSTAMP,
  DTSTART/DTEND (date and datetime forms), DURATION, SUMMARY/LOCATION/
  DESCRIPTION, RRULE (canonicalised via `RRuleModel`, unmodelled rules pass
  through verbatim), RDATE/EXDATE passthrough, STATUS, TRANSP, VALARM
  (ACTION:DISPLAY) from reminder minutes. `PRODID =
  "-//PiercingXX//XX-Calendar//EN"`, synthetic UID
  `<eventId>-<calendarId>@xx-calendar`, real `UID_2445` preferred when the
  row carries one. No VTIMEZONE emission (TZID references IANA ids); no
  attendees/organizer/categories/attachments. Exception rows (`ORIGINAL_ID`
  set) not modelled. Import dedupes by UID; canceled occurrences skipped.
- Settings DATA section + `MainActivity.kt` 15.7 pipeline: SAF file picker,
  bounded read (`MAX_IMPORT_BYTES`), target-calendar picker sheet, toast
  result counts (`N imported · N duplicates skipped · N canceled skipped`).

### F7. App widgets (month grid + schedule)

- `widget/MonthWidget.kt` — static current-month 7xN text grid: day numerals,
  today inverted, event days reduced to `+N`. System monospace (Glance cannot
  load JetBrains Mono).
- `widget/ScheduleWidget.kt` — next-events agenda widget.
- `widget/WidgetSupport.kt`, `widget/WidgetPalette.kt` — query + ground
  helpers; `widget/ThemeSyncReceiver.kt` — family theme broadcast handling.
- `res/xml/widget_month_info.xml`, `widget_schedule_info.xml` — widget
  provider declarations; `widget_loading.xml` placeholder layout.

### F8. Theme system (family-synced, no in-app picker)

- `ui/theme/ThemePreset.kt` — seven named presets (AMOLED Night default,
  Graphite, Forest Night, Ocean Drift, Burgundy, Paper, Mist), pure Kotlin,
  matched by display name from the launcher broadcast.
- `ui/theme/Theme.kt`, `Color.kt`, `Type.kt` — Compose theme, ink ramp,
  JetBrains Mono / Space Mono (`res/font/jetbrains_mono.xml`,
  `space_mono.xml`).
- `ui/theme/ThemeGroundState.kt` — re-reads the broadcast-persisted ground
  on every `onResume` so the app follows the launcher with no picker.
- Identity is carried by sigil + opacity tier (D6: `▌ ▏ ░ ▒ ▓ ·`,
  collapsing to four perceptual tiers per todo S1); signal white reserved
  for the now-rule and selection.

### F9. Launcher shortcuts and deep links

- `res/xml/shortcuts.xml` — static shortcuts: New event (INSERT intent to
  `EditorActivity`), Today (VIEW `content://com.android.calendar/time` to
  `MainActivity`, lands on schedule-today).
- `MainActivity.kt` `parseDeepLink`: `VIEW content://com.android.calendar/time`
  (`/time/<millis>` and `/time/epoch/<millis>`) and `VIEW text/calendar`
  (imports the handed-over `.ics` in place). Unparseable links open Schedule
  normally, never crash. Links are consumed below the permission gate.

### F10. Permissions (machine-checkable privacy posture)

- `AndroidManifest.xml` declares exactly: `READ_CALENDAR`,
  `WRITE_CALENDAR`, `POST_NOTIFICATIONS`, `RECEIVE_BOOT_COMPLETED`,
  `USE_EXACT_ALARM`, `SCHEDULE_EXACT_ALARM`, plus the family
  `com.piercingxx.xxlauncher.permission.THEME_SYNC`. **No `INTERNET`** —
  enforced by a pre-push hook running `aapt2 dump permissions` (R3/D3).
- First-run permission gate in `MainActivity.kt`: one explanatory screen,
  one grant button; permanent denial offers an exit into system app settings.
- `CalendarApp.kt` — application class wiring.

### F11. Background behaviors

- `BootReceiver` (BOOT_COMPLETED / LOCKED_BOOT_COMPLETED) → reconcile.
- `ProviderObserver` (ContentObserver, 2s debounce) → reconcile.
- Daily 03:00 inexact heartbeat (`HeartbeatClock`) → reconcile.
- TIME_SET / TIMEZONE_CHANGED / MY_PACKAGE_REPLACED → reconcile.
- Widget refresh on provider change.
- Notification presentation: local notifications with bundled chime;
  content preview off, heads-up off, declined hidden, daily agenda off
  (all D12 quiet defaults, each one toggle away).

### F12. Logging and diagnostics

- `log/AppLog.kt` — process-wide logger: logcat + rotating file under
  `filesDir/logs` + in-memory ring. On-phone only, no network; no message
  bodies, numbers, keystrokes, or mail bodies in log lines.
- `log/LogsUi.kt` + Settings "logs" row — copy/share the ring after a crash.

### Non-goals carried over from the Android app

Teardown §§3.4/3.7 and design non-goals, all resolved: guests, invitations,
RSVP, scheduling, tasks, Goals, Google Reminders entity, conferencing
generation, location autocomplete, title suggestion, natural-language
parsing, tablet/landscape two-pane layouts, Wear OS, print, telemetry and
crash reporting. Search-in-app renders inert this wave (`MainActivity.kt`
search icon is decorative). `.ics` URL subscription and multi-account
switching are deferred. These stay out on Linux unless this spec re-opens
them (it does not).

## 2. Linux implementation per feature

General rules: EDS (Evolution Data Server) is the system of record wherever
`CalendarContract` was. The app is a normal EDS client and never touches
sync internals. All EDS and D-Bus I/O runs off the GTK main loop (async
Gio or a worker thread + `GLib.idle_add`). The pure-logic `core/` ports
(`RRuleModel`, scope resolution, `TimeMath`, `SigilAssigner`,
`AgendaGrouping`, planner math, heartbeat math) stay GUI-free and carry the
unit tests, exactly as the Android tree defends that boundary.

### F1 views → GTK4 + libadwaita

- `Adw.Application` + `Adw.NavigationView`: Schedule / Day / Week / Month
  pages, detail page, editor page, settings page. Schedule is the default
  page (D10). One `+` button with one action (D11).
- Horizontal swipe between windows via `Gtk.GestureSwipe`, reusing the
  `SwipeNavigate` direction rule (mostly-horizontal flick past a threshold;
  held drags stay reserved for grid gestures). Window state holders
  (`ScheduleWindowState`, `DayWindowState`, `WeekWindowState`, month pager
  generation-stamped jump) port as plain Python classes so the top bar,
  Today button, and mini-month picker can jump every view.
- Day/Week time grids: custom draw (`Gtk.DrawingArea`) or a `Gtk.ColumnView`
  grid; drag-to-move/resize ports `GridWrites.kt`. Tap-empty-cell create
  ports the month double-tap / peek-create behavior.
- Deep links: the `.desktop` file exposes `MimeType=text/calendar;` (F9
  `.ics` handler) and `DBusActivatable=true`; a `content://.../time` analog
  is a `--date=YYYY-MM-DD` command-line option plus `Open` D-Bus method
  parameters. Unparseable input opens Schedule, never crashes.
- Search stays inert (out of scope, as on Android).

### F2 editor → EDS writes with opaque-field preservation

- `ECal.Client` create/modify/remove. The app models exactly the Android
  §6.2 column set, mapped to `icalcomponent` properties
  (SUMMARY, DTSTART/DTEND/DURATION, TRANSP, LOCATION, DESCRIPTION, RRULE,
  RDATE, EXDATE, STATUS, VALARM from reminder minutes, per-calendar
  identity). Everything else on the `VEVENT` passes through untouched (D8):
  read the full component on load, hold it in editor state, write back the
  unmodeled properties verbatim on save.
- Recurring scopes port `ScopeResolver` one-to-one: This instance → insert
  exception (`RECURRENCE-ID`); This and following → split (UNTIL on the
  parent + new recurring component carrying later RDATE/EXDATE entries,
  re-pointed exceptions); All → modify/remove the parent. Unmodelled rule
  shapes refuse the write with a plain reason, never guess (same failure
  table as Android §10).
- Editor stays open with the edit intact when a write fails (`✗` message;
  never discard typing).
- No `INTERNET`-equivalent concern: the app process itself needs no network
  permission concept on Linux, but the layering holds — the app never speaks
  CalDAV/HTTP itself (see §6); only EDS does. Conferencing links render as
  text, never generated; attachments shown as a count, never opened.

### F3 provider → EDS (EClient / ECal)

- `CalendarRepository` becomes an `ECal.Client` wrapper per EDS source:
  calendar list (sources + colors + visibility), instance expansion over
  `[start, end)` (EDS expands recurrence like `Instances` did), reminders
  (VALARM minutes), one `last_source_change` timestamp for the SYNC row.
- Change observation: EDS `SourceRegistry` / client change signals replace
  `ContentObserver`; every view re-queries its window on change.
- `AutoAddedDetector` ports as a render-time filter with the same two-stage
  `OFF/CALENDAR/METADATA` switch and fail-closed rule. Linux note: with a
  self-hosted CalDAV origin (Synology/xx-drive hub, §6) there is no Gmail
  injection at all, so the filter mostly idles; it still applies to any
  Google-backed source the user adds.
- First-run bootstrap ports `LocalCalendarBootstrap`: if no writable EDS
  calendar source exists, create one local "On This Phone" source so a fresh
  install is immediately usable, with no "not connected" nag.
- What is NOT portable: `CALLER_IS_SYNCADAPTER` / dirty-flag mechanics and
  `SYNC_DATA*` columns have no EDS-client equivalent and are not needed —
  EDS owns the sync contract with the CalDAV server internally. The app is a
  plain client on both platforms; that is the part that ports.

### F4 reminders → the app's own background service (decision)

DECISION: reminders are scheduled by the app's own background service,
which posts `org.freedesktop.Notifications`, and NOT by EDS alarm notify
(`evolution-alarm-notify`).

Justification:

1. Parity with the proven Android design (§4.3, D7): reconcile-not-chase
   converges after any missed signal (reboot, Doze-equivalent suspend,
   daemon restart). The `ReminderPlanner` desired-set math ports verbatim
   as GUI-free Python and keeps its unit tests.
2. Quiet defaults are app semantics EDS cannot express: declined instances
   never alarm, no implicit defaults for all-day events, 18:00 day-before
   all-day anchor, preview/heads-up off. `evolution-alarm-notify` fires on
   raw VALARMs with none of this filtering.
3. No dependence on `evolution-alarm-notify` being installed and running.
   Minimal phone images (postmarketOS/pmOS-tweaks, FuriOS) may not ship or
   autostart it; the app must remind with only its own service present.
4. The service still writes standard VALARM (ACTION:DISPLAY) rows into EDS,
   so GNOME Calendar and any EDS consumer interoperate at the data level.

Coexistence: if `evolution-alarm-notify` also runs, both would post for the
same VALARM. Mitigation, in order: (a) the app's service is the documented
owner of reminders — the spec recommends deactivating the
`evolution-alarm-notify` autostart entry on images where the app owns
reminders; (b) a Settings toggle "let the system alarm daemon also notify"
(default off) with the double-fire consequence stated; (c) the app never
deduplicates another daemon's notifications (no reliable cross-daemon id).
This is a documented ops step, not code.

Linux mechanism mapping: `AlarmManager.setExactAndAllowWhileIdle` has no
exact analog — use a `systemd --user` timer/service (or XDG autostart entry
where systemd is absent; pmOS default images are OpenRC, so ship both like
XX-WM ships systemd + OpenRC units) plus wake-friendly scheduling; exact
timing is best-effort after suspend, stated honestly in Settings like the
Android exact-alarm warn row. The 03:00 heartbeat, 48h reconcile window,
provider-change debounce, and timezone-change triggers port unchanged.

### F5 settings → XDG JSON + libadwaita preferences

- `SettingsStore` (all sixteen keys + filter-fidelity switch, same names,
  same quiet defaults) becomes `~/.config/xx-calendar/settings.json` with
  the same total-reader rule: unknown keys ignored, bad values fall back,
  corrupt file keeps last-good in memory and never crashes launch.
- Appearance keys (`background`, `font`, `text_size_scale`) become
  follow-the-shell: the app reads `~/.config/xx-wm/config.json` (`theme`,
  `font`, `text_size_scale`, `prefer_dark`) when present and tracks it via
  file monitoring (hot-reload like the shell). There is no in-app theme
  picker — same as Android's no-picker rule. The key→preset tables are the
  eight `THEME_PRESETS` and four `FONT_FAMILIES` from
  `ref/xx-wm/launcher/src/config.py`, reused verbatim.
- SYNC section: "last change X ago" from EDS + one row naming the CalDAV
  origin (Synology / hub URL, §6) with an opener for the account setup,
  shown once, never a nag. The Android DAVx⁵ deep-link becomes a button
  opening GNOME Settings Online Accounts (or the EDS source setup), hidden
  when not applicable.
- Sigils persist like `SigilStore` (`calendar:<id>:<account>` → tier) and
  the four-tier collapse (todo S1) is applied on Linux from the start.
- Exact-alarm warn row becomes a background-service-health row, shown only
  when the reminder service is unreachable, with a fix action.

### F6 backup/import/export → same formats, XDG pickers

- `BackupJson` v1 format is kept byte-compatible: the Linux client reads
  Android backup files (settings key names are identical; sigil rows carry
  `{calendarId, accountName, tier}` ordinals) and writes the same shape, so
  a user moving from the Pixel restores settings + sigils in one file.
  Restore stays wholesale replacement with the consequence surfaced first.
- `IcsCodec` subset ports as a GUI-free Python module with the same
  PRODID, UID rules (`UID_2445`-equivalent preferred, synthetic
  `<eventId>-<calendarId>@xx-calendar` fallback), UID dedupe on import,
  canceled-skipped counts, and the same honest subset limits (no VTIMEZONE
  emission, no attendees/organizer/attachments).
- SAF picker → `Gtk.FileDialog` (never `FileChooserDialog.get_filename()`
  / `dialog.run()`, per `XXWM_RULES.md`) and XDG portal file access;
  bounded import size (`MAX_IMPORT_BYTES` equivalent); target-calendar
  picker before insert; result counts in one message.

### F7 widgets → XX-WM surfaces, not app widgets

NOT portable as widgets: Glance/ RemoteViews home-screen widgets have no
Linux equivalent the app can ship. Closest equivalents, owned by the shell
with data from EDS:

- Month widget → the shade's inline month calendar
  (`notification_shade.py` + `calendar_grid.py`): event-day dots/`+N`
  counts fed from EDS (see §4), tap opens `io.piercingxx.XXCalendar`.
- Schedule widget → the XX-WM home "Calendar" slot content / date-widget
  tap action opening the app.
- Theme following: the app reads the shell config directly (§2/F5), which
  is stronger than the Android broadcast — no receiver needed.

### F8 theme → follow-the-shell (no picker, no broadcast)

- Android's launcher-broadcast sync becomes a file read:
  `~/.config/xx-wm/config.json` (`theme`, `custom_background`,
  `prefer_dark`, `font`, `custom_font_family`, `text_size_scale`) monitored
  for hot-reload. Absent → libadwaita dark/light via `prefer_dark`.
- Palette: the eight XX-WM presets reused by key (`amoled` default,
  `graphite`, `forest`, `ocean`, `paper`, `mist`, `aura`, `burgundy`,
  `custom` derived). The Android app's seven presets map onto these
  (AMOLED Night→`amoled`, Graphite→`graphite`, Forest Night→`forest`,
  Ocean Drift→`ocean`, Burgundy→`burgundy`, Paper→`paper`, Mist→`mist`;
  `aura`/`custom` are new on Linux and fall back sensibly).
- Fonts: same four families; widgets' system-monospace limitation is moot
  (no Glance on Linux — full font stack everywhere).
- Sigil + opacity tier identity ports unchanged (monochrome per theme, no
  hue, signal white reserved for now/selection).

### F9 shortcuts/deep links → .desktop actions + URI handling

- Static shortcuts → `.desktop` Actions: `NewEvent` (opens the editor page),
  `Today` (opens Schedule on today).
- `.ics` VIEW → `MimeType=text/calendar;` + import pipeline in place.
- `content://.../time/<millis>` → `--date=` CLI option and D-Bus `Open`
  method; malformed input opens Schedule.

### F10 permissions → Linux equivalents

| Android permission | Linux equivalent |
|---|---|
| `READ/WRITE_CALENDAR` | EDS source access (session bus, no prompt); first-run gate explains + creates local source if none |
| `POST_NOTIFICATIONS` | `org.freedesktop.Notifications` via the shell daemon (`notif_daemon.py` owns the name; the app only posts — no conflict) |
| `RECEIVE_BOOT_COMPLETED` | `systemd --user` service + XDG autostart for the reminder service |
| `USE_EXACT_ALARM` / `SCHEDULE_EXACT_ALARM` | best-effort scheduling; health row in Settings when the service is down |
| `THEME_SYNC` family broadcast | direct read of `~/.config/xx-wm/config.json` (stronger, no IPC) |

The no-`INTERNET` posture ports as architecture, not manifest: the app
process opens no sockets itself — CalDAV/HTTP belongs to EDS (and secret
storage to libsecret). A lane test asserts no `socket`/`urllib`/`http`
imports in `src/xx_calendar/` outside an explicitly justified sync module
(which this spec does not create — see §6).

### F11 background → systemd user service + autostart

All five trigger classes port: boot (service start), source change (EDS
signals, debounced), 03:00 heartbeat (timer), timezone/clock change
(timedate1 monitoring + EDS signals), package update (reconcile on service
start covers it). Notification presentation keeps the D12 quiet defaults.

### F12 logging → XDG + journal

`AppLog` becomes a rotating log under
`~/.local/share/xx-calendar/logs/` plus stderr (journal capture) and an
in-memory ring behind the Settings "logs" row (copy/share). Same privacy
rule: no bodies, numbers, keystrokes, or descriptions in log lines.

## 3. Integration with XX-WM

### 3.1 What the app feeds and what it replaces

- Home "Calendar" slot: `default_layout.py` resolves the slot from
  `_CALENDAR_CANDIDATES` (`org.gnome.Calendar`, `gnome-calendar`, ...).
  Lane work prepends `io.piercingxx.XXCalendar` to that candidate list so a
  phone with the app installed seeds and resolves the Calendar slot to it.
  GNOME Calendar remains the fallback when the app is absent.
- Shade month calendar: `notification_shade.py` draws an event-less month
  grid from `calendar_grid.py`; tapping the date header toggles it. Lane
  work: (a) tapping a day with events offers "open in XX-Calendar", and
  (b) event-day dots/`+N` counts are fed from EDS (shared query helper —
  see §5 — so the shell and the app never run divergent expansions).
  The shade grid itself stays event-light by design; full fidelity lives
  in-app, mirroring how the Android widget was deliberately reduced.
- Notifications: the app is a pure *client* of
  `org.freedesktop.Notifications`, which the shell's `notif_daemon.py`
  owns (backing off only if another daemon owns the name). The app never
  owns that name, so there is no conflict and no shell change needed. DnD,
  per-app mutes (`muted_apps`), and lock-screen modes keep applying to the
  app's reminders with zero app code.
- Caller ID / SMS / dialer: the calendar app places no calls and sends no
  SMS, so it touches nothing in `modem_monitor.py`, `dialer.py`, `sms.py`,
  or `contacts.py`. It does not feed caller-ID lookup (event attendees are
  not rendered at all, same as Android) and it does not replace the shell's
  built-in dialer/SMS fallbacks. Stated explicitly so later lanes do not
  invent a coupling.

### 3.2 Ownership conflicts and how they are avoided

The shell defers when the app owns its D-Bus name. Concretely:

- `io.piercingxx.XXCalendar` (activatable application + `Open` method):
  the shell launches/activates it and never duplicates its windows.
- Reminder ownership: the app's background service owns posting its own
  reminders; the shell only renders what arrives over
  `org.freedesktop.Notifications`. No OVERRIDES, no name-taking on the
  notification bus.
- Calendar data: single-writer per row at the EDS layer (the app's own
  saves); the shell only *reads* EDS for dots/counts, never writes events.
- The `.desktop` id `io.piercingxx.XXCalendar` is the stable key the shell
  uses for the home slot, drawer labels (`app_labels`), mutes, and focus
  mode. It must never change once shipped.

## 4. Data model and storage paths (Revision 2: the app owns its store)

§2/F3's "EDS is the system of record" mapping is superseded: EDS is an
import/publish peer only (§4.4). The Android parity material in §1–§3 still
describes what the app does; this section describes where it keeps it.

### 4.1 What lives where

| Data | Home |
|---|---|
| Events, recurrence (RRULE/RDATE/EXDATE), exceptions (RECURRENCE-ID), reminders (VALARM), attendees | Own SQLite DB `~/.local/share/xx-calendar/calendar.db` (0600 file, 0700 dirs, WAL, `busy_timeout`) — the system of record |
| Calendar list (name, color, visibility, sigil tier) | Same DB, `calendars` table |
| Raw VEVENT text (lossless, incl. unknown X- props) | Same DB, `events.ics`, plus indexed columns: uid, calendar_id, dtstart/dtend UTC int64, original TZID, all-day flag, rrule text, recurrence-id, status, revision |
| Change feed (`List` since a revision) | Same DB: global `revision` counter + tombstones |
| Settings (§2/F5, same key names) | `~/.config/xx-calendar/settings.json` |
| Rotating logs + shareable ring dump | `~/.local/share/xx-calendar/logs/` |
| Exported `.ics` / backup JSON (user-chosen) | XDG Documents (via portal picker), never app-private |
| CalDAV passwords / tokens | Secret Service via libsecret (`Secret`), never in config files |

Ids are TEXT iCalendar UIDs; any integer id or timestamp column used in
GTK models is int64 (see `XXWM_RULES.md`).

### 4.2 Import path from the Android app

1. Settings: copy the Android "backup to JSON" file over and "restore
   from JSON". The format is byte-compatible by design (§2/F6):
   identical key names, `app: "xx-calendar"`, `version: 1` checked first,
   unknown keys ignored, wholesale-replacement consequence surfaced before
   applying.
2. Events: Android "export .ics" → Linux import `.ics` (target-calendar
   picker, UID dedupe, counts). Real UIDs keep server identity, so
   re-importing a Google/Synology export dedupes instead of duplicating.
3. Standard fallbacks: any iCal producer works the same way through the
   `.ics` pipeline.

### 4.3 D-Bus service, single writer (see §5)

`xx-calendar --service` owns `io.piercingxx.Calendar` (never the GUI app
id) at `/io/piercingxx/Calendar`, interface `io.piercingxx.Calendar1`:
`ListCalendars`, `ListInstances` (expanded occurrences, windowed),
`Get`/`Create`/`Update`/`Delete` (scoped writes), `List` since a
revision, signal `Changed`. The GUI reads SQLite directly for speed but
every write goes through the service, which emits `Changed`. Ships a
D-Bus activation file; idle-exits.

### 4.4 EDS import/publish (the moved EDS data-layer text)

- One-time import from EDS (if present) into the own store, same pipeline
  as the Android `.ics` import (UID dedupe, counts).
- Optional one-way publish to EDS for third-party apps (GNOME Calendar),
  off by default.
- CalDAV two-way sync with the Synology is its own lane (§7, L11);
  credentials via libsecret. With no sync configured the app is fully
  useful against the local "On This Phone" calendar, with no
  "not connected" nag.

## 5. Architecture

```text
src/xx_calendar/
  main.py        Adw.Application, NavigationView, top bar + Today + FAB,
                 theme application, --date handling, .ics activation
  store.py       Own SQLite store (calendars, events + raw VEVENT, revision
                 + tombstones, migrations) — one lock, WAL, 0600/0700
  ics.py         Lossless iCalendar codec (folding, params, VTIMEZONE
                 pass-through, VALARM, attendees) + UID-dedupe import
  recurrence.py  Windowed RRULE/RDATE/EXDATE/RECURRENCE-ID expansion,
                 DST-correct via zoneinfo (stdlib only, no dateutil)
  models.py      GUI-free data types: event/instance/draft, sigil tiers +
                 SigilAssigner, RRuleModel, scope Resolution, TimeMath,
                 AgendaGrouping, ReminderPlanner math, HeartbeatClock math
  service.py     `io.piercingxx.Calendar` single-writer service (L2)
  eds_import.py  One-time EDS import + optional EDS publish (L4)
  reminders.py   Background service consuming the own store (L8):
                 reconcile loop, Notify posts, 03:00 timer, health state
  backup.py      BackupJson v1 read/write (byte-compatible with Android)
  app_config.py  settings.json total reader + XX-WM theme follow
  theme.py       THEME_PRESETS/FONT_FAMILIES tables, preset resolution,
                 CSS generation (no GTK imports — unit-tested headless)
  log.py         rotating file + ring buffer logger
  ui/            schedule/day/week/month/detail/editor/settings pages
                 (later lanes; text-first, 360x720, wrap/ellipsize)
```

- Threading: the GTK main loop never blocks. Store access takes one lock
  (`BEGIN IMMEDIATE`); `.ics` parse, backup I/O, and EDS import run on a
  worker thread or async Gio; results post back with `GLib.idle_add`.
  Service-change invalidation is debounced like `ProviderObserver` (2s).
- D-Bus names: `io.piercingxx.XXCalendar` (app, activatable).
  `io.piercingxx.Calendar` (calendar service, L2) and
  `io.piercingxx.XXCalendar.Reminders` (reminder service, L8) run under
  `systemd --user` with a D-Bus-activated fallback and an XDG autostart
  entry for non-systemd sessions.
- Consumers of the service API (not EDS): the shell's shade calendar
  dots/agenda, the reminder service, and later contacts' birthday opt-in.
- `.desktop`/D-Bus activation/autostart: `io.piercingxx.XXCalendar.desktop`
  (`DBusActivatable=true`, `MimeType=text/calendar;`, Actions NewEvent +
  Today), metainfo `io.piercingxx.XXCalendar.metainfo.xml`, reminder service
  unit `io.piercingxx.XXCalendar-reminders.service` + timer, autostart
  desktop entry, and an OpenRC user-service note mirroring XX-WM's dual
  systemd/OpenRC shipping.

## 6. CalDAV backend sync (Synology / xx-drive hub)

Context from the Android tree: the app itself never syncs. Sync architecture
B won (teardown §2.3): DAVx⁵ owns the Google account and the network while
the app stays a provider client with no `INTERNET` permission. The
self-hosted future is "a Radicale container on the Synology becomes a config
change" (teardown §2.3), and `todo.md` (estate) plans DAVx⁵ pointed at an
xx-drive hub CalDAV on `127.0.0.1`, falling back to a user-chosen CalDAV
origin (estate hostname, Synology, or any server) when the hub is down.

### 6.1 Decision: later CalDAV sync lane against the own store (Revision 2)

Under Revision 2 the app is not an EDS client: the own SQLite store (§4)
is the system of record, and CalDAV two-way sync with the Synology /
xx-drive hub is its own lane (§7, L11), speaking CalDAV directly with
credentials in the Secret Service. This keeps the teardown §2.3 line —
the UI process itself never owns sync or network code paths — while the
sync lane owns exactly the incremental-sync and conflict work EDS would
otherwise have done. Until L11 lands, §4.4 applies: local use plus
one-time `.ics`/EDS import and optional EDS publish.

Why not sync through an EDS source: it would make EDS the system of
record again, contradicting §4, and route every write through a daemon
the app does not control. Rejected under Revision 2.

### 6.2 Whether any backend change is needed

No backend change is needed. Standard CalDAV is standard CalDAV: Radicale
on the Synology, the xx-drive hub, or any server works with EDS as an
unmodified client, exactly as any CalDAV server works with DAVx⁵ today.
Ops-only checklist for the owner: publish the account URL (hub
`127.0.0.1` URL when up, Synology URL otherwise — never `:845x`/`:8447`/
`:8448`/`:8449` as user-facing UX, per `todo.md`), store credentials in
the Secret Service (GOA / source registry does this by default), and keep
server time correct (UNTIL/recurrence depend on it). If the hub is up, EDS
points at the hub URL; if down, at the user-chosen origin. The app shows
only "last change X ago" plus the origin name — sync-state UI stays the
sync layer's job, same honest line Android draws with DAVx⁵.

### 6.3 Linux behaviors with no sync configured

Same as Android R4/§4.4: with no CalDAV source configured the app is fully
useful against the local "On This Phone" source, shows no "not connected"
nag, and surfaces the CalDAV setup once in Settings.

## 7. Milestone plan (build lanes)

Each lane is a self-contained unit of at most ~1500 lines of change with
its acceptance tests. Order is dependency order: data layer → UI →
background services → polish. Every lane extends `src/xx_calendar/` and
`tests/`; every lane keeps `pytest -q tests` and `ruff check src tests`
green and runs headless (pure-module tests; no display).

- L1 — Store + codec + recurrence + pure models (`store.py` schema +
  migrations, revision, `List(since)`, 0600/0700, WAL, one lock; `ics.py`
  lossless codec; `recurrence.py` windowed expansion; `models.py`
  event/instance/draft types, `RRuleModel`, scope `Resolution`,
  `TimeMath`, `AgendaGrouping`, `SigilAssigner` collapsed to four tiers,
  planner + heartbeat math). Accept: ports of the Android JVM suites
  (RRule round-trip, scope table, all-day/DST math, sigil stability,
  agenda grouping, planner diff, 03:00 math) green under pytest, plus
  codec round-trip fixtures, recurrence suites (DST, 31st, BYSETPOS,
  exceptions), store migration/mode/`List(since)` tests.
- L2 — D-Bus service + activation (`service.py`: `io.piercingxx.Calendar`,
  `ListCalendars`/`ListInstances`/`Get`/`Create`/`Update`/`Delete`/`List`,
  `Changed`, activation file, idle-exit). Accept: service tests over the
  real store incl. scoped writes; GUI starts while the service runs.
- L3 — Config + theme follow (`app_config.py`, `theme.py` hardening:
  XX-WM config watch, hot-reload, `custom` derivation, libadwaita
  fallback). Accept: preset/keys byte-match `config.py` tables; corrupt or
  missing shell config falls back without crashing (tests pin this).
- L4 — Backup + `.ics` import/export + EDS one-time import (`backup.py`,
  `eds_import.py`: v1 byte-compat read of a real Android backup fixture,
  UID-dedupe import with counts, bounded read). Accept: Android fixture
  restores settings+sigils; export→import round-trip dedupes.
- L5 — App shell + Schedule + Month (`main.py` NavigationView, top
  bar/Today/FAB, Schedule and Month pages, swipe navigation, 360x720 with
  no horizontal overflow). Accept: headless VM-free smoke (page registry,
  route mapping, `--date` parsing) + on-device screenshot check.
- L6 — Day + Week grids (draw, tap-create, drag move/resize, scope
  prompt wiring). Accept: grid math tests + device gesture check; writes
  go through the L2 service.
- L7 — Detail + editor (detail sheet, editor form, repeat builder,
  scoped delete with undo, failure keeps typing). Accept: scope-resolution
  integration tests against the service; refusal on unmodelled shapes.
- L8 — Reminders, consuming the service (`reminders.py`: reconcile loop,
  Notify posts honoring quiet defaults, 03:00 timer, systemd unit +
  autostart, health row, coexistence doc). Accept: planner regression
  tests + simulated boot/timezone/service-change reconciles; no
  `evolution-alarm-notify` dependency.
- L9 — Settings + calendars pages (all sixteen keys, calendars list
  with visibility toggles, SYNC row, DATA rows, logs row, auto-added filter
  switch). Accept: settings round-trip tests; every row writes something
  real (no decorative switches).
- L10 — XX-WM integration (candidate-list prepend, shade dots/`+N`
  feed + tap-to-open via the service API, date-widget tap action,
  coexistence note for `evolution-alarm-notify`). Accept: shell tests for
  slot resolution and the month-grid feed; app id stability check.
- L11 — CalDAV sync (two-way, Synology / xx-drive hub, credentials via
  libsecret, own store stays the system of record).
- L12 — Polish + ship gate (all-day anchor options, density themes,
  performance on a busy month, log hygiene audit, no-socket audit,
  on-device run: single + recurring all-scope edits, `.ics` import,
  reminder across reboot). Accept: full suite green, device checklist
  signed off, README install verified on a clean image.

Out of scope for these lanes (stay out unless a later spec re-opens them):
in-app search, `.ics` URL subscription, conferencing generation, location
autocomplete, natural-language parsing, tablet layouts, telemetry.

## Revision 2 (owner directive, 2026-10-16) — overrides §4, §5, §6 and §7 where they conflict

xx-calendar OWNS its events, the same way xx-contacts owns contacts. It does not mirror Evolution Data Server.

1. **Own store.** Events, recurrence (RRULE/RDATE/EXDATE), exceptions (RECURRENCE-ID overrides), reminders (VALARM), attendees, calendars (name, color, visibility, sigil tier) live in xx-calendar's SQLite database at `~/.local/share/xx-calendar/calendar.db` (0600 file, 0700 dirs, WAL, busy_timeout). Lossless iCalendar: every VEVENT keeps its original properties and parameters (including unknown X- props) and exports byte-equivalent where the model doesn't touch them, as xx-contacts does for vCard. Ids: TEXT UIDs (iCalendar `UID`); any integer id or timestamp column used in GTK models is int64 (see XXWM_RULES.md).
2. **D-Bus service, single writer.** `xx-calendar --service` owns `io.piercingxx.Calendar` (never the GUI app id) at `/io/piercingxx/Calendar`, interface `io.piercingxx.Calendar1`: `ListCalendars`, `ListInstances(x start_utc, x end_utc, as calendar_ids)` (expanded occurrences, windowed), `Get(s uid) -> s ics`, `Create(s calendar_id, s ics) -> s uid`, `Update(s uid, s ics, s scope, x recurrence_id)` (scope = this / this-and-following / all), `Delete(s uid, s scope, x recurrence_id)`, `List(t since_revision) -> (t revision, as changed, as deleted)`, signal `Changed(t revision, as uids)`. The GUI reads SQLite directly for speed but every write goes through the service, which emits `Changed`. Ships a D-Bus activation file; idle-exits.
3. **Consumers.** The shell's shade calendar dots/agenda, the reminder service, and later contacts' birthday opt-in use this API, not EDS.
4. **Import / sync.** One-time import from the Android `.ics` export and from EDS (if present) into the own store; CalDAV two-way sync with the Synology later (its own lane; credentials via libsecret). Optional one-way publish to EDS for third-party apps, off by default.
5. **Milestones become:** L1 store + iCalendar codec (lossless) + recurrence expansion + pure models (the old Lane 1 ports); L2 D-Bus service + activation; L3 config/theme; L4 backup + `.ics` import/export + EDS one-time import; L5 app shell + Schedule + Month; L6 Day/Week; L7 detail + editor; L8 reminders (consume the service); L9 settings + calendars; L10 XX-WM integration; L11 CalDAV sync; L12 polish.
