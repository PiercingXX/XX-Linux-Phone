# Piercing WM — Work Order (Skippy)

You are Skippy, working on **Piercing WM**: a minimalist, text-first Wayland launcher/shell for Linux phones. Read `README.md` (identity), `design.md` (the UI spec — treat it as the contract), and `launcher/README.md` (code layout) before touching anything.

**Everything in Workstreams 1–10 runs on the dev machine — no phone required.** Device-gated work is quarantined at the bottom; do not attempt it.

## Ground rules

- **Spec**: `design.md` wins. Where this file and design.md disagree, design.md is right; flag the conflict in your commit message.
- **Style**: Python 3.12+, GTK4/libadwaita via `gi`, match the existing code's idiom. No comments unless the WHY is non-obvious. Text-first UI: no icon grids, no images, monochrome per theme.
- **CSS invariants** (`launcher/src/style.css`): uniform background from the active theme on every surface; children transparent; no borders anywhere except inside `.settings-page`; Space Mono default; invisible Paned separators. Don't regress these.
- **Verify before every commit**: `python3 -m py_compile launcher/src/*.py` and `python3 -m pytest tests/ -q` (create `tests/` in Workstream 10 — from then on it gates everything). If you add a runtime behavior, run the shell locally (`cd launcher && PYTHONPATH=src python3 src/main.py` — it falls back to a window when layer-shell is absent) and exercise the flow.
- **Commits**: one commit per task or coherent group, imperative subject, body says what changed and how it was verified. Never commit `__pycache__`, `build/`, or `devices/*/downloads/` (gitignored).
- **Config compatibility**: `~/.config/piercing-shell/config.json` may exist from earlier runs. Every schema change needs a silent migration path (missing keys → defaults; never crash on old configs).
- **Decisions already made** (don't relitigate): keep `piercing-shell` internal naming for now (rename is user-gated, see bottom); keep the `aura` theme preset as a seventh Linux-only bonus; phoc is the compositor; lisgd owns system-level gestures via IPC.

---

## Workstream 1 — Home: 8-slot model (the big one)

The home model is **up to 8 ordered slots**, each holding an app, a pinned shortcut, or a folder (`design.md` "Home screen"). Current code instead renders a hardcoded `HOME_ITEMS` tree (`launcher/src/home_launcher.py:130`) plus a `pinned` list (`config.py`). Replace that with the slot model.

- [ ] **1.1 Slot schema in config** — `config.py`: add `home_slots` to `DEFAULT_CONFIG`: list of ≤8 dicts, `{type: 'app'|'folder', label: str, app_id: str|None, cmd: list[str]|None, folder: [member…]|None}` where members are `{label, app_id or cmd}`. `app_id` is a `.desktop` id resolvable by `Gio.DesktopAppInfo.new()`; `cmd` is an argv list for non-desktop entries (e.g. `piercing-note`). Accessors: `get_home_slots()`, `set_home_slots(slots)` with validation (cap 8, reject unknown types). Migration: if `home_slots` missing, empty list.
- [ ] **1.2 Default layout seeding** — new `launcher/src/default_layout.py`: on first boot (`home_slots` empty AND a `default_layout_applied` flag unset) seed: **Notes** → `cmd: ['piercing-note']` if found on PATH else first installed notes-ish app else skip; **Audio** folder → Audiobooks, Music; **Comms** folder → Phone (built-in dialer), Text, Email; **Tools** folder → Internet (default browser via `Gio.AppInfo.get_default_for_type('x-scheme-handler/http', True)`), Camera, Calculator, Photos. Resolve each against installed desktop apps; skip unresolvable members; don't create empty folders; set the flag even if nothing resolved. **Never overwrite a non-empty `home_slots`.** Unit-test the resolution logic with an injected fake resolver.
- [ ] **1.3 Render home from slots** — rewrite the home page in `home_launcher.py`/`window.py` to render `get_home_slots()`: one text row per slot, alignment from `config.home_alignment`, folder rows open a folder view. Delete `HOME_ITEMS` and the old pinned-row rendering once the slot model is live (search `window.py` for `pinned` usages).
- [ ] **1.4 Folder view** — full-screen overlay (same window, stack page or modal box — follow the existing stack pattern) listing member rows in the same typography; tap launches, back gesture/row closes. No nested folders.
- [ ] **1.5 Edit mode** — long-press on home (there's already a `GestureLongPress` wired to settings — rebind: long-press → edit mode, move settings entry into edit mode header). In edit mode: per-slot remove (✕), reorder (up/down buttons — GTK4 DnD stays out of scope), "add slot" when <8 (picker: app from drawer / new folder), folder editing (rename, add/remove members from an app picker).
- [ ] **1.6 Launch dispatch** — single `launch_slot(slot)` helper: `app_id` → `Gio.DesktopAppInfo.launch()`, `cmd` → `subprocess.Popen`, label `Phone` with no target → built-in dialer surface (preserve the existing special-case). Record usage via `config.record_launch()`.

## Workstream 2 — Drawer

Current drawer (`window.py` + `app_index.py`): search-on-open, A–Z jump strip, hidden apps, usage/A–Z sort. Gaps vs `design.md` "App drawer":

- [ ] **2.1 Auto-launch single result** — new config bool `search_auto_launch` (default off). In the drawer search handler: when the query narrows to exactly one visible row and the user presses Enter — or, when the option is on, immediately — launch it.
- [ ] **2.2 `!` web search fallback** — query starting with `!` → strip prefix, open `https://duckduckgo.com/?q=<urlencoded>` via `Gio.AppInfo.launch_default_for_uri()`. Also offer it as the Enter action when a non-`!` search has zero results (the spec is prefix-only — keep it that way unless extending is trivial).
- [ ] **2.3 Per-app rename labels** — config dict `app_labels: {app_id: label}`. Long-press a drawer row → menu with Rename / Hide / Pin-to-slot / App info (skip uninstall — package ops are distro-specific and device-gated). Renamed label shows everywhere: drawer, search, folders, home slots (slot label copies it at pin time; renames afterwards update slots pointing at that `app_id`). Search matches both original and renamed label.
- [ ] **2.4 Sort modes** — extend the drawer sort toggle to cycle: Default (usage when counts exist, else A–Z) → A–Z → Install date (mtime of the `.desktop` file — good enough) → back. Skip size sort (package size is ill-defined here); document the omission in `design.md`.
- [ ] **2.5 Visibility options** — two config bools in Settings: `hide_home_items_from_search` and `hide_folder_members_from_drawer`; filter accordingly in `app_index.search()` call sites (home-slot and folder-member `app_id`s come from `home_slots`).

## Workstream 3 — Themes

`config.py:23` has the right preset names but **wrong colors** vs the spec, and the wrong default.

- [ ] **3.1 Align backgrounds** — set preset background (first hex) to the canonical values from `design.md`: amoled `#000000` (already right), graphite `#111827`, forest `#10261B`, ocean `#0F1C2E`, paper `#F3EEE2`, mist `#E6EDF5`. Rederive the surface/border shades per preset from its background (keep the current relative-lightness relationships); keep text colors near-white on dark presets, near-black on paper/mist. Keep `aura` untouched (Linux bonus).
- [ ] **3.2 Default = AMOLED** — `DEFAULT_CONFIG['theme'] = 'amoled'` (spec default). Existing configs keep their saved choice.
- [ ] **3.3 Custom solid color** — Settings: a hex entry (validate `#RRGGBB`) that builds an ad-hoc ThemePreset from one background color (auto-derive shades + pick black/white text by luminance). Persist as `theme: 'custom'`, `custom_background: '#…'`. Backgrounds are solid colors only — no wallpaper support, ever.
- [ ] **3.4 Burgundy** — add `#2A1018` as a named custom-color suggestion (an extra background, not a preset — see `design.md` "Themes").

## Workstream 4 — Fonts

`FONT_FAMILIES` (`config.py`) has system-light/space-mono/jetbrains-mono. The spec adds:

- [ ] **4.1 JetBrains Mono Nerd** — add `'jetbrains-mono-nerd': 'JetBrainsMono Nerd Font, JetBrains Mono, Monospace'`. Render gracefully when not installed (the fallback chain handles it).
- [ ] **4.2 Custom font import** — Settings: file path entry (a phone file-picker is overkill; text entry + validation is fine) for a `.ttf`/`.otf`; copy into `~/.local/share/fonts/`, run `fc-cache -f` (subprocess, silent on failure), store `font: 'custom'`, `custom_font_family` read via Pango after install. Note: custom font files are not part of backup (4.3 → Workstream 6).

## Workstream 5 — Widgets row (time / date / battery / weather)

Home header currently: clock, date, battery+network status strip, hardcoded. Spec (`design.md` "Home screen"):

- [ ] **5.1 Widget config model** — `config.py`: `widgets` dict `{time: {enabled, order, tap}, date: {…}, battery: {…}, weather: {…}}`; `tap` ∈ `'default' | 'none' | {'app': app_id}`. Defaults: time/date/battery enabled, weather disabled.
- [ ] **5.2 Render + tap actions** — build the header from the config, ordered, skipping disabled. Tap `default`: time → installed clock app if any else none; date → calendar app; battery → power/settings page; weather → refresh. Tap `{'app': id}` → launch it.
- [ ] **5.3 Settings section** — per-widget enable switch, order up/down, tap-action dropdown (Default / Open app… / None).
- [ ] **5.4 Weather widget** — new `launcher/src/weather.py`: Open-Meteo current-conditions endpoint (no API key), lat/lon from two config floats set manually in Settings (no GPS dependency — iio location is device-gated). Cache result to `~/.cache/piercing-shell/weather.json`; refresh at most every 15 min (per spec); render `18° Clear` text-only; **silent** (widget shows `--°`) when offline/unset. Fetch in a `threading.Thread(daemon=True)` + `GLib.idle_add` like `sms.py` does — never block the main loop. Unit-test the cache/staleness logic with injected clock + fetcher.

## Workstream 6 — Backup / restore (JSON)

Match the backup scope in `design.md` "Backup / restore":

- [ ] **6.1 Export** — `launcher/src/backup.py`: `export_backup() -> dict` with `{version: 1, home_slots, app_labels, hidden_apps, widgets, theme (+custom color), font, text_size_scale, home_alignment, auto_lock_timeout, gestures (from gesture_config), search/visibility prefs}`. Explicitly excluded: PIN hash (security), launch counts (noise), custom font file. Settings button "Export backup" → write `~/piercing-wm-backup-YYYYMMDD.json`.
- [ ] **6.2 Restore, atomic** — `restore_backup(path)`: parse → validate the **entire** payload against the schema (types, slot cap, known theme/font/gesture keys) → only then apply, via one `ShellConfig` write + one gestures write. Invalid payload = zero writes + visible error label (the no-write-on-invalid-payload guarantee). Settings button "Restore from backup" with a confirm step.
- [ ] **6.3 Tests** — round-trip test (export → restore onto fresh config → configs equal), and a table of malformed payloads (truncated JSON, wrong types, 9 slots, unknown gesture action) asserting nothing was written.

## Workstream 7 — Sounds

Assets already in `launcher/data/sounds/` (ringtone.mp3, notify.wav, comm-on.wav, alert.wav).

- [ ] **7.1 Meson install** — `install_data` the four files to `datadir/piercing-shell/sounds/`.
- [ ] **7.2 Player helper** — `launcher/src/sound.py`: resolve sound dir (installed path, fall back to repo-relative for dev runs); `play(name, loop=False)` / `stop()` via `paplay` subprocess (PipeWire's pactl frontend ships it everywhere we target); loop = respawn on exit from a daemon thread until stopped. Silent no-op if `paplay` is missing.
- [ ] **7.3 Wire ringtone** — `call_ui.py`: loop `ringtone.mp3` on `show_incoming()`, stop on accept/decline/`end_call()`/remote hangup (all `StateChanged` paths in `modem_monitor.py` flow through `window.py._on_call_*` — stop in every terminal path).
- [ ] **7.4 Wire notification sound** — `notif_daemon.py`: play `notify.wav` on `Notify` unless the shade's DnD tile is active (read the DnD state from `quick_actions.py` — it's currently a stub tile; give it a real boolean the daemon can query) or the notification carries the `suppress-sound` hint.
- [ ] **7.5 Config toggles** — Settings switches: `sound_ringtone` (default on), `sound_notifications` (default on).

## Workstream 8 — Gesture polish

`gesture_config.py` already has the right shape. Gaps:

- [ ] **8.1 Arbitrary app targets** — swipe-left/right should launch *any chosen app* (`design.md` "Gestures"). Extend actions with `launch:<app_id>` (validate the id exists at bind time, fall back to `none` at dispatch if uninstalled). Settings gesture editor: the dropdown gains "Launch app…" → app picker.
- [ ] **8.2 Swipe-down choice** — verify the Settings editor exposes `swipe_down_top` = notifications vs search (both already valid actions) and that the `search` action opens the drawer with keyboard focus. Fix if not.
- [ ] **8.3 Prune dead slots** — `squeeze` and `double_press_power`/`fingerprint_swipe` are hardware-gated; keep them in `_DEFAULTS` but hide from the Settings editor behind a "hardware gestures" expander with a note that they need device support.

## Workstream 9 — Scripts (installer / deploy / init)

Installer pattern: whiptail menu, cached sudo, network check up front.

- [ ] **9.1 `scripts/install.sh`** — whiptail TUI, POSIX sh. Menu: Install / Update / Reboot / Exit. Install path: detect `apk` vs `apt` → install deps (`py3-gobject3 gtk4 libadwaita gtk4-layer-shell meson ninja rsync` | Debian equivalents `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libgtk4-layer-shell0 …`) → `meson setup build && meson install` from `launcher/` → install session files → run `scripts/bootstrap-dots.sh` (tolerate its current loud failure — the piercing-dots phone profile is a parallel task) → enable the shell service (systemd user unit or OpenRC, detected via `ps -p 1`).
- [ ] **9.2 `scripts/deploy.sh`** — dev-loop rsync deploy: env `PIERCING_DEVICE` (required), `PIERCING_USER` (default `user`); rsync `launcher/src/` → `~/piercing-shell/src/` on device, then restart the shell service over SSH, handling both systemd (`systemctl --user restart piercing-shell`) and OpenRC (`rc-service piercing-shell restart` via doas/sudo). `--dry-run` flag. Can't be end-to-end tested without a phone — test the argument handling and rsync command construction locally (echo mode).
- [ ] **9.3 OpenRC service** — `launcher/data/openrc/piercing-shell` init script equivalent to the systemd user unit (respawn on failure), meson-installed. postmarketOS default images are OpenRC; this unblocks device day one.
- [ ] **9.4 App-set menu entry** — optional "Install phone apps" install.sh menu item, distro-aware: Neovim, Yazi, Flathub (FLOSS subset), UFW + allow SSH, Tailscale, Waydroid (skip cleanly where unavailable). Skip Homebrew (broken on musl). Each item guarded by `command -v`/repo checks — the menu must never hard-fail on one missing package.
- [ ] **9.5 `shellcheck`** — all of `scripts/*.sh` and `devices/*/*.sh` pass `shellcheck -s sh` (or `-s bash` where the shebang says bash). Add fixes as needed.

## Workstream 10 — Tests & QA harness

- [ ] **10.1 `tests/` with pytest** — pure-logic coverage, no GTK imports needed: `config.py` (defaults, migration, slot validation), `default_layout.py` (fake resolver: full/partial/none resolution, never-overwrite), `backup.py` (6.3), `gesture_config.py` (unknown key/action rejection, `launch:` validation), `app_index.py` search matching + sort modes, weather cache logic. Guard GTK-importing modules out of test collection (`tests/conftest.py`).
- [ ] **10.2 CI-ish gate script** — `scripts/check.sh`: `py_compile` all sources + `pytest -q` + `shellcheck`. This is the pre-commit gate; run it before every commit.
- [ ] **10.3 Docs drift** — when a workstream lands, tick it here and update `design.md`/`launcher/README.md` if behavior diverged from spec (e.g. the APK-size sort omission).

---

## Suggested order

1 → 3 → 2 → 7 → 10.1/10.2 (early, then continuous) → 4 → 8 → 5 → 6 → 9 → 10.3. Workstream 1 first: everything else touches config, and the slot migration is the riskiest schema change — land it while the surface area is small.

## Blocked — do NOT attempt (needs hardware or the user)

- **Flashing / device bring-up** (Fairphone 5 fastboot, Librem 5 session swap, FLX1 research) — `devices/*/notes.md` hold the checklists.
- **lisgd/wob/wvkbd runtime integration, threshold calibration, telephony testing** — phone required.
- **Final product rename** (`piercing-shell` → ?) — user decision; when made, rename DBus namespace, socket, service, meson project in one commit.
- **piercing-dots phone profile** — separate agent, separate repo. This repo only consumes `install.sh --profile phone` via `scripts/bootstrap-dots.sh`.
- **Publishing/releases** — none until first device boot.
