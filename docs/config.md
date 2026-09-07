# XX-WM — Config Reference

Every shell preference lives in `~/.config/xx-wm/`. The config file
**is the public API**: edit it in a terminal and the running shell hot-reloads
it (debounced; an invalid file is ignored and the last good config stays
live). The in-shell Settings page covers system things only (network, sound
devices, battery, updates, backup) — never these keys, except a dedicated
Appearance surface that writes the `theme` (and `prefer_dark`) keys so a
tablet without a keyboard can still pick a preset.

Files:

- `config.json` — everything below except gestures
- `gestures.json` — gesture → action bindings

Missing keys fall back to their defaults; unknown keys are ignored. No
migration steps are ever required.

## config.json

### Appearance

| Key | Type | Default | Effect |
|---|---|---|---|
| `theme` | string | `"amoled"` | Preset key: `amoled`, `graphite`, `forest`, `ocean`, `paper`, `mist`, `aura` (Linux bonus), `burgundy`, or `custom`. |
| `custom_background` | string | — | `#RRGGBB` used when `theme` is `custom`; shades and text color are derived. Burgundy `#2A1018` is the blessed extra. |
| `prefer_dark` | bool | `true` | Light/dark surface mode. |
| `font` | string | `"jetbrains-mono-nerd"` | `system-light`, `space-mono`, `jetbrains-mono`, `jetbrains-mono-nerd`, or `custom`. |
| `custom_font_family` | string | — | Family used when `font` is `custom`. Install via `config.install_custom_font()` or drop a font in `~/.local/share/fonts` and set both keys. Font files are not part of backups. |
| `text_size_scale` | float | `1.0` | Launcher-wide type scale, clamped 0.5–2.0. |
| `home_alignment` | string | `"center"` | `left` / `center` / `right` for home rows. |

### Home screen

| Key | Type | Default | Effect |
|---|---|---|---|
| `home_slots` | list | `[]` | Up to 8 slots: `{type: 'app'\|'folder', label, app_id, cmd, folder}`. `app_id` is a `.desktop` id; `cmd` an argv list for non-desktop entries; `folder` a list of `{label, app_id or cmd}` members. Seeded on first boot when empty. |
| `default_layout_applied` | bool | `false` | Set once seeding has run; never re-seeds. |
| `app_labels` | dict | `{}` | `{app_id: label}` renames; shown everywhere, searched alongside original names. |
| `pinned` | list | `[]` | App ids surfaced first in the drawer browse list, in this order. |
| `hidden_apps` | list | `[]` | App ids hidden from the drawer browse list (still searchable). |
| `launch_counts` | dict | `{}` | Usage counters (recorded automatically; excluded from backups). |

### Widgets

| Key | Type | Default | Effect |
|---|---|---|---|
| `widgets` | dict | all enabled | Per-widget `{enabled, order, tap}` for `time`, `date`, `weather`, `battery`. `tap` ∈ `"default"`, `"none"`, `"refresh"`, or `{"app": "<app_id>"}`. Defaults: order time→date→weather→battery; weather tap `refresh`. The clock also keeps double-tap-to-lock. |
| `weather_lat` / `weather_lon` | float\|null | `null` | Coordinates for the weather widget (Open-Meteo, no API key). Unset → the widget shows `--°`. |

### Drawer & search

| Key | Type | Default | Effect |
|---|---|---|---|
| `search_auto_launch` | bool | `false` | Launch immediately when a search narrows to one result (never for `!` queries). |
| `preload_gesture_apps` | bool | `false` | Warm swipe-bound apps into RAM at session start so gestures open a resident process instead of cold-starting (the camera through the Android HAL can be slow). Opt-in — preloading is counter to the minimalism directive; only takes effect in a real session (`XX_WM_SESSION`). |

### Sounds & notifications

| Key | Type | Default | Effect |
|---|---|---|---|
| `sound_ringtone` | bool | `true` | Loop the ringtone on incoming calls. |
| `sound_notifications` | bool | `true` | Play `notify.wav` on notifications (DnD, mutes, and `suppress-sound` still win). |
| `muted_apps` | dict | `{}` | `{app_id: until_epoch}` from "Disable for…" — notifications are discarded until the deadline. Expires silently; excluded from backups. |
| `lock_screen_notifications` | string | `"summary"` | `summary` (app + summary rows), `count`, or `off`. Hidden while DnD is active. |

### Do Not Disturb

