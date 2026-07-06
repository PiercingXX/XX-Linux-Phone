# Librem 5 — test device

Purism Librem 5. In hand, not yet used for Piercing WM testing.

| Feature | Detail |
|---|---|
| SoC | NXP i.MX 8M Quad (mainline since ~5.x) |
| GPU | Vivante GC7000Lite (etnaviv, mainline) |
| RAM / storage | 3 GB / 32 GB eMMC |
| Display | 5.7" IPS, 720×1440, ~283 PPI → scale ~1.75–2 |
| OS | PureOS (Debian derivative), ships **Phosh on phoc** |
| Kernel | mainline (Purism-maintained patches) |
| Killswitches | Hardware: WiFi/BT, modem, camera/mic |

## Why it matters here

- Already runs phoc — Piercing WM replaces Phosh *in place*: install launcher, point the session at our `piercing-session`, done. No flashing required.
- apt-based, so it exercises the Debian path of our setup scripts (FuriOS is also Debian-based).
- Weakest hardware of the three — the performance canary. If the launcher is smooth here, it's smooth everywhere.

## To verify on device (Phase 4)

- [ ] PureOS GTK4/libadwaita/gtk4-layer-shell versions (`apt list --installed | grep -E 'gtk4|adwaita|layer-shell'`)
- [ ] Session mechanism (wayland-sessions entry vs phosh.service override)
- [ ] Output name (`wlr-randr`), evdev nodes (`libinput list-devices`), IIO sensors (`ls /sys/bus/iio/devices/`)
- [ ] Modem stack: ModemManager (expected — our surfaces target mmcli)
