# xx-find lane 5 — erase + low battery (built, never running)

Nothing here is enabled, auto-started or deployed: the unit ships with
no `[Install]` section, the agent refuses to act until enabled AND
paired (plus `erase_enabled` for erase), and tests touch only `tmp`
fixtures, injected buses and fakes — never the real network, GPS,
keyring, bus, audio or files outside `tmp`.

## 1. Erase wipe list (owner review)

Source of truth: `plan_erase()` in `src/xx_find/erase.py`. Dry-run only
lists what WOULD be deleted; it deletes nothing and is the only mode
tests and docs exercise. Real deletion needs an explicit
`dry_run=False` call, which no caller in this repo makes.

| # | Target | Kind | Note |
|---|---|---|---|
| 1 | `~/.config/xx-wm`, `~/.config/xx-find` | dir | shell + agent config |
| 2 | `~/.local/share/xx-wm`, `~/.local/share/xx-find` | dir | shell data, contacts, logs |
| 3 | `~/.local/state/xx-find` | dir | lock-screen message, agent state |
| 4 | `~/.cache/xx-wm`, `~/.cache/xx-find` | dir | weather, protocol caches |
| 5 | `~/.mozilla/firefox` | dir, if present | Firefox profiles |
| 6 | `~/.config/chromium` | dir, if present | Chromium profile |
| 7 | `~/.config/google-chrome` | dir, if present | Chrome profile |
| 8 | `~/.config/epiphany` | dir, if present | GNOME Web profile |
| 9 | `~/.config/txxt`, `~/.local/share/txxt` (messages DB + MMS store), `~/.cache/txxt` | dirs | messages, MMS store, thumbnails |
| 10 | `~/.config/xx-email`, `~/.local/share/xx-email` (mail DB + drafts + outbox), `~/.local/state/xx-email`, `~/.cache/xx-email` | dirs | mail, drafts, queued sends, attachments cache |
| 11 | `~/.config/xx-vitals`, `~/.local/share/xx-vitals`, `~/.local/state/xx-vitals`, `~/.cache/xx-vitals` | dirs | metrics DB, logs |
| 12 | `~/.config/{xx-contacts,xx-dialer,xx-calendar,xx-share,xx-captions}`, `~/.local/share/...`, `~/.local/state/...`, `~/.cache/...` | dirs, owner-reviewable | contacts DB + photos, call history, calendar, share history, transcripts; conventional XDG leaves, flagged as TODO lines in the dry-run plan until confirmed against the app sources |
| 13 | `xx-find/device-token`, `xx-find/owner-verify-key` | keystore accounts | per-item Delete via `keystore` seam |
| 14 | xx-email OAuth tokens (`application=io.piercingxx.XXEmail, account=<email>`), xx-vitals NAS token (`application=io.piercingxx.XXVitals, host=<nas-host>`) | Secret Service items | per-item `Delete` with the apps' attribute schemas |
| 15 | login keyring items owned by the user | Secret Service items | per-item `Delete` on the default collection |

Never listed, never deletable: the OS (`/`, `/usr`, `/etc`, `/boot`,
`/opt`), other users' homes, and removable media (`/media`, `/mnt`,
`/run/media`). `is_safe_target()` requires every target to resolve
inside `$HOME`, refuses `$HOME` itself, refuses symlinks (fail closed),
and refuses the forbidden roots above. `~/.local/share/keyrings` files
are documented in the plan with `action: keep` and are never removed as
files (see §2).

Double gate (unchanged from lane 4, still enforced in
`Agent._erase_confirmed`): the `erase_enabled` setting AND a typed
confirmation repeating the device id verbatim
(`erase.validate_erase_confirmation`). Either missing → the command is
refused before dispatch, after the counter is still consumed and
persisted.

## 2. Keyring decision

Picked: per-item Secret Service `Delete` on the default collection's
items (xx-find accounts through the existing `keystore` seam, login
items through an injected collection with the same `search_items` /
`Delete` shape as SecretStorage).

Rejected: removing `~/.local/share/keyrings` files after locking.
File removal races the daemon's open handles, bypasses its lock, can
corrupt unrelated collections, and deletes secrets outside this
feature's scope with no audit trail. Per-item `Delete` goes through the
daemon, so locking stays valid, each removal is scoped to the user's
default collection, and a locked or missing daemon fails closed (an
error the agent reports) instead of corrupting files.

## 3. Low-battery last location + shutdown hook

`src/xx_find/schedule.py`:

- `is_low_battery(pct, charging, warning_level=None)` gates when the
  UPower `WarningLevel` is low (3), critical (4) or action (5) — the
  `UpDeviceLevel` values for `WarningLevel` (`0` unknown, `1` none,
  `2` discharging, `3` low, `4` critical, `5` action) — or when
  `pct <= 15` and not charging. Charging always wins; unknown level
  never gates alone. The two-arg call shape from lane 4 is unchanged.
- `battery_snapshot(bus)` reads the display device `GetAll`
  (`IsPresent` / `Percentage` / `State` / `WarningLevel`; charging is
  `State == 1`) over an injected bus and never raises.
  `build_battery_probe()` keeps its `(pct, charging)` shape;
  `build_battery_snapshot_probe()` returns the full snapshot.
- `build_last_report_hook(battery_probe, read_fix, send_report,
  config, on_privacy_event)` returns a zero-arg `attempt()` that sends
  one final fix through the existing `send_report(config, fix)` path
  with `battery_pct` attached, fires the privacy event like a locate,
  and never raises (every failure returns `{"sent": False, reason}` so
  a shutdown hook never delays shutdown).
- `subscribe_shutdown_hook(bus, on_prepare)` subscribes to logind
  `PrepareForShutdown` with `sender=None` (signals arrive from logind's
  unique name) filtered on `/org/freedesktop/login1`; production calls
  the last-report `attempt()` from `on_prepare` only when no charger is
  connected, on a worker thread with a short timeout.

## 4. VM / device verification procedure (not run in this lane)

No on-device run happened here: the feature ships disabled and the
owner has not enabled it. When they do, verify in this order.

VM (QEMU + pmOS/Mobian image with xx-find installed but disabled):

1. Boot the VM with a Linux phone image, pair xx-find to a test
   server over the tailnet, enable the agent in Settings but leave
   `erase_enabled` OFF.
2. Dry-run erase: run the planner against a scratch `HOME`
   (`plan_erase(home=<scratch>)`) and compare with `format_plan`
   output. Pass: every listed dir resolves inside the scratch home,
   `~/.local/share/keyrings` appears only as `action: keep`, and no
   listed path escapes the scratch root. Nothing is deleted.
3. Double gate: queue an `erase` command with a wrong confirmation;
   confirm refusal and no deletion. Enable `erase_enabled`, repeat
   with the device id verbatim; confirm the dry-run plan is returned
   and still nothing is deleted (real wipe is never exercised in the
   VM: pass `dry_run=False` only against a disposable scratch home).
4. Low battery: stop UPower's real readings from mattering by
   pointing the probe at a fake bus is NOT the device check — on the
   VM, discharge to 15 % (or `upower -d` to read the current
   `WarningLevel`), confirm one last sealed report with `battery_pct`
   arrives server-side, then polling stops until the charger connects.
5. Shutdown hook: `busctl monitor` the system bus, issue
   `loginctl terminate-session` or power off, confirm
   `PrepareForShutdown(true)` fires and the last report precedes it.

Device (owner's phone, daily driver image):

1. Fresh charge, xx-find enabled + paired, screen off, note start %
   and time. Discharge to 15 % through normal use (do not force it:
   forced discharge skews the budget numbers).
2. Confirm one last sealed report with `battery_pct` arrives
   server-side with the device flagged low battery, then polling stops
   until charging. Record `%` and `WarningLevel` at send time.
3. Erase is verified by dry-run listing review only, on the owner's
   screen in Settings (typed device id shown, wipe list shown, no
   deletion). Real wipe is the owner's explicit action, never a test.
4. Record idle %/day, active %/h, low-battery stop y/n, and modem
   restore y/n in the lane 8 soak note.

## 5. Ring review lesson (lane 4 finding, fixed here)

`build_ring_action` sent `TriggerFeedback(app, "phone-incoming-call",
{}, 0)` and claimed alarm-class ring-through. Under feedbackd the
active profile (`full` / `quiet` / `silent`, `feedbackd(8)`) caps
which theme sections are consulted, and under `silent` the bare event
produces nothing. The per-event `profile` hint (`{"profile": "full"}`)
requests the full level for this one event; `important: True`
requests the alarm-clock override alongside it (confirmed in
`data/org.sigxcpu.Feedback.xml`: `profile` overrides the profile for
the event, `important` overrides the global level together with
`profile`; valid profiles `full`/`quiet`/`silent`; the daemon may
ignore `important` unless the app is in `allow-important`). Lane 5
sends both hints (`ring_feedback_params`, one-step
`(ssa{sv}i)` Variant of plain-dict Variants, covered by a real-GLib
test) and keeps the `paplay`/`pw-play` fallback for absent or
declining feedbackd.