| Key | Type | Default | Effect |
|---|---|---|---|
| `dnd_enabled` | bool | `false` | Manual toggle. Notifications collect silently; only exceptions ring. |
| `dnd_schedules` | list | `[]` | `{days: [0-6 Monday-first], start: "HH:MM", end: "HH:MM"}`; overnight ranges wrap. |
| `dnd_starred_numbers` | list | `[]` | Numbers that always ring through (star contacts by long-pressing a dialer suggestion). Repeat callers (twice within 15 min) also ring. |

### Focus Mode

| Key | Type | Default | Effect |
|---|---|---|---|
| `focus_enabled` | bool | `false` | Manual toggle; paused apps dim, won't launch, and their notifications are held until focus ends. |
| `focus_apps` | list | `[]` | App ids to pause (or use the drawer long-press "Focus: pause this app"). |
| `focus_schedules` | list | `[]` | Same shape as `dnd_schedules`. |
| `focus_break_until` | float | `0.0` | Break deadline (set by "Take a break" 5/10/15 min); transient, excluded from backups. |

### System

| Key | Type | Default | Effect |
|---|---|---|---|
| `auto_lock_timeout` | int | `120` | Idle seconds before the lock screen; `0` disables. |
| `pin_hash` | string | — | SHA-256 of the PIN (minimum 6 digits). Never exported in backups. |
| `apn` / `apn_user` / `apn_pass` | string | — | Mobile-data APN pushed to NetworkManager (Settings page owns these). |
| `update_script` | string | PiercingXX menu path | Script run by "Update system". |
| `update_last_check` / `update_snooze_until` | float | `0.0` | Daily update-check bookkeeping. |

## gestures.json

Flat `{gesture: action}`. Valid actions: `home`, `app_switcher`,
`notification_shade`, `back`, `search` (drawer + keyboard focus),
`lock_screen`, `settings`, `camera`, `dialer`, `assistant`, `none`, or
`launch:<app_id>` for any installed app (an uninstalled target behaves as
`none`).

Four slots are system-level: lisgd fires them even while an app is
focused. Their bindings are generated at session start from this file.
Values may be an IPC verb (`gesture.home`, `gesture.shade`,
`gesture.back`, `gesture.keyboard`, `gesture.switcher`) **or** the
settings-UI action names `home`, `app_switcher`, `notification_shade`,
`back`, `search` (mapped to those verbs). `launch:<app>` stays in-shell
and does not replace the lisgd verb. Two fallbacks: missing or corrupt
JSON loads schema defaults then `ACTION_TO_VERB` (short → `gesture.home`,
long → `gesture.switcher`); a **valid unmapped** value (`camera`, `none`,
`launch:…`) keeps that slot's `DEFAULT_VERBS` (legacy: short →
`gesture.keyboard`, long → `gesture.home`). Garbage keys are dropped at
load, so they take the schema-default path, not `DEFAULT_VERBS`.
Rebinding restarts lisgd immediately from Settings. If that restart
fails, it applies at the next session start.

| Gesture | Default | Notes |
|---|---|---|
| `swipe_down_top` | `notification_shade` | lisgd → `gesture.shade`. Verb-rebindable. |
| `swipe_up_short` | `app_switcher` | lisgd → `gesture.switcher`. Verb-rebindable. A normal slide up from the bottom opens recents (activate / close). Missing/corrupt JSON uses this mapped default, not `gesture.keyboard`. |
| `swipe_up_long` | `app_switcher` | lisgd → `gesture.switcher`. Same recents sheet as short — long vs short was too easy to miss on a phone. Verb-rebindable. |
| `swipe_left_edge` | `back` | lisgd → `gesture.back` on both edges. Verb-rebindable. |
| `long_press_bottom` | `search` | |
| `double_tap_home` | `lock_screen` | Kept on Linux deliberately. |
| `long_press_home` | `settings` | The shell opens slot edit mode on this. |
| `swipe_left_home` | `none` | Seeded to `launch:<skippy>` when Skippy is installed. `none` falls through to page navigation. |
| `swipe_right_home` | `camera` | |
| `squeeze` | `assistant` | Hardware-gated (Active Edge); needs device support. |
| `fingerprint_swipe` | `notification_shade` | Hardware-gated; needs device support. |
| `double_press_power` | `camera` | Hardware-gated; needs device support. |

## Not in these files

- Contacts: `~/.local/share/xx-wm/contacts.json` or GNOME Contacts VCF.
- Weather cache: `~/.cache/xx-wm/weather.json` (safe to delete).
- Logs: `~/.local/share/xx-wm/shell.log`.
- Backups: versioned JSON via Settings → Backup; see `launcher/src/backup.py` for the exact scope.
