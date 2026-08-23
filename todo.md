# XX-WM — Work Order (Skippy)

You are Skippy, working on **XX-WM**: a minimalist, text-first Wayland launcher/shell for Linux phones. Read `README.md` (identity), `design.md` (the UI spec — treat it as the contract), and `launcher/README.md` (code layout) before touching anything.

**Workstreams 19–24 run on the dev machine — no phone required.** The tablet checklist needs the x86 test tablet; device-gated work is quarantined at the bottom; do not attempt it.

## Ground rules

- **Spec**: `design.md` wins. Where this file and design.md disagree, design.md is right; flag the conflict in your commit message.
- **Style**: Python 3.12+, GTK4/libadwaita via `gi`, match the existing code's idiom. No comments unless the WHY is non-obvious. Text-first UI: no icon grids, no images, monochrome per theme.
- **CSS invariants** (`launcher/src/style.css`): uniform background from the active theme on every surface; children transparent; no borders anywhere except inside `.settings-page`; the **configured font applies launcher-wide** via `font_theme.apply_global_font` (default JetBrains Mono Nerd — surfaces must not hardcode a family); invisible Paned separators. Don't regress these.
- **Verify before every commit**: `sh scripts/check.sh` (py_compile + ruff + pytest + shellcheck — 255 passing as of 2026-08-23). If you add a runtime behavior, run the shell locally (`cd launcher && PYTHONPATH=src python3 src/main.py` — it falls back to a window when layer-shell is absent) and exercise the flow.
- **Commits**: one commit per task or coherent group, imperative subject, body says what changed and how it was verified. Never commit `__pycache__`, `build/`, or `devices/*/downloads/` (gitignored).
- **Config compatibility**: `~/.config/xx-wm/config.json` may exist from earlier runs (and auto-migrates from `piercing-shell`). Every schema change needs a silent migration path (missing keys → defaults; never crash on old configs). `docs/config.md` is the public API reference — update it with every key change.
- **Decisions already made** (don't relitigate): the product is **XX-WM** (renamed 2026-08-17; app id `io.piercingxx.XXWM`, binaries `xx-wm`/`xx-wm-ipc`/`xx-wm-session`); phoc is the compositor; lisgd owns system-level gestures via IPC; the keyboard is **squeekboard** with the PiercingXX Colemak layouts; the lock screen stays ours (no phrog/phosh code, ever); DnD and Focus Mode copy the **Pixel's** behavior; the in-shell Settings page is **system-only** — every shell preference lives in `~/.config/xx-wm/`; backgrounds are solid colors only, never wallpaper; `aura` stays as a Linux-only bonus theme; Android-launcher parity syncs (2026-07-20/21) are already folded into design.md — design.md is current.
- **Minimalism directive**: the user has said the test tablet is underpowered — keep everything lightweight. Prefer text over textures, skip caches/preloads/snapshots unless explicitly reconfirmed.

## Status — where things stand (2026-08-18)

Workstreams 1–18 of the original build plan are **done** (see git history for detail): 8-slot home model with inline folders and edit mode; one-handed drawer (85% sheet, bottom search/results, long-press menus, `!` web search); 8 themes + custom solid color; fonts incl. custom import; config-driven widget row with Open-Meteo weather; JSON backup/restore (validate-before-write); sounds (ringtone/notify via paplay); gesture dispatch incl. `launch:<app_id>`; install.sh/deploy.sh/apps.sh + OpenRC & systemd units; pytest suite + `check.sh` gate; lock screen v2 (swipe unlock, notifications); shade v2 (date/time header, inline calendar, Settings entry); Pixel-model DnD and Focus Mode; system-only Settings page (WiFi/BT/sound/battery via NM/BlueZ/pactl/UPower) with config hot-reload and `docs/config.md`; squeekboard + Colemak layouts vendored and session-integrated; default app set scripting; first-boot walkthrough (`xx-wm --welcome`).

First real on-device session (x86 Arch tablet + FLX1, 2026-07-21) landed a pile of fixes — layer-shell `LD_PRELOAD`, auto-maximize GSetting, `INPUT_PROP_DIRECT` touchscreen detection, power-key ownership, DisplayManager wake — and surfaced the gaps below. **Everything that follows is the remaining work.**

Branch note: `device-testing-fixes` is ~10 commits ahead of `master` with a clean tree — see 23.1.

---

## Workstream 19 — App switcher: real window list (top priority)

RESOLVED (19): the switcher (`app_switcher.py`) used to focus windows with `wmctrl` — an **X11 tool that does nothing under Wayland** — and had no real window source; it now populates from `toplevel_manager.py`. The foundation was **verified on-device**: `python-pywayland` speaking `wlr-foreign-toplevel-management-unstable-v1` (protocol XML at `/usr/share/wlr-protocols/unstable/`, plus core `wayland.xml`) connects to phoc 0.56 and can list/activate/close toplevels.

- [x] **19.1 `toplevel_manager.py`** — pywayland client on a GLib fd-watch (never block the GTK loop): `list() -> [Toplevel(app_id, title, handle)]`, `activate(handle)`, `close(handle)`; track state events (title/app_id changes, closed). Filter out our own `io.piercingxx.XXWM` surfaces. Graceful absence: if the compositor doesn't offer the protocol (dev runs over Phosh-less nested setups), the switcher shows an empty state instead of crashing. Vendor or locate the protocol XML robustly (repo copy under `launcher/data/` beats depending on `wlr-protocols` being installed).
- [x] **19.2 Wire into `app_switcher.py`** — replace the pid-based `AppInfo`/`wmctrl`/kill plumbing: cards populate from `toplevel_manager.list()`, tap → `activate()`, swipe-up-on-card / ✕ → `close()`. Keep the existing IPC verbs (`switcher.show/hide`, `gesture.switcher`) working. Refresh the list on every show.
- [x] **19.3 Style: minimal text cards** — per the minimalism directive, cards are **app name + title text only** — no snapshots, no icons. (User once floated grim snapshot thumbnails; do **not** build that unless they reconfirm — capture-on-leave + cache is the snapshot path if they do.) And fix the theming: `_SWITCHER_CSS` hardcodes its own palette, violating the theme invariant — derive colors from the active `ThemePreset` like every other surface (fold into the 22 sweep).
- [x] **19.4 Tests** — pure-logic coverage with a fake protocol layer: list ordering, own-app filtering, closed-handle pruning, activate/close dispatch.

## Workstream 20 — Session & input fixes (device bring-up fallout)

- [x] **20.1 Keyboard: hide on tap outside a text entry** — squeekboard stays up after focus leaves a field. Hide it (`sm.puri.OSK0 SetVisible false` over D-Bus, or drop input-method focus) when a tap lands outside any editable widget. Belongs in the shell's focus handling, not per-surface hacks.
- [x] **20.2 Fix the floating power menu** — the shade's ⏻ opened a *floating* window instead of full-screen. `power_menu.py` has layer-shell code and the wrapper applies `LD_PRELOAD`, so on a stable login it *should* work — verify on a session that stays up; if it still floats, compare its layer-shell init/timing against `notification_shade.py` (which works) and fix the ordering.
- [x] **20.3 `install.sh`: add the shell user to the `input` group** — real setup gap: lisgd (touchscreen) and DisplayManager (power/volume keys) read evdev directly and silently do nothing without it. This was the root cause of "gestures don't work over apps" and "power button does nothing" on the tablet. `usermod -aG input`, idempotent, with a "re-login required" note.
- [x] **20.4 App preloading → opt-in, default off** — `XX_WM_SESSION` warms swipe-bound apps into RAM (`window.py:preload_gesture_apps`); counter to minimalism on weak hardware. Gate behind a config key (`preload_gesture_apps`, default `false`), document in `docs/config.md`.
- [x] **20.5 Per-device phoc.ini** — the tablet's panel reports as `DSI-1` 1200×1920 (scale 1.5) and **collides with the FP5's `DSI-1`** (scale 2.5); phoc.ini is currently hardcoded for the FP5. Make the output scale device-selected: ship per-device phoc.ini fragments (or one templated file) chosen by install.sh (prompt or detect), documented in `devices/*/notes.md`.

## Workstream 21 — Volume/brightness HUD (RESOLVED — in-shell HUD shipped, wob dropped)

RESOLVED (21): `install.sh` installed `wob` and README listed it in the stack — but nothing launched it and nothing fed it. Volume keys (`display_manager.py` → pactl ±5%) and brightness changes had zero on-screen feedback outside the shade. 21.1 chose the in-shell HUD; wob is gone from install.sh and the README stack.

- [x] **21.1 Decide: wob or in-shell HUD** — wob is a C overlay bar fed by a FIFO; alternatively a tiny in-shell layer-shell overlay (text-first `Vol 45%` label, auto-hide) matches the design language better and drops a dependency. Pick one (lean in-shell per the design language; flag the choice in the commit), update README/stack table accordingly.
- [x] **21.2 Wire it** — on volume key press (`display_manager.py`) and brightness change, surface the HUD with the new level; auto-hide after ~1s. If wob: session starts it with a FIFO at `$XDG_RUNTIME_DIR/wob.sock`, writers echo percentages. If in-shell: new small module, OVERLAY layer, theme-derived colors.
- [x] **21.3 Silent absence** — no HUD backend → volume/brightness still change, no errors logged per keypress.

## Workstream 22 — Theme-invariant sweep

`app_switcher.py`, `power_menu.py`, `back_gesture.py`, `first_boot.py`, `lock_screen.py`, `call_ui.py`, `dialer.py`, `quick_actions.py`, and `home_launcher.py` all contain hardcoded hex colors (`config.py`'s preset table is legitimately full of them). Some are dark-only palettes that break on Paper/Mist.

- [x] **22.1 Audit** — for each surface, classify every hardcoded color: theme-derived already (fine), dark-only assumption (bug on light themes), or intentional (e.g. call-decline red). Produce the list in the commit body.
- [x] **22.2 Fix** — route surface backgrounds/text through the active `ThemePreset` (same mechanism `style.css`/`font_theme` use); keep intentional semantic colors (incoming-call green/red) as named constants. Verify visually on amoled + paper at minimum.

## Workstream 23 — Repo hygiene & docs drift

- [ ] **23.1 Merge `device-testing-fixes` → master** — the branch is ~10 commits ahead with a clean tree and green tests. Merge (or PR) so master reflects the rebrand; user-gated only if they want to review first — ask before pushing.
- [x] **23.2 Docs drift** — `launcher/README.md` "What's not done yet" says the switcher implementation is "in progress" (update when 19 lands) and still lists wob as stack (update per 21.1). Keep `docs/config.md` in lockstep with every config key added in 20.4/21.
- [x] **23.3 Reference-installer cleanup** — `scripts/reference/linux-phone-mod/` is vendored for porting; 9.x ports are done. Confirm nothing still needed, then either delete it or leave a one-line README note saying it's historical. `scripts/bootstrap-dots.sh` stays a loud stub until the piercing-dots phone profile lands (blocked, below).

## Workstream 24 — GDM Colemak OSK (adjacent, dev-machine)

The GDM greeter uses GNOME Shell's own OSK (JSON layouts in `/usr/share/gnome-shell/osk-layouts/`), not squeekboard — so the login screen types QWERTY even with our Colemak stack installed.

- [x] **24.1** Adapt the PiercingXX Colemak layout to GNOME's OSK JSON format (start from the user's `piercingxx-keyboard`/`furi-phone-colemak-keyboard` repos; if no GNOME-OSK variant exists, this becomes a new small repo). Install step in `install.sh` (guarded — GDM hosts only; pmOS/FuriOS phones don't run GDM, so this mainly serves the tablet).

---

## Tablet verification checklist (needs the x86 tablet, stable login)

`dr3k@192.168.1.129` — user is now in `input`; needs a clean session. Run through in one sitting and file fixes as found:

- [ ] lisgd running and bound to the touchscreen (`FTSC1000`, detected by `INPUT_PROP_DIRECT`) — system gestures work **over a running app**, not just on home
- [ ] shade opens full-width; apps auto-maximize (`sm.puri.phoc auto-maximize` GSetting applied)
- [ ] power key: short press blanks/wakes, long-press → power menu, menu is full-screen (20.2)
- [ ] keyboard: appears on entry tap only, hides on tap-outside (20.1), Colemak layout active, terminal/email/url purpose variants switch
- [ ] switcher lists/activates/closes real windows (19)
- [ ] volume keys give HUD feedback (21)
- [ ] first-boot wizard fits the 1200×1920 panel at scale 1.5 (20.5)
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
- **Snapshot thumbnails in the switcher** — only if the user reconfirms, against their minimalism directive (19.3).
- **Publishing/releases** — none until first phone boot.
- **Hyprland/Hyprgrass migration** — parked until Hyprgrass matures (README notes the intent; no work now).

## Device facts (reference)

- **Tablet:** panel reports as `DSI-1` 1200×1920 → scale **1.5** (collides with the FP5's `DSI-1` name — hence 20.5). Touchscreen **FTSC1000** on `event3`, detected by `INPUT_PROP_DIRECT` (0x2), not by name. `wlopm` **not** installed (DisplayManager tracks blank state internally). `lisgd` built from source (`~mil/lisgd`), not in Arch repos.
- **phoc 0.56 / wlroots 0.20:** supports `wlr-foreign-toplevel-management`. Auto-maximize is the **`sm.puri.phoc auto-maximize` GSetting**, not a phoc.ini key. `gtk4-layer-shell` must be `LD_PRELOAD`ed before libgtk-4 or all layer surfaces silently float.
- **FLX1:** in MediaTek Preloader/BROM mode, recoverable via mtkclient. FuriOS systemd 261-rc3 `systemd-sysusers` breaks apt postinsts (pipewire, wpasupplicant) — hold upgrades.
- **FP5 (pmOS):** VoLTE not working; speaker/mic fragile; fingerprint dead; USB data drops while charging → WiFi SSH only; default user `user`.

## Suggested order

19 (the switcher is the last missing core surface) → 20 (small, unblocks the tablet checklist) → 21 → 22 → 23 → tablet checklist in one sitting → 24 when convenient. Device-gated work starts whenever a phone is in hand — FLX1 recovery first (it's the telephony device), FP5 flash second.
