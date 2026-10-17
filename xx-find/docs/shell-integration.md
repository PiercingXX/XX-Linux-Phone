# xx-find lane 4 — shell integration (for the orchestrator)

Lane 4 implements the phone-side **actions** behind injectable seams.
Nothing here is enabled, auto-started or deployed: the unit ships with
no `[Install]` section, the agent refuses to act until enabled AND
paired, and tests never touch the real bus, network, GPS, keyring or
audio. This file specs the **minimal shell hook** the orchestrator
needs; `ref/xx-wm` is read-only here and was not modified.

## 1. Ring (full volume, DnD/silenced-proof)

Agent path (`src/xx_find/actions.py:build_ring_action`) drives the SAME
bus call as the shell's own `Ringer` (`ref/xx-wm/launcher/src/call_ui.py`):

- Session bus `org.sigxcpu.Feedback`, `/org/sigxcpu/Feedback`,
  `org.sigxcpu.Feedback.TriggerFeedback(app_id, "phone-incoming-call",
  {"profile": "full", "important": True}, 0)` with reply type `(u)`,
  then `EndFeedback(u)` after `RING_DURATION_S` (60 s). Timeout `0`
  loops until `EndFeedback` (shell comment: `-1` would play once then
  go silent). Built in one step as a plain dict of Variants
  (`ring_feedback_params`), never a nested prebuilt `a{sv}` Variant.
- Ring-through needs no volume write, but it DOES need the hints:
  feedbackd looks up feedbacks under the active profile (`full` /
  `quiet` / `silent`), and under `silent` the bare event with empty
  hints produces nothing. `profile: "full"` requests full-profile
  feedbacks for this one event; `important: True` requests the
  alarm-clock override (the daemon may ignore it unless the app is in
  `allow-important`, hence the sound-loop fallback below). DnD only
  silences notification sounds (`ref/xx-wm/launcher/src/banner.py`).
  The agent never writes ALSA/PipeWire volumes (static test bans
  `amixer`, `set-sink-volume`, `wpctl set-volume`, etc.).
- Fallback when feedbackd is absent: the injected `play`/`stop` loop,
  same shape as the shell's `sound.play('ringtone.ogg', loop=True)`
  (`paplay`, else `pw-play`) in `ref/xx-wm/launcher/src/sound.py`.
- `full_volume=True` is accepted for the `AgentDeps.ring` seam and
  intentionally changes no mixer; ring-through comes from the event
  class.

Minimal shell hook: **none for the bus call**, plus one optional asset
step. The agent uses `app_id="io.piercingxx.Find"`. If feedbackd has no
theme entry for that id, it falls back to the default profile (still
audible on stock feedbackd) and then to the sound loop. To guarantee
the exact shell ringtone, ship a feedbackd theme mapping
`io.piercingxx.Find/phone-incoming-call` to the same ringtone the shell
uses for `io.piercingxx.XXWM`, or accept the `paplay` fallback.

All D-Bus per XXWM_RULES: `reply_type` is always a
`GLib.VariantType` (never a string), replies unpack as tuples
(`reply.unpack()[0]`), worker-thread blocking calls only.

## 2. Lock-screen message + callback

Agent path (`build_message_action`) writes one JSON file atomically
(tmp + fsync + rename, dir `0700`, file `0600`):

- Path: `<XDG_STATE_HOME>/xx-find/find-message.json`
  (default `~/.local/state/xx-find/find-message.json`).
- Schema (sorted keys, ASCII): `{"callback":"…","epoch":int,"text":"…","v":1}`.
  `text` is whitespace-collapsed and clipped to 280 chars,
  `callback` to 64 chars; `epoch` is unix time. Readers ignore unknown
  fields and accept a missing/corrupt file as "no message".
