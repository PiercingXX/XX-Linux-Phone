# Piercing WM — Design Spec

The [PiercingXX Android launcher](https://github.com/PiercingXX/PiercingXX-launcher) (v6.5) is the reference implementation. This document is the parity contract: what the Linux launcher must match, adapted where Android and Wayland genuinely differ. Facts below are taken from the launcher source (`LauncherThemePreset.kt`, `LauncherBackground.kt`, `LauncherFont.kt`, `DefaultLayoutBootstrapper.kt`, `Constants.kt`, README).

## Design language — PiercingXX

Text-first. No icon grids, no app icons on home. Low visual noise, local-only customization, monochrome surfaces, large type, everything reachable by search or gesture.

## Home screen

- Up to **8 home slots**; each slot holds an app, a pinned shortcut, or a **folder**.
- Default layout (seeded on first boot, never overwrites user config):
  - **Notes** → note-taking app (Android: Keep; Linux: kitty + nvim daily note via piercing-dots)
  - **Audio** (folder) → Audiobooks, Music
  - **Comms** (folder) → Phone, Text, Email
  - **Tools** (folder) → Internet, Camera, Calculator, Photos
  - Members that don't resolve to an installed app are skipped; empty folders are not created.
- Widgets above the slots: **time, date, battery, weather** — individually toggleable, manually orderable, each with a configurable tap action (open default app / refresh weather / open chosen app).
- Alignment configurable (left/center/right); default **centered**.
- Long-press on home → configuration (slot editing), not a wallpaper picker.

## App drawer

- Full-screen text list, **search auto-focused** on open.
- Search can **auto-launch the single result**; `!query` falls back to web search.
- Sort modes: default, A–Z, A–Z incognito, install date, size. (Linux adds: usage frequency — already built.)
- A–Z character jump strip on the right edge.
- **Hidden apps**: hideable per-app; optional hiding of home items from search and folder members from the main drawer.
- Per-app rename labels (folder members show their renamed label everywhere).
- Drawer long-press → pin/hide/rename/uninstall actions.

## Folders

Create, rename, delete, manage membership, manual reorder. Folder opens as a text list overlay, same typography as home.

## Gestures

| Gesture | Action |
|---|---|
| Swipe left on home | Launch configured app (default: Camera) |
| Swipe right on home | Launch configured app (default: Phone) |
| Swipe down | Notifications **or** search (user choice) |
| Double-tap | Lock screen |
| Swipe up | (Linux, system-level) Home / app switcher — via lisgd |
| Edge swipe left/right | (Linux, system-level) Back — via lisgd |

On Linux the system-level gestures (up/edges) belong to lisgd + IPC; the on-surface gestures (left/right/down/double-tap on home) stay GTK gesture recognizers, exactly as the Android launcher handles them in-process.

## Themes

Six presets + custom solid colors. Backgrounds are **solid colors only** — never wallpaper images.

| Preset | Mode | Background |
|---|---|---|
| AMOLED (default) | dark | `#000000` |
| Graphite | dark | `#111827` |
| Forest | dark | `#10261B` |
| Ocean | dark | `#0F1C2E` |
| Paper | light | `#F3EEE2` |
| Mist | light | `#E6EDF5` |

Extra named color: Burgundy `#2A1018`. Light/dark/system mode switch. Text size scaling and per-surface alignment.

## Fonts

Bundled options: **Space Mono (default)**, JetBrains Mono, JetBrains Mono Nerd, System Light, plus user-imported custom font. Font applies launcher-wide.

## Backup / restore

Versioned JSON export covering: home slots, folders + membership, pins, widget config + tap actions, theme, hidden apps, gestures, prefs. Restore never writes on invalid payload. (Linux: same JSON schema where possible — a phone reflash should restore the launcher in one file.)

## Linux-only surfaces (no Android equivalent)

The Android launcher lives inside SystemUI; on Linux we *are* SystemUI. These surfaces extend the same design language and already exist in `launcher/src/`:

lock screen (6-digit PIN, fingerprint when hardware supports), notification shade + daemon, quick settings tiles, app switcher, call UI + dialer + SMS + contacts, first-boot wizard, power menu, volume/brightness HUD (wob), virtual keyboard (wvkbd), display/power management.

## Non-goals

Icon packs, wallpaper images, widgets from third-party apps, animations beyond functional reveals, desktop multi-window tiling.
