# Piercing WM — launcher

GTK4/libadwaita launcher + shell surfaces for Linux phones. Despite the "WM" name, this is a *shell*, not a window manager — it runs on top of phoc (which does the window management) as a full Wayland session replacement (no GNOME, no Phosh). Text-first, gesture-driven, monochrome — the PiercingXX design language; `../design.md` is the parity contract with the Android launcher.

## What's built

- **Home surface** (`window.py`) — BOTTOM layer, full-screen. Pinned app slots (edit mode, +/− pin), clock/date, battery+network status strip, search, swipe nav to Apps/Settings.
- **Lock screen** (`lock_screen.py`) — OVERLAY layer, 6-digit PIN keypad, shake on wrong PIN, 30s clock refresh.
- **Notification shade** (`notification_shade.py`) — TOP layer, slide-down reveal, in-process notification daemon (`notif_daemon.py`), tap-to-launch, swipe-to-dismiss, quick actions embedded.
- **App switcher** (`app_switcher.py`) — TOP layer, slide-up reveal, swipe card up to kill app.
- **Quick actions** (`quick_actions.py`) — WiFi, BT, mobile data, airplane, torch, sliders for brightness and volume.
- **Call UI** (`call_ui.py`) — OVERLAY, incoming/active call, mute, speakerphone (pactl UCM), timer, in-call bar.
- **Dialer** (`dialer.py`) — 12-key keypad, US number formatting, mmcli voice call.
- **SMS** (`sms.py`) — Conversation bubble view, send via mmcli.
- **First-boot wizard** (`first_boot.py`) — PIN setup, theme, timezone.
- **IPC server** (`ipc.py`) — Unix socket at `$XDG_RUNTIME_DIR/piercing-shell.sock`.
- **Modem monitor** (`modem_monitor.py`) — ModemManager DBus watcher for call events.
- **Back arrow overlay** (`back_gesture.py`) — visual feedback only; gesture detection is lisgd's job, delivered via `gesture.*` IPC commands.
- **Sounds** (`data/sounds/`) — ringtone + notification sounds (wiring: todo.md Phase 2).

## What's not done yet

- Android-launcher parity gaps — 8-slot home model, widgets config, `!` web search, rename labels, theme presets, JSON backup (`../todo.md` Phase 1)
- Device bring-up: evdev paths, IIO sensor path, wlopm output name (Phase 3)
- lisgd gesture service wiring, wob HUD, wvkbd keyboard (Phases 2–3)
- Telephony verification on device (Phase 3; VoLTE testing on the FLX1 in Phase 4)
- App switcher live window list — blocked on `wlr-foreign-toplevel-management-unstable-v1` in phoc
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

## Deploy to device

```bash
# WiFi SSH (USB data drops while charging on the FP5)
export PIERCING_DEVICE=<device-ip>
export PIERCING_USER=user   # pmos default; check per device
./scripts/deploy.sh
```

`deploy.sh` rsyncs `src/` to the device and restarts the shell service. (Script written in Phase 3, after first SSH.)

## Device checks to run first

Confirm the device has the required packages over SSH (`apk` on postmarketOS, `apt` on PureOS/FuriOS):

```bash
apk search gtk4-layer-shell        # or: apt list gtk4-layer-shell
pactl info | grep -i server
ps -p 1                            # systemd or OpenRC?
```

## Session config

- Wayland session: `wayland-sessions/piercingos.session`
- Session launcher: `libexec/piercing-session` (phoc wrapper, sets GTK_THEME from config)
- systemd user service: `share/systemd/user/piercing-shell.service` (`Restart=on-failure`) — OpenRC variant needed for postmarketOS default images
- phoc.ini: display scale per device (`devices/*/notes.md`); currently set for the FP5 (2.5)

## Fonts

Space Mono (default), JetBrains Mono, JetBrains Mono Nerd (parity spec). Install per distro; overlay dir fallback for Space Mono.