- After the write the agent best-effort sends the shell's existing
  `lock` verb over the shell IPC socket (`$XDG_RUNTIME_DIR/xx-wm.sock`,
  same layout as the shell's `ipc.py`) so the lock screen shows
  immediately. IPC failure never fails the command.

Minimal shell hook (orchestrator patch in `lock_screen.py`):

1. Watch the file above with a `Gio.FileMonitor` (or read it in
   `LockScreen._build` / on every `_show_lock_screen`).
2. Render `text` + `callback` in the existing emergency/medical card
   area pattern (`_build_emergency_surface`, `mid_box`,
   `_medical_revealer`): a persistent owner-message card visible
   without unlock, styled from the active preset, ellipsized/wrapped to
   320 px. Reuse `format_medical_info` card styling; do not add icons.
3. Clear the card when the file is deleted (owner cancels from the
   server UI; agent lane 6/7 sends a clear). No new D-Bus name and no
   activation file: the file hook keeps the built-never-running rule
   (no `dbus-1/services` entry, no autostart).

## 3. Lock now

Agent path (`build_lock_action`): primary is the shell's existing `lock`
IPC verb (`_on_ipc_command` → `_show_lock_screen` in the shell's
`main.py`); fallback is logind `org.freedesktop.login1`,
`/org/freedesktop/login1`, `Manager.LockSessions()` (no args) on the
system bus. Raises only when both fail. No shell change needed.

## 4. Privacy + bus names

Every successful location read fires `on_privacy_event("location",
"xx-find-agent")`, which production wires to the shell's
`PrivacyMonitor.refresh_location` / `UsageLog` path, so the `loc`
indicator and 24 h log stay honest. The agent never registers GeoClue's
`AuthorizeApp` slot (the shell's `LocationState` owns it) and never
owns the shell's D-Bus names (future service name, if any:
`io.piercingxx.Find.Agent`, with its own activation file only when the
owner enables the feature — not shipped here).

## 5. Poll cadence, jitter, backoff, battery budget

Implemented pure in `src/xx_find/schedule.py` (tests pin the math):

| Item | Value |
|---|---|
| Idle poll | 900 s (15 min) |
| Active poll (ring/message pending) | 360 s (6 min) |
| Heartbeat location fix | every 21600 s (6 h) or on `locate` |
| Jitter | ±25 %: `base * (0.75 + 0.5 * rand)`, `rand` in [0, 1) injected |
| Backoff | `min(base * 2**failures, 3600 s)`, failures cap at shift 4, then jitter |
| Radio model | ~10 s wake per poll, no GPS on idle polls |
| Idle target | < 1 %/day (96 polls/day = 960 s airtime) |
| Active target | < 3 %/h |
| Low battery | `pct <= 15` and not charging → last sealed report, stop until charging |
| Wake lock | agent never holds one; unit has no `WakeSystem` |

`budget_report()` computes the wake counts above and `log_budget()`
logs them; `build_battery_probe()` reads UPower's display device
(`org.freedesktop.UPower`, `DisplayDevice`, `GetAll` on
`org.freedesktop.UPower.Device`: `IsPresent`/`Percentage`/`State`,
charging = `State == 1`) over an injected system bus, same path as the
shell's `app_standby.query_battery`.

## 6. On-device measurement procedure (deferred)

The owner has not enabled the feature, so no measurement was taken.
When they do, run this on the target phone to verify §1.2 budgets:

1. Prerequisites: freshly charged phone on the daily driver image,
   xx-find enabled + paired to the NAS over Tailscale, screen off,
   no calls/media, battery saver off, note start `%` and time.
2. Idle soak (24 h or at least 8 h scaled): leave the phone idle with
   no commands queued. Record `journalctl --user -u xx-find-agent`
   poll lines (each shows the jittered delay from `next_wake`) and the
   battery `%` drop. Pass: pro-rata < 1 %/day with no GPS fixes in the
   log (location only on heartbeat).
3. Active session (1 h): queue a `ring` + `message` from the server UI,
   confirm the phone rings at full volume under DnD/silenced, the lock
   screen shows text + callback, and `lock` locks immediately. Record
   `%` drop over the hour. Pass: < 3 % and DnD ring-through observed.
4. Low battery: discharge to 15 % (or set the test threshold), confirm
   one last sealed report with `battery_pct` arrives server-side, then
   polling stops until the charger is connected.
5. Modem check (review lesson): after any `locate`, run
   `mmcli --modem N --location-get` and confirm the enabled sources
   match the pre-locate value (the agent restores `Enabled` +
   `SignalsLocation` in a `finally`; GPS must not stay on).
6. Record the four numbers (idle %/day, active %/h, low-battery stop
   observed y/n, modem restored y/n) in the lane 8 soak note. If idle
   exceeds 1 %/day, lengthen the idle interval or narrow the jitter
   before re-measuring; do not add a wake lock.
