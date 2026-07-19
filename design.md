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

lock screen (6-digit PIN, fingerprint when hardware supports), notification shade + daemon, quick settings tiles, app switcher, call UI + dialer + SMS + contacts, first-boot wizard, power menu, volume/brightness HUD (wob), virtual keyboard (squeekboard with the PiercingXX Colemak layouts), display/power management.

### Lock screen

- **Swipe up to unlock.** No PIN set → an upward swipe unlocks directly. PIN set → the swipe reveals the keypad (keypad is not shown until the swipe).
- 6-digit PIN with escalating lockout; fingerprint when hardware supports it.
- **Notifications on the lock screen**: a text list of app name + summary (no bodies, no actions), fed by the shell's notification daemon. Config `lock_screen_notifications`: `summary` (default) / `count` / `off`. Hidden while Do Not Disturb is active. Tapping one prompts unlock, then opens the shade.

### Notification shade & quick settings

- **Header**: date + time on the left; tapping it expands an inline text-first month calendar (no events — just the month). A **Settings** entry on the right opens the Settings page and collapses the shade.
- **Tiles**: WiFi, Bluetooth, Data, Airplane always visible; Torch, DnD, Focus, Auto-brightness, Location, Hotspot in the expanded tier. Hardware-gated tiles stay hidden until the device supports them.
- **Brightness and volume sliders** in the expanded tier.
- Notification list below: swipe to dismiss, clear all.

### Do Not Disturb

Modeled on the Pixel's DnD. One toggle silences notification sounds and banners; notifications still collect silently in the shade. Exceptions that always get through: alarms, **repeat callers** (same number calling twice within 15 minutes), and **starred contacts**. Optional schedules (days + start/end time). While active, the lock screen shows no notifications. State is config-backed so the notification daemon and call UI can consult it.

### Focus Mode

Modeled on the Pixel's Focus Mode. The user picks a list of distracting apps; while focus is active those apps render dimmed with a "paused" note on home and in the drawer, launching them is blocked, and their notifications are held silently and delivered when focus ends. **Take a break** pauses focus for 5/10/15 minutes, then auto-resumes. Optional schedule, sharing the DnD schedule machinery. Focus and DnD are independent toggles.

### Settings scope

The in-shell Settings page is **system-only**: the things a config file can't own — WiFi networks, Bluetooth pairing, cellular/APN, sound devices, battery/power, system updates, backup/restore actions, about. It replaces both GNOME Settings and phosh-mobile-settings for everything that applies to this shell. Every *shell* preference — theme, fonts, text size, alignment, home slots, widgets, gestures, sounds, DnD/Focus rules — lives in `~/.config/piercing-shell/` and is edited there (or through dedicated surfaces like home edit mode). The shell hot-reloads the config file, so editing it in a terminal is a first-class workflow.

## Non-goals

Icon packs, wallpaper images, widgets from third-party apps, animations beyond functional reveals, desktop multi-window tiling.
