# Piercing WM — Design Spec

This document is the UI contract for Piercing WM: every surface, theme, and gesture the shell provides. Anything the shell draws must conform to it.

## Design language — PiercingXX

Text-first. No icon grids, no app icons on home. Low visual noise, local-only customization, monochrome surfaces, large type, everything reachable by search or gesture.

## Home screen

- Up to **8 home slots**; each slot holds an app, a pinned shortcut, or a **folder**.
- Default layout (seeded on first boot, never overwrites user config):
  - **Notes** → daily note (kitty + nvim via piercing-dots)
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
- Sort modes: default, A–Z, A–Z incognito, install date, size, usage frequency.
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
| Swipe up | Home / app switcher (system-level, via lisgd) |
| Edge swipe left/right | Back (system-level, via lisgd) |

System-level gestures (swipe up, edge swipes) belong to lisgd + IPC because they must work over any app. On-surface gestures (left/right/down/double-tap on home) are GTK gesture recognizers handled in-process by the launcher.

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

Versioned JSON export covering: home slots, folders + membership, pins, widget config + tap actions, theme, hidden apps, gestures, prefs. Restore never writes on invalid payload. A phone reflash should restore the launcher in one file.

## System surfaces

Piercing WM *is* the system UI, so the shell owns every surface beyond the launcher. These extend the same design language and already exist in `launcher/src/`:

lock screen (6-digit PIN, fingerprint when hardware supports), notification shade + daemon, quick settings tiles, app switcher, call UI + dialer + SMS + contacts, first-boot wizard, power menu, volume/brightness HUD (wob), virtual keyboard (wvkbd), display/power management.

## Non-goals

Icon packs, wallpaper images, widgets from third-party apps, animations beyond functional reveals, desktop multi-window tiling.
