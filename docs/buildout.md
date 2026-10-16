# XX-WM build-out plan (2026-10-15)

Review of the whole tree plus a live headless-phoc rig (tablet, 360×720 logical px). The 619-test gate was green while the shell was broken on screen: tests fake every compositor, D-Bus and modem seam. From here on, **a change is done when it is verified on the rig (screenshot / measure) or on a device**, not when pytest passes.

Execution: each task is a Muse lane (contributor mode on, private repo copy, patch out). The orchestrator reviews every patch, applies it, runs `scripts/check.sh`, then re-runs the rig.

## What the review found (headline)

| Area | State on the rig / on hardware |
|---|---|
| Layout | Shell window is **624×728 on a 360×720 screen**: the Settings page needs 624 px and the page stack sizes every page to the widest. Home, drawer and settings are shifted right and clipped; the drawer search sits below the screen edge. First-boot wizard (436 px), power menu (374 px), shade header and popovers also overflow. |
| Typing | phoc speaks layer-shell **v3** (no on-demand keyboard). The home/drawer surface never asks for keyboard focus, so the drawer search, rename, new folder, WiFi password and APN entries can never receive a key. |
| Switcher | **Crashes on import** on the tablet: the vendored bindings need pywayland 0.4.19 and the device has 0.4.18. When it works, it shows raw window titles and leaves the shell covering the app you switched to. |
| Calls | **The shell never sees a call.** ModemManager announces calls through `Modem.Voice.CallAdded`, not the ObjectManager signal the code watches. No outgoing-call UI, `mmcli -m 0` breaks after any modem reset, audio routing is a no-op (no callaudiod), the lock screen can cover a live call, and incoming SMS is never noticed (the SMS window is unreachable). |
| Notifications | No banners at all. The shade always says "handled by external daemon" (it detects itself). App actions are ignored, dismissals never reach the app, expiring notifications vanish from history, and markup shows raw tags. |
| Jank | Every evdev event (touch moves, accelerometers) was posted to the UI loop. The home screen stalls for seconds every 60 s (synchronous `nmcli`/`mmcli` in the status line). The first shade open blocks for ~5 s on synchronous D-Bus probes. The HUD swallowed all touches for 1.2 s per volume press. |
| System | Secured WiFi can never connect (no key-mgmt on the profile). Bluetooth has no pairing agent. Power menu actions do nothing on OpenRC. Auto-brightness writes sysfs without permission. Restore-from-backup crashes (GTK3 `get_filename`). A touch woke a blanked screen in a pocket. |
| Install/session | All of todo.md Workstream 1 is still open. An installed shell crashes on start (meson omits 3 modules). Short swipe-up opens the keyboard instead of going home. pmOS power key powers off (elogind path). Session/unit double-start. |
| Theming | Hot reload misses the home rows, quick tiles, switcher, lock, HUD and back overlay. Fonts and colors otherwise conform to design.md. |

## Wave 1 — make it work (done)

| Lane | Scope | Status |
|---|---|---|
| `w1_display_input` | evdev filtering in the reader thread; no touch-wake while blanked; `display_blank_timeout`; `wake()`/`inhibit_idle()`; HUD and back overlays pass input through; HUD says Volume/Brightness; HUD and back re-theme | **applied** |
| `w1_fit` | Every surface ≤ 320 px min width; non-homogeneous stacks; drawer search always on screen; wizard and power menu fit; **keyboard/layer policy** (TOP+EXCLUSIVE while the drawer, settings or an entry popover is up, BOTTOM+NONE on home); lock screen EXCLUSIVE | done |
| `w1_switcher` | pywayland bindings generated at runtime per installed version (vendored copy removed); app names on cards; drop the shell to BOTTOM on activate; vertical full-width list | done |
| `w1_telephony_core` | Voice.CallAdded/ListCalls; per-call state; outgoing calls; modem by D-Bus path; incoming SMS → notification; caller name; defer to GNOME Calls/Chatty when present | done |
| `w1_notifications` | self-detection fix; async shade construction and tile probes; live tile state; dismiss → NotificationClosed; actions + default action; safe markup; keep history on expiry; show/hide race; quick-tile re-theme | done |
| `w1_system` | async status line; WiFi PSK profiles; BlueZ agent + Trusted; login1 power actions; login1 SetBrightness; update-checker terminals and stale indexes | done |
| `w1_install` | todo.md WS1 1.1–1.7 (meson modules, pacman, one shell per session, gesture verbs, deploy into /usr/share, naming, device prompt); elogind drop-in; IPC socket path, client exit code, single-instance guard | done |

