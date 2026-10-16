# XX-Vitals — Config Reference

Every app preference lives in `~/.config/xx-vitals/config.json` (plain
JSON, written atomically). Missing keys fall back to their defaults;
unknown keys are ignored. The fabric token is never here — it lives in
libsecret under the `io.piercingxx.XXVitals` schema.

## config.json

### Goals and profile

| Key | Type | Default | Effect |
|---|---|---|---|
| `steps_goal` | int | `10000` | Daily step goal (outer ring on Home). |
| `effort_goal` | int | `90` | Daily effort goal (inner ring on Home). |
| `weekly_effort_goal` | int | `150` | Weekly effort target card. |
| `height_metres` | float | `0.0` | Height; `0` means unset (derived distance/calories stay null, never 0). |
| `birth_year` | int | `0` | Birth year; `0` means unset. |
| `units` | string | `"imperial"` | `metric` or `imperial`; display only, storage is always metric. |

### Tabs and sync

| Key | Type | Default | Effect |
|---|---|---|---|
| `show_journal_tab` | bool | `true` | Journal tab visible (Home is always visible). |
| `show_browse_tab` | bool | `true` | Browse tab visible. |
| `show_profile_tab` | bool | `true` | Profile tab visible. |
| `server_url` | string | `""` | NAS base URL, e.g. `https://nas:8443`. Empty means not configured. |
| `xxchat_url` | string | `""` | xx-chat host used for fabric login. |
| `relogin_required` | bool | `false` | Set on 401; Profile shows re-login until sign-in clears it. |
| `allowed_cleartext_hosts` | list | `[]` | Hosts where cleartext `http://` is allowed; HTTPS is always allowed. |

### Pedometer and collector

Step counting is **opt-in and off by default**: a fresh install never
samples the accelerometer until the user flips the switch in Profile.

| Key | Type | Default | Effect |
|---|---|---|---|
| `collect_enabled` | bool | `false` | Master pedometer switch. `false` means the resident service, the timer tick and the in-app fallback all do nothing. |
| `count_while_screen_off` | bool | `false` | Take a logind `delay` sleep inhibitor while motion is detected, so a walk with the screen off delays suspend briefly. Off by default (uses more battery). |
| `step_baseline` | object | — | Last hardware counter reading `{counter, elapsed_ms, wall_ms, source}`. Written automatically; `source` is `hardware`. Delete it to force a cold start (next tick attributes nothing). |
| `step_noise_floor` | float | — | Per-device still-noise floor learned by the resident sampler. Written automatically; delete it to re-learn. |

Background collection depends on the probed step source (enable
explicitly after opting in; units are never auto-enabled at install):

- **Hardware step counter present:** the timer path.
  `xx-vitals-collect.timer` fires every 15 min and runs the one-shot
  `xx-vitals-collect-hw.service` (`xx-vitals-collect --one-shot`),
  which samples the cumulative counter through the Android rebase
  rules and exits. Sampling a cumulative counter every 15 min is
  exactly right — nothing is missed between ticks.

  ```sh
  systemctl --user enable --now xx-vitals-collect.timer
  ```

- **Accelerometer only:** the resident path.
  `xx-vitals-collect.service` (`Type=simple`, `Restart=on-failure`,
  `Nice=10`, owns no bus name) runs the motion-gated sampler from
  `collect/resident.py`: one accelerometer read every ~2 s while
  still, continuous ~40 Hz sampling once motion is confirmed, steps
  counted with the peak filter while moving, back to the cheap poll
  after ~20 s still. Counted steps flush to the database every minute
  and on SIGTERM, so a kill loses at most a minute.

  ```sh
  systemctl --user enable --now xx-vitals-collect.service
  ```

If `systemctl --user` is unavailable, the GUI runs the same
motion-gated sampler on a worker thread while the app window is open
(hardware counter: a 15-minute in-app tick samples it while open).
Documented degradation: steps only accumulate while XX-Vitals is open.

### Power and suspend

Still polls cost ~0.5 wakeups/s at a measured ~0.00005% CPU and moving
sampling ~40 wakeups/s at ~0.01% CPU (process-time ratios over a
synthetic run on the build machine — order of magnitude only, and
sensor reads excluded). The screen-off inhibitor is off by default;
with it off the resident service takes no wake-lock of any kind.

Suspended phones count nothing: Linux phones suspend to RAM when the
screen is off, and while suspended no user process runs, so a software
pedometer only counts while the phone is awake. The Profile page says
so plainly ("Without a hardware step counter, steps are only counted
while the phone is awake"), and a hardware counter instead attributes
the full since-last-sample delta on the next tick, suspend included.

### No estimates, ever

The steps column holds only observed counts. The old burst sampler was
removed for exactly this reason: a 10 s burst every 15 min covers ~1%
of the day, and banking its count as the day's steps presented fiction
as measured data. No extrapolated or estimated step number is stored
anywhere, so there is no `estimated` field for the UI to label.

## Not in this file

- SQLite replica: `~/.local/share/xx-vitals/vitals.db` (stays out of
  shell backups unless the user opts in; sync re-fills it from the NAS).
  See [backup.md](backup.md) for the full exclusion list.
- Fabric token: Secret Service (`io.piercingxx.XXVitals`, attributes
  `{application, host}`) — never in config.json or SQLite.
- Logs: `~/.local/state/xx-vitals/xx-vitals.log` plus the in-memory ring
  behind Profile "Copy logs".
