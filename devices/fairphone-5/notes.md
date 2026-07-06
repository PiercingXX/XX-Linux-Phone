# Fairphone 5 — test device

Fairphone 5 (`fairphone-fp5`) on postmarketOS. Primary bring-up target (todo.md Phase 3). Images downloaded in `downloads/`; `flash.sh` ready — needs the phone in fastboot mode.

## Hardware

| Feature | Detail |
|---|---|
| SoC | Qualcomm QCM6490 (commercial Snapdragon 778G derivative) |
| CPU | Kryo 670 (4× Gold + 4× Silver) |
| GPU | Adreno 642L (Freedreno open-source driver, DRM/KMS via mainline) |
| RAM | 8 GB LPDDR5 |
| Storage | 256 GB UFS 3.1 |
| Display | 6.46" OLED, 2340×1080, 90 Hz, 401 PPI → scale ~2.5 |
| Fingerprint | Side-mounted (embedded in power button) — not yet working on pmos |
| Battery | 4200 mAh, 30W charging |
| USB-C | USB 3.1 Gen 1 |

## OS: postmarketOS

Alpine Linux / musl, mainline kernel 6.15.x, package manager `apk`. Device codename `fairphone-fp5`. Maintained by Luca Weiss (Fairphone engineer) — official community images for v26.06 and edge.

### Hardware status on pmos (as of 2026-06-28)

| Component | Status | Notes |
|---|---|---|
| Display / GPU | Working | Freedreno, DRM/KMS, mainline |
| WiFi / Bluetooth | Working | |
| SMS | Working | ModemManager + Chatty |
| Basic voice calls | Working | 2G/3G |
| VoLTE | **Not working** | Active work in progress — use the FLX1 for VoLTE testing |
| Speaker / mic | Fragile | Treat as unstable |
| Camera | Working (limited) | libcamera tuning files added Oct 2025 |
| Fingerprint | Not working | |
| USB data while charging | **Broken** | Drops when charger connected — use WiFi SSH |
| GPS | Working | |

**Dev implication:** WiFi SSH (`ssh user@<ip>`) for all deploy/debug. pmos default user is `user`.

## Flash procedure

1. Enable OEM unlocking in Developer Options
2. `adb reboot bootloader` (or Power + Vol-Down from off)
3. `fastboot flashing unlock` (wipes device)
4. `./devices/fairphone-5/flash.sh` — decompresses, flashes boot to both A/B slots + rootfs to userdata, reboots
5. First boot ~2–3 min into Phosh; connect WiFi, note IP, confirm SSH

## Supporting packages (apk)

`lisgd wob wvkbd wlopm grim wtype brightnessctl fprintd` plus `py3-gobject3 gtk4.0 libadwaita gtk4-layer-shell meson ninja rsync`. Verify exact names with `apk search` — Alpine naming differs from Debian.

## Open questions (answer on device, Phase 3)

1. systemd or OpenRC? (`ps -p 1`) — pmos defaults to OpenRC; determines service file format
2. Is `gtk4-layer-shell` in pmos repos? (`apk search gtk4-layer-shell`) — hard dependency
3. wlopm output name (`wlopm` with no args)
4. evdev nodes for power/volume/fingerprint (`libinput list-devices`)
5. Ambient light sensor in IIO sysfs? (`ls /sys/bus/iio/devices/`)
6. PipeWire confirmed? (`pactl info`)
7. Display manager / session mechanism (greetd? tinydm?) — where the `.session` file goes
8. lisgd touchscreen auto-discovery vs hardcoded `-d /dev/input/eventX`
9. USB-C / BT audio routing quirks under PipeWire
10. VoLTE progress (watch pmos blog + `gitlab.com/postmarketOS/pmaports`)
