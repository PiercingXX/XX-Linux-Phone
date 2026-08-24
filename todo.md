# XX-WM — Work Order (Skippy)

You are Skippy, working on **XX-WM**: a minimalist, text-first Wayland launcher/shell for Linux phones. Read `README.md` (identity), `design.md` (the UI spec — treat it as the contract), and `launcher/README.md` (code layout) before touching anything.

**Workstream 25 runs on the dev machine — no phone required.** The tablet checklist needs the x86 test tablet; device-gated work is quarantined at the bottom; do not attempt it.

## Ground rules

- **Spec**: `design.md` wins. Where this file and design.md disagree, design.md is right; flag the conflict in your commit message.
- **Style**: Python 3.12+, GTK4/libadwaita via `gi`, match the existing code's idiom. No comments unless the WHY is non-obvious. Text-first UI: no icon grids, no images, monochrome per theme.
- **CSS invariants** (`launcher/src/style.css`): uniform background from the active theme on every surface; children transparent; no borders anywhere except inside `.settings-page`; the **configured font applies launcher-wide** via `font_theme.apply_global_font` (default JetBrains Mono Nerd — surfaces must not hardcode a family); invisible Paned separators. Don't regress these.
- **Verify before every commit**: `sh scripts/check.sh` (py_compile + ruff + pytest + shellcheck — **472 passing as of 2026-08-24**, up from 313 pre-WS26; ruff resolves from the venv per 25.1, shellcheck is absent on this box and prints a loud `SKIPPED`). If you add a runtime behavior, run the shell locally (`cd launcher && PYTHONPATH=src python3 src/main.py` — it falls back to a window when layer-shell is absent) and exercise the flow.
- **Commits**: one commit per task or coherent group, imperative subject, body says what changed and how it was verified. Never commit `__pycache__`, `build/`, or `devices/*/downloads/` (gitignored).
- **Config compatibility**: `~/.config/xx-wm/config.json` may exist from earlier runs (and auto-migrates from `piercing-shell`). Every schema change needs a silent migration path (missing keys → defaults; never crash on old configs). `docs/config.md` is the public API reference — update it with every key change.
- **Decisions already made** (don't relitigate): the product is **XX-WM** (renamed 2026-08-17; app id `io.piercingxx.XXWM`, binaries `xx-wm`/`xx-wm-ipc`/`xx-wm-session`); phoc is the compositor; lisgd owns system-level gestures via IPC; the keyboard is **squeekboard** with the PiercingXX Colemak layouts; the lock screen stays ours (no phrog/phosh code, ever); DnD and Focus Mode copy the **Pixel's** behavior; the in-shell Settings page is **system-only** — every shell preference lives in `~/.config/xx-wm/`; backgrounds are solid colors only, never wallpaper; `aura` stays as a Linux-only bonus theme; the volume/brightness HUD is **in-shell** (wob dropped, WS21.1); Android-launcher parity syncs (2026-07-20/21) are already folded into design.md — design.md is current.
- **Minimalism directive**: the user has said the test tablet is underpowered — keep everything lightweight. Prefer text over textures, skip caches/preloads/snapshots unless explicitly reconfirmed.

## Status — where things stand (2026-08-23)

**Workstreams 1–24 are done and verified on master.** Master is clean, `sh scripts/check.sh` is green at 313 tests, and `.venv/bin/ruff check launcher/src tests` passes.

Original build plan 1–18 (see git history): 8-slot home model with inline folders and edit mode; one-handed drawer (85% sheet, bottom search/results, long-press menus, `!` web search); 8 themes + custom solid color; fonts incl. custom import; config-driven widget row with Open-Meteo weather; JSON backup/restore; sounds; gesture dispatch; install/deploy/apps scripts + OpenRC & systemd units; pytest suite + `check.sh` gate; lock screen v2; shade v2; Pixel-model DnD and Focus Mode; system-only Settings page; squeekboard + Colemak layouts; default app set scripting; first-boot walkthrough.

Post-device-session workstreams 19–24, all landed since:

- **19 — App switcher, real window list.** `toplevel_manager.py` speaks `wlr-foreign-toplevel-management-unstable-v1` via `python-pywayland` on a GLib fd-watch; protocol XMLs vendored under `launcher/data/`. `app_switcher.py` populates from it (wmctrl gone), tap → activate, ✕/swipe-up → close, own surfaces filtered, graceful empty state. Text-only cards. Covered by `test_toplevel_manager.py`, `test_app_switcher_wiring.py`, `test_switcher_cards.py`, `test_protocol_vendoring.py`.
- **20 — Session & input fixes.** Keyboard hides on tap-outside; power menu goes full-screen; `install.sh` adds the user to `input`; `preload_gesture_apps` gated off by default; per-device phoc.ini fragments under `launcher/data/phoc/` (tablet, FP5, FLX1, L5).
- **21 — Volume/brightness HUD.** In-shell `hud.py` on the OVERLAY layer, theme-derived, ~1 s auto-hide, silent when GTK/layer-shell can't init. wob removed from `install.sh` and the README stack.
- **22 — Theme-invariant sweep.** Every surface derives its CSS from the active `ThemePreset`. The only hardcoded hex left in `launcher/src/` is `config.py`'s preset table (legitimate) plus one intentional `#ffffff` on `.btn-hangup` in `call_ui.py` (see 25.4).
- **23.2 / 23.3 — Docs & hygiene.** README, `launcher/README.md`, and `docs/config.md` are in lockstep with the code; every `DEFAULT_CONFIG` key is documented. `scripts/reference/linux-phone-mod/` already carries its own "vendored reference / not runnable" header, satisfying the 23.3 note requirement.
- **24 — GDM Colemak OSK.** `launcher/data/gnome-osk/us.json` in GNOME's osk-layouts schema, installed by a GDM-guarded `install.sh` step that backs up the stock file. Covered by `test_gnome_osk.py`.

**Everything that follows is the remaining work.**

---

## Workstream 25 — Dev-machine cleanup (current, no phone required)

Small items surfaced by the 2026-08-23 repo review. None blocks the tablet checklist; do them while a device isn't in hand.

- [x] **25.1 `check.sh`: stop silently skipping ruff** — the gate probes `command -v ruff`, but ruff lives at `.venv/bin/ruff` and is not on PATH, so **every run since the venv was created has skipped linting** while printing "All checks passed". Resolve it the way the pytest branch already does: prefer `"$PYTHON" -m ruff` / `.venv/bin/ruff`, fall back to PATH. Ruff currently passes clean, so this is a gate hole, not a backlog of violations. Same question for shellcheck — it's absent on this box entirely; either vendor a check or make the skip loud (print `SKIPPED` in a way a reader won't mistake for a pass). **Done 2026-08-23:** ruff now tried via `$PYTHON -m ruff`, `.venv/bin/ruff`, then PATH; missing tools print loud `SKIPPED` lines (non-fatal), found-but-failing still fails — verified with `sh -n` plus a full gate run, ruff executing from the venv.
- [x] **25.2 Retire the `device-testing-fixes` branch** *(supersedes the old 23.1 — its premise was wrong)* — the branch is **not** ahead of master. `origin/master...origin/device-testing-fixes` is 39/41, and `git diff origin/master origin/device-testing-fixes` is **pure deletion**: the branch is missing `hud.py`, `toplevel_manager.py`, `lock_lines.py`, the vendored protocol XMLs, the per-device phoc fragments, the gnome-osk layout, and 17 test files. Every device-bring-up fix it carried (`INPUT_PROP_DIRECT`, `LD_PRELOAD`, the auto-maximize GSetting, power-key ownership, the rebrand) is already on master via the laundry-bot history. **Merging it would revert shipped work.** Action: confirm with the user, then `git push origin --delete device-testing-fixes`. Do not merge. Do not push anything without asking. **Done 2026-08-23:** confirmed with the user and the branch is gone — no longer on origin or locally; nothing was merged, master untouched.
- [x] **25.3 Resolve the PIN-length spec conflict** — `design.md:103` and `design.md:108` both say **6-digit PIN**; the code enforces a **4-digit minimum** (`first_boot.py:371`, `first_boot.py:582`) with a 64-digit cap (`lock_screen.py:_MAX_PIN`), and `docs/config.md:87` documents "minimum 4 digits". Two commits on the old branch (`17a9cb2` PIN minimum wording, `2192352` raise PIN cap) look like a deliberate change that never reached design.md. Ground rule says design.md wins, but this needs the user's call, not a unilateral fix. **Ask which is authoritative**, then make all three agree in one commit. Resolution (user call): enforce **6 digits** — code and docs now enforce it; design.md already did. **Done 2026-08-23:** both wizard gates raised 4→6; lock-screen entry path unchanged (hash compare only, stored short PINs still unlock); `docs/config.md` updated. Verified: ruff + new/updated pin tests.
- [x] **25.4 Name the one remaining literal color** — `call_ui.py:69` sets `color: #ffffff` on `.btn-hangup` for contrast against `DANGER_RED`. It's semantically correct in every theme, so this is polish, not a bug: promote it to a named constant beside `DANGER_RED` in `config.py` (`ON_DANGER_FG`) so the WS22 invariant reads as absolute — grep for a bare hex outside `config.py` should return nothing. **Done 2026-08-23:** `ON_DANGER_FG` sits beside `DANGER_RED`, `.btn-hangup` uses it, bare-hex sweep outside `config.py` is clean. Verified: ruff + targeted pytest run.
- [x] **25.5 Location & Hotspot tiles: implement or drop from the spec** — `design.md` lists both in the shade's expanded tier, but `quick_actions.py:215-224` defines them as no-op stubs and `_tiles()` unconditionally skips them ("stay hidden until something real backs them"). That's the honest interim state, but it's an undelivered spec line nobody is tracking. Decide: back them for real (Location via geoclue/`org.freedesktop.GeoClue2`, Hotspot via `nmcli device wifi hotspot`, both hardware/service-gated like Torch and Auto), or cut them from `design.md`'s tile list. Recommendation: implement Hotspot (NM already does the work, one nmcli call each way) and cut Location — a text-first shell has no consumer for a location toggle, and geoclue is a dependency the minimalism directive doesn't want. **Done 2026-08-23:** both tiles implemented (went past the rec to cut Location): Hotspot real via nmcli, gated on NM + a WiFi device; Location via the GeoClue2 Agent API with revoke on `MaxAccuracyLevel=0`, hidden when geoclue is absent or registration is rejected; stubs removed; ~26 new mocked tests. Verified: ruff + full suite (287).
- [x] **25.6 README image caption drift** — the theme-preset screenshot caption says "six theme presets" while the body, `design.md`, and `config.py` all say eight (Aura and Burgundy came later). Either reshoot `docs/images/theme-presets.jpg` with all eight or reword the caption to say the shot predates Aura/Burgundy. Trivial; fold into whatever docs commit comes next. **Done 2026-08-23:** caption reworded to note the shot predates Aura/Burgundy. Verified in the README render.

