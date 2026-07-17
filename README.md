# Piercing WM

**The PiercingXX Launcher, rebuilt as a Wayland window manager + launcher for Linux phones.**

> **"WM" is the name, not the architecture.** Piercing WM is in fact a *shell*: the phoc compositor does the actual window management, and Piercing WM provides every surface drawn over it (home, lock screen, shade, switcher, call UI, …) — the same relationship Phosh has to phoc.

GNOME Mobile and Phosh are not the answer. This repo replaces them: a text-first, monochrome, gesture-driven shell that boots straight into the PiercingXX design language on real Linux phones. The [PiercingXX Android launcher](https://github.com/PiercingXX/PiercingXX-launcher) is the reference implementation — its home model, drawer, themes, and gestures define what we build here. Android is the prototype; this is the real thing.

<table>
<tr><td><b>Product</b></td><td>Piercing WM — compositor session + GTK4 layer-shell launcher</td></tr>
<tr><td><b>Reference UI</b></td><td>PiercingXX Launcher v6.5 (Android) — see <code>design.md</code></td></tr>
<tr><td><b>Compositor</b></td><td>phoc today (all test phones ship it); Hyprland when Hyprgrass matures</td></tr>
<tr><td><b>Stack</b></td><td>Python + GTK4/libadwaita + gtk4-layer-shell, lisgd gestures, wob HUD, wvkbd keyboard</td></tr>
<tr><td><b>Ecosystem</b></td><td><a href="https://github.com/PiercingXX/piercing-dots">piercing-dots</a> for the terminal/dotfile layer; <a href="https://github.com/PiercingXX/debian-mini-mod">debian-mini-mod</a> minimal-install patterns</td></tr>
</table>

## What this is (and isn't)

- **It is** a launcher + shell session: home screen, app drawer, lock screen, notification shade, app switcher, quick settings, call UI, dialer, SMS — every surface a GTK4 layer-shell window over a wlroots compositor.
- **It is** device-agnostic. Phones are test targets, not the product.
- **It isn't** a distro. It installs onto whatever mobile OS the phone runs (postmarketOS, PureOS, FuriOS). Base OS is a dependency.
- **It isn't** GNOME/Phosh with a theme. No GNOME Shell, no libhandy app grid, no icon grids anywhere.

## Test devices

| Device | OS | Kernel | Role |
|---|---|---|---|
| **Fairphone 5** | postmarketOS (Alpine) | mainline 6.15 | Primary bring-up target — image downloaded, flash pending (`devices/fairphone-5/`) |
| **Librem 5** | PureOS | mainline | Second target — already runs phoc/Phosh, ideal for replacing Phosh in place (`devices/librem-5/`) |
| **Furi Phone FLX1** | FuriOS (Debian) | Halium-based | Third target — daily-driver-grade telephony incl. VoLTE (`devices/furiphone-flx1/`) |

All three ship a phoc-based stack, so one launcher codebase covers the whole matrix. Device directories hold only flash/setup scripts and hardware notes.

## Repo layout

```
launcher/          ← the product: GTK4 layer-shell launcher + session files
  src/             ← all surfaces (window.py, lock_screen.py, notification_shade.py, …)
  data/            ← phoc.ini, session files, systemd user service
design.md          ← UI spec distilled from the Android launcher — the parity contract
todo.md            ← the build plan
scripts/           ← piercing-dots bootstrap + shared device setup helpers
devices/           ← per-phone flash scripts and hardware notes (not the product)
```

## Read first

1. `design.md` — what we're building (launcher parity spec)
2. `todo.md` — how we get there
3. `launcher/README.md` — code layout, local run, deploy

## License

GPL-3.0, matching the Android launcher.