Acceptance for the wave: every surface screenshot on the rig fits 360×720 with nothing clipped; typing works in the drawer search on the rig (virtual keyboard); the switcher lists rig windows; an `xx-wm-ipc` gesture round-trip works; gate green.

## Wave 2 — UI pass (design.md conformance and feel)

1. **Unified theme registry**: one provider per surface reloaded on hot reload (home `theme_css` is currently loaded once from `config.theme`, ignoring `custom`); one fan-out list; remove per-construction provider leaks (wizard replay).
2. **Home**: widget block with no `--°` placeholder (hide weather until there's data; add a location setting); status line with SSID/battery and no flicker; ellipsized slot rows; pressed-state feedback; edit mode with ≥ 44 px targets and a visible row layout.
3. **Drawer**: uniform background (no grey sheet: `.shell-root scrolledwindow` paints `surface`); folders and apps aligned the same way; A–Z strip with real touch targets (today 8pt / 20 px); live app list (`Gio.AppInfoMonitor`); no "Launching…" toast spam; respect `OnlyShowIn`/`NotShowIn`; hide other shells/launchers (design.md).
4. **Shade as a full-height sheet**: tiles fit 4-up at 320 px; tiles show state text (SSID, BT device, "Off"); long-press a tile → its Settings section; sliders visible without a second tap; tap below the sheet closes it; notification grouping per app.
5. **Heads-up banners** (new surface): TOP-layer banner for ~4 s; tap → default action; swipe away; suppressed by DnD, mute and Focus; never above the lock screen. Lock screen shows the count/summary per config.
6. **Lock screen**: ext-session-lock via `Gtk4SessionLock` when the compositor offers it (verify phoc 0.56; the typelib is on the tablet), else OVERLAY+EXCLUSIVE. While locked, ignore shade/switcher/home IPC verbs. Remove the visible Paned separator line. Keypad sized to the screen. Unify the idle-lock and idle-blank timers.
7. **Call UI redesign** (with telephony wave): incoming/outgoing/active pages, hold/swap for a waiting call, DTMF keypad, a minimize-to-CallBar path, always-recoverable "Close" fallback, re-presented above the lock screen.
8. **Settings page**: system-only per design.md, sections that fit, WiFi/BT rows with state, password entry with PASSWORD purpose and hidden text, battery details, About. Move gesture bindings to their own sub-page.
9. **Dialogs/popovers**: width cap (`max_width_chars≈28`, WORD_CHAR wrap), themed, Enter commits.
10. **First boot**: fit, no forced Colemak gsettings change on non-phone sessions, timezone via the installer (no polkit agent in-session).

## Wave 3 — telephony to daily-driver (needs FLX1 / L5 for verification)

callaudiod-backed audio routing (earpiece/speaker/mute through `org.mobian_project.CallAudio`), proximity blanking during calls (iio-sensor-proxy), wake-on-ring, ringer via feedbackd (`phone-incoming-call`, respects silent profile) and a WAV/OGG ringtone (libsndfile MP3 is missing on PureOS), call log + missed-call notification, `+` on long-press 0, SMS inbox/thread list or a clean hand-off to Chatty, contacts lookup everywhere, repeat-caller memory persisted.

Decision needed (see "Open decisions").

## Wave 4 — performance and robustness

- Measure startup, first-shade, drawer-open and swipe latency on the Librem 5 (performance canary). Choose `GSK_RENDERER` per device (the rig warns about Vulkan surface loss; NGL or cairo may be faster on weak GPUs).
- Lazy construction everywhere, no synchronous subprocess or D-Bus on the main loop (lint rule in `check.sh`: grep for `subprocess.run`/`check_output`/`call_sync` outside worker helpers).
- Fewer config writes (`record_launch` writes eMMC on every launch: batch it).
- ~~`window.py` split~~ done (`w4_split`: home_page / drawer_page / settings_page / call_wiring).
- Rig in the repo: `scripts/rig.sh` + `XX_WM_DEV=1` debug IPC verbs (page, search, measure, notify, call) and a "fits the screen" check that fails the run if any surface's minimum size exceeds 320×640.

## Wave 5 — devices (todo.md Workstreams 2–7, unchanged in substance)

Tablet cutover and smoke (2–4), FLX1 recovery and bring-up (5–6), Librem 5 canary (7). Waves 1–2 should land first so the smoke tests a shell that fits and types.

## Wave 5 — Android-parity features (owner request 2026-10-15)

Features Linux phone shells don't have yet. Call screening and spam blocking live in the xx-dialer port, not the shell.

| Lane | Feature | Status |
|---|---|---|
| `f_emergency` | Emergency calls + Medical ID from the lock screen | done |
| `f_media` | MPRIS media card in shade (lock screen next), headset keys | done (lock-screen card too) |
| `f_helper` | Root helper (D-Bus + polkit): charge limit, per-app network, cgroup freeze | done |
| `f_usage` | Screen-time data layer (foreground time, unlocks, notifications) | done |
| `f_standby` | App standby (freeze unused apps), battery saver + auto at 15% | done |
| `f_privacy`, `w5_settings_a` | Per-app permissions (network/camera/mic/location) + privacy indicators | done |
| `f_smartlock`, `w5_settings_a` | Smart Lock (trusted Bluetooth device / Wi-Fi) | done |
| `w5_settings_b` | Charge limit UI + per-app battery usage (estimated) | done |
| `../xx-find` | Find My Device, self-hosted on the Synology | being built; owner: must NOT run yet |
| `w5_settings_b` | Screen time UI (+ per-app limits) | done |
| `../xx-share` | Nearby Share, LocalSend-compatible | built; LocalSend interop verified (send); receive test pending |
| `../xx-captions`, `w5_captions_tile` | Live captions + call transcription (local) | built (engine, service, overlay, shell tile); VM run pending |
| `f_capture` | Screen recording + scrolling screenshot | done |
| `w3_xxdb` | Wireless debugging (pair code, shell/logs/screenshot/deploy/input) | done |

## Wave 6 — Android app ports (owner request 2026-10-15)

Sources in `../android/`, Linux ports as sibling repos in `../` (`xx-contacts`, `xx-dialer`, `txxt`, `xx-calendar`, `xx-vitals`, `xx-email`). Each starts with a Muse spec + scaffold lane (`PORT_SPEC.md`), reviewed before the build lanes it defines. Stack: Python + GTK4/libadwaita; contacts and calendar on Evolution Data Server so the shell, GNOME Calls and the ports share data.

## Test platforms

- **Tablet rig** (`dr3k@192.168.1.129`): headless phoc + grim, fast surface screenshots.
- **VM** (`../../vm/`): postmarketOS v26.06 Phosh image under KVM at 720x1440. Real greeter (greetd/phrog), real install, real touch via a uinput touchscreen, SSH. The VM caught the broken `/usr/bin/xx-wm`, the wrong pmOS package names, and pmOS's switch to systemd.

## Open decisions (owner)

1. **Telephony ownership** — now that xx-dialer is being ported (with call screening), the `port_spec_dialer` lane proposes the split between the dialer and the shell's lock-screen call surface; owner reviews it. Recommended: GNOME Calls / Chatty own modem, audio routing and SMS storage where installed (FuriOS, pmOS). The shell shows its own call surface only on the lock screen and as the fallback when Calls is absent, like Phosh does. The alternative is a fully in-shell telephony stack (more work, duplicates callaudiod handling).
2. **Suspend policy.** Today the phone never suspends; blank only. Options: never (modem-safe), or s2idle after N minutes blanked on devices with modem wake.
3. **Commit cadence.** Patches are applied to the working tree, gate-green, and not committed. The proposal is one commit per lane.