---

## WS25 follow-ups (from 2026-08-23 review)

**Resolved 2026-08-23** in the post-review hardening pass (suite 287 → 313, ruff clean, gate green):

- Location agent docstring overstated per-app authorization — corrected: the tile is a **global master switch** (ON grants every client up to EXACT accuracy, OFF denies all via `MaxAccuracyLevel=0`); per-app policy deliberately not built.
- Geoclue restart mid-session left a stale agent registration / lying tile — fixed: the `org.freedesktop.GeoClue2` name owner is watched; an owner change drops the export, re-registers when the tile is ON, and tile state now derives from the live registration (`is_active()`), never from intent.
- Agent-slot displacement — registration is lazy (first enable) and released on disable; the availability probe is read-only, so building the shade no longer claims the system's single geoclue agent slot.
- Hotspot OFF assumed a profile literally named "Hotspot"; state query used nmcli while the gate used D-Bus — unified: the active hotspot is resolved via NM D-Bus `ActiveConnections` (`Type == 'ap'`), deactivated by object path, and state is answered from that same view. No profile-name assumption.

Also landed in the same pass (found by the review, outside WS25):

- Lock screen consulted a construction-time config snapshot — a PIN set out-of-band after startup was never enforced until shell restart. `LockScreen` now shares the window's live `ShellConfig`, so hot-reloaded PINs apply at decision time.
- `fprintd-verify -f <username>` passed a username where fprintd(1) wants a finger name — fingerprint unlock could never succeed. Now bare `fprintd-verify`.
- `launch_counts` crashed on corrupt/non-numeric config values — now skips bad entries per-item, same idiom as `muted_apps`.
- `ipc.py` docstring listed a nonexistent `unlock` verb — corrected; the socket has no unlock path by design.

