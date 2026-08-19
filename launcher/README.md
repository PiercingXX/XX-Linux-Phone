# XX-WM — launcher

GTK4/libadwaita launcher + shell surfaces for Linux phones. Despite the "WM" name, this is a *shell*, not a window manager — it runs on top of phoc (which does the window management) as a full Wayland session replacement (no GNOME, no Phosh). Text-first, gesture-driven, monochrome — the PiercingXX design language; `../design.md` is the UI spec.

## What's built

- **Home surface** (`window.py`, `home_launcher.py`) — BOTTOM layer. 8-slot home model with inline folder drop-downs, long-press edit mode, config-driven widget row (time/date/weather/battery with tap actions), gesture dispatch (`launch:<app_id>` bindings), config hot reload.
- **App drawer** (`window.py`, `app_index.py`) — ~85% bottom sheet, bottom search with bottom-anchored results, A–Z jump strip, A–Z ↔ install-date sort, pinned-first ordering, inline folder rows, `!` web search, auto-launch option, long-press action menus (`app_item_actions.py`).
- **Lock screen** (`lock_screen.py`) — OVERLAY layer, swipe-up unlock revealing the PIN keypad (4+ digits), escalating lockout, fingerprint, notification list (summary/count/off, DnD-aware).
- **Notification shade** (`notification_shade.py`) — TOP layer, date/time header with inline month calendar (`calendar_grid.py`), Settings entry, in-process daemon (`notif_daemon.py`), tap-to-launch, swipe-to-dismiss, clear all, quick actions embedded.
- **DnD & Focus** (`dnd.py`, `focus_mode.py`) — Pixel-model Do Not Disturb (schedules, starred contacts, repeat callers) and Focus Mode (paused apps, held notifications, take-a-break), wired through tiles, calls, and the notification path.
- **Quick actions** (`quick_actions.py`) — WiFi, BT, mobile data, airplane, torch, DnD, Focus; brightness/volume sliders; hardware-gated tiles hide themselves.
- **Sounds** (`sound.py`, `data/sounds/`) — ringtone loop + notification sound via paplay/pw-play, gated by config, DnD, mutes.
- **Weather** (`weather.py`) — Open-Meteo current conditions, 15-min cache, silent offline fallback.
- **System settings** (`system_settings.py`) — WiFi scan/connect (nmcli), Bluetooth scan/pair (BlueZ D-Bus), sound output picker (pactl), battery (UPower); the settings page is system-only — every shell preference lives in `~/.config/xx-wm/` (`../docs/config.md`).
- **Backup** (`backup.py`) — versioned JSON export/restore with validate-before-write.
- **Call UI / Dialer / SMS** (`call_ui.py`, `dialer.py`, `sms.py`) — mmcli/ModemManager telephony surfaces; dialer rows star contacts for DnD.
- **First-boot wizard** (`first_boot.py`) — PIN, theme, timezone, then the interactive gesture walkthrough (replay with `xx-wm --welcome`).
- **IPC server** (`ipc.py`) — Unix socket at `$XDG_RUNTIME_DIR/xx-wm.sock` (`lock`, `shade.*`, `switcher.*`, `gesture.*`, `welcome`).
- **Modem monitor** (`modem_monitor.py`) — ModemManager DBus watcher for call events.
- **Back arrow overlay** (`back_gesture.py`) — visual feedback only; gesture detection is lisgd's job, delivered via `gesture.*` IPC commands.

## What's not done yet (device-gated)

- Device bring-up: flashing, evdev paths, IIO sensor path, wlopm output name (`../devices/*/notes.md`)
- lisgd/wob/squeekboard runtime verification, gesture threshold calibration, telephony testing
- Waydroid init + microG + Android app installs (`../todo.md` Workstream 17.6/17.7)
- App switcher live window list — phoc *does* implement `wlr-foreign-toplevel-management-unstable-v1` (verified on phoc 0.56 / wlroots 0.20); implementation in progress via `python-pywayland` (list/activate/close) plus `grim` snapshot caching for Android-style recents
- Performance baseline — needs device testing (Librem 5 is the canary)

## Local build (dev machine)

```bash
# Install deps (Arch)
sudo pacman -S python-gobject gtk4 libadwaita gtk4-layer-shell meson ninja

# Run directly (no layer-shell compositor needed)
cd launcher
PYTHONPATH=src python3 src/main.py

# Build + install
meson setup build --prefix=/usr
meson install -C build
```

## Tests & the pre-commit gate

`scripts/check.sh` is the gate: py_compile + ruff + pytest + shellcheck.
pytest needs PyGObject, so the venv must see system site packages:

```bash
python3 -m venv --system-site-packages .venv   # .venv is gitignored
.venv/bin/pip install pytest ruff
PATH="$PWD/.venv/bin:$PATH" sh scripts/check.sh
```

shellcheck comes from the distro (`pacman -S shellcheck` / `apk add
shellcheck`); the gate skips any tool that isn't on PATH rather than fail.

## Deploy to device

```bash
# WiFi SSH (USB data drops while charging on the FP5)
export PIERCING_DEVICE=<device-ip>
export XX_WM_USER=user   # pmos default; check per device
./scripts/deploy.sh
```

`deploy.sh` rsyncs `src/` to the device and restarts the shell service (systemd user unit or OpenRC). `--dry-run` prints the commands without touching anything. Full installs go through `scripts/install.sh` (whiptail menu: Install / Update / Install phone apps).

## Device checks to run first

Confirm the device has the required packages over SSH (`apk` on postmarketOS, `apt` on PureOS/FuriOS):

```bash
apk search gtk4-layer-shell        # or: apt list gtk4-layer-shell
pactl info | grep -i server
ps -p 1                            # systemd or OpenRC?
```

## Session config

- Wayland session: `wayland-sessions/xx-wm.desktop` (display managers only scan `*.desktop`)
- Session launcher: `libexec/xx-wm-session` (phoc wrapper, sets GTK_THEME from config); the in-session `bin/xx-wm` starts squeekboard before the shell
- systemd user service: `share/systemd/user/xx-wm.service` (`Restart=on-failure`); OpenRC: `data/openrc/xx-wm` → `/etc/init.d/` (postmarketOS default images)
- Keyboard layouts: `data/squeekboard/` (PiercingXX Colemak, incl. terminal/email/url variants) → `datadir/xx-wm/squeekboard`, symlinked by install.sh to `~/.local/share/squeekboard/keyboards/`
- phoc.ini: display scale per device (`devices/*/notes.md`); currently set for the FP5 (2.5)

## Fonts

JetBrains Mono Nerd (default), Space Mono, JetBrains Mono, System Light, plus user-imported custom fonts (`config.install_custom_font`). Install per distro; overlay dir fallback.