Still open (watch items, none blocking):

- Refresh-path sync D-Bus fan-out on the GTK main loop: `_nm_has_wifi_device()` does 1 + N Gets per refresh and `_active_hotspot_path()` adds one Get per active connection; fine today, revisit if the shade ever stalls on the underpowered tablet.
- After a location-enable rejection the tile stays hidden until the geoclue owner changes or the shell restarts (matches hide-on-reject); auto-retry when a competing agent quits would need a panel-side re-probe trigger.
- Hotspot toggle feedback is still optimistic for the ON direction (nmcli runs async; next refresh corrects) — OFF is now state-driven.
- Tablet checklist additions: verify geoclue revokes live clients on `MaxAccuracyLevel=0`; confirm enable-time registration behaves against phosh's prompting agent on device.

---

## Workstream 26 — Post-review hardening (done 2026-08-24, dev machine)

Two-agent review (code reviewer + research audit) surfaced blocker/high defects concentrated in the paths a phone lives or dies by. All dev-machine-fixable items landed in 8 commits; suite went 313 → **472 passing**, gate green.

- **Incoming calls were dead on arrival** — `CallBar()` constructed without its required `on_expand` (TypeError before ringtone/UI), and nothing ever called MM1 Accept/Hangup. Fixed: real accept/hangup D-Bus calls wired through the buttons, CallBar show/hide contract satisfied.
- **Dialing/SMS never transmitted** — invalid mmcli verbs (`--voice-call=`, create-without-send), verified against upstream mmcli source; now create→parse→start/send with failure surfaced instead of silently swallowed.
- **Switcher backend could never connect** — five pywayland glue defects (bad import, inverted dispatcher registration, fd-watch never armed, missing wlr protocol module handling, phantom create_toplevel/seat-less activate) plus a stale app_id/title cache. Backend degrades honestly until the protocol module is generated on device.
- **Lock/security core hardened** — atomic 0600 config saves (a torn write used to silently disable the PIN), salted PBKDF2 PIN storage with transparent legacy-sha256 upgrade, fail-closed corrupt hashes, IPC socket chmod 0600 + bounded reads (a silent local client used to freeze the UI thread) + logged dispatch errors.
- **GTK4/API breakage** — backup restore's removed `Dialog.run()` converted to response-signal flow; hostile `custom_font_family` sanitized at source (startup-crash vector via restored backup); wizard PIN entry capped at the lock screen's `_MAX_PIN` (longer settable-but-unenterable PIN = lockout).
- **Power/fingerprint/WiFi** — fired long-press no longer followed by instant blank; fingerprint node auto-detected from sysfs (`FP_INPUT_DEV` override, event3 fallback); WiFi PSK moved off nmcli's command line into a 0600 passwd-file.
- **Shade/sliders** — one subprocess per drag (200ms trailing debounce) instead of per tick; single getter batch per expand; dead Notify signal subscription replaced by an honest external-daemon probe.
- **Docs/env** — burgundy added to docs/config.md theme list; README scripts layout completed; stale `contracts/` gitignored; venv gate repaired (suite runs again on this box).
- **Test infra** — hud module tests made collection-order-independent; fake-gi seams pinned where tests drove imported modules.

Device-gated follow-ups from this workstream: smoke-test `nmcli connection up … passwd-file` on a real NM; generate the wlr protocol module on device (`python -m pywayland.scanner`) or vendor it; exercise MM1 accept/hangup against a live modem; verify fp node detection names on hardware.

---


## Tablet verification checklist (needs the x86 tablet, stable login)

`dr3k@192.168.1.129` — user is now in `input`; needs a clean session. **This is the highest-value work the moment the tablet is up**: it's the only thing standing between "19–24 pass their unit tests" and "19–24 actually work". Run through in one sitting and file fixes as found:

- [ ] lisgd running and bound to the touchscreen (`FTSC1000`, detected by `INPUT_PROP_DIRECT`) — system gestures work **over a running app**, not just on home
- [ ] shade opens full-width; apps auto-maximize (`sm.puri.phoc auto-maximize` GSetting applied)
- [ ] power key: short press blanks/wakes, long-press → power menu, menu is full-screen (20.2)
- [ ] keyboard: appears on entry tap only, hides on tap-outside (20.1), Colemak layout active, terminal/email/url purpose variants switch
- [ ] switcher lists/activates/closes real windows against live phoc toplevels (19) — the fake-protocol tests prove the wiring, not the protocol handshake
- [ ] HUD appears on volume/brightness keys and auto-hides (21) — first real layer-shell OVERLAY test for `hud.py`
- [ ] per-device phoc.ini picks the tablet fragment: `DSI-1` at scale **1.5**, not the FP5's 2.5 (20.5)
- [ ] first-boot wizard fits the 1200×1920 panel at scale 1.5
- [ ] every surface readable on a **light** theme (paper/mist) — the WS22 sweep was verified in tests and locally, never on the panel
- [ ] GDM greeter types Colemak after the install.sh OSK step (24)
- [ ] config hot-reload: edit `~/.config/xx-wm/config.json` over SSH, watch theme/slots apply live

## Device-gated — needs a phone (do not attempt without one)

- **Fairphone 5 bring-up** — image downloaded, `flash.sh` ready; needs the phone in fastboot. Then the full checklist in `devices/fairphone-5/notes.md`: output name/scale, evdev nodes, IIO sensors, WiFi-SSH deploy loop (USB data drops while charging), telephony (2G/3G calls + SMS via ModemManager — VoLTE not working on pmOS; use the FLX1 for that).
- **FLX1 recovery + bring-up** — phone currently sits in MediaTek Preloader/BROM mode; recover via mtkclient first. Then the `devices/furiphone-flx1/notes.md` open questions: can the Phosh session be replaced cleanly; mmcli vs FuriOS custom modem layer; gtk4-layer-shell availability; the "vd" Android container vs a non-Phosh shell. **Do not `apt upgrade`** the held packages until FuriLabs fixes the systemd 261-rc3 `systemd-sysusers` postinst breakage (workaround on record: `|| true` in the failing `.postinst`, then `dpkg --configure -a`).
- **Librem 5 session swap** — replace Phosh in place; also the performance-baseline canary (weakest hardware in the matrix).
- **Telephony verification** — call UI / dialer / SMS against a real modem (FLX1 for VoLTE); DnD ring suppression (starred/repeat-caller exceptions) end-to-end; ringtone loop stop on every terminal call path.
- **Runtime integration** — lisgd threshold calibration per panel; squeekboard on-screen typing + purpose-variant switching in real apps; fingerprint (FLX1 — FP5's doesn't work on pmOS); ALS auto-brightness tile on hardware with a real sensor.
- **Waydroid + microG** (17.6/17.7) — install is scripted; everything after `waydroid init` is device work: microG image (waydroid_script/MinDroid — document exact steps in `devices/` notes), sign-in, install YouTube Music, Synology Drive/Photos/Chat, Google Calendar + Gmail (decision: via microG, not GNOME Online Accounts). Verify Waydroid-exported `.desktop` entries surface in our drawer.
- **Skippy over Tailscale** (17.4) — verify the PWA `.desktop` entry against a live tailnet on-device.
- **Browser check at port time** — Waterfox arm64 Linux build exists? (historically x86-only) → else Firefox ESR + `mobile-config-firefox` stands.

## Blocked — needs the user (do NOT attempt)

- **piercing-dots phone profile** — separate agent, separate repo. This repo only consumes `install.sh --profile phone` via `scripts/bootstrap-dots.sh` (currently a loud stub — that's intentional).
- **Snapshot thumbnails in the switcher** — only if the user reconfirms, against their minimalism directive (19.3). Capture-on-leave + cache is the path if they do.
- **Publishing/releases** — none until first phone boot.
- **Hyprland/Hyprgrass migration** — parked until Hyprgrass matures (README notes the intent; no work now).

## Device facts (reference)

- **Tablet:** panel reports as `DSI-1` 1200×1920 → scale **1.5** (collides with the FP5's `DSI-1` name — hence the per-device phoc fragments). Touchscreen **FTSC1000** on `event3`, detected by `INPUT_PROP_DIRECT` (0x2), not by name. `wlopm` **not** installed (DisplayManager tracks blank state internally). `lisgd` built from source (`~mil/lisgd`), not in Arch repos.
- **phoc 0.56 / wlroots 0.20:** supports `wlr-foreign-toplevel-management`. Auto-maximize is the **`sm.puri.phoc auto-maximize` GSetting**, not a phoc.ini key. `gtk4-layer-shell` must be `LD_PRELOAD`ed before libgtk-4 or all layer surfaces silently float.
- **FLX1:** in MediaTek Preloader/BROM mode, recoverable via mtkclient. FuriOS systemd 261-rc3 `systemd-sysusers` breaks apt postinsts (pipewire, wpasupplicant) — hold upgrades.
- **FP5 (pmOS):** VoLTE not working; speaker/mic fragile; fingerprint dead; USB data drops while charging → WiFi SSH only; default user `user`.

## Suggested order

25.1–25.6 — done, landed 2026-08-23 → **tablet checklist in one sitting the moment the tablet has a stable login** — that's the real gate on 19–24, and every unit test in the repo is a proxy for it. Device-gated work starts whenever a phone is in hand: FLX1 recovery first (it's the telephony device), FP5 flash second.
