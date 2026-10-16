# XX-Vitals — Backup and restore

The NAS is the system of record; the phone holds a replica plus an
offline write queue. A wiped phone re-fills everything by syncing, so
there is deliberately little to back up — mirroring the Android app's
`allowBackup="false"` (the Room replica was excluded from cloud/adb
backup on purpose: a decade of health data must never ride a cloud
backup, per the manifest comment in `ref/xx-vitals`).

## What is excluded

- SQLite replica `~/.local/share/xx-vitals/vitals.db` — excluded from
  backups. It re-fills from the NAS on the next sync (`GET /v1/changes`
  backfills everything, including server-side Takeout imports). Backing
  it up would also risk restoring a stale `sync_state` cursor and
  `pending_write` queue next to newer server rows.
- Fabric token (Secret Service, `io.piercingxx.XXVitals`) — never
  leaves the keyring. After a restore, sign in again on Profile; the
  token is re-stored and the `relogin_required` flag clears.
- Logs `~/.local/state/xx-vitals/xx-vitals.log` and cache
  `~/.cache/xx-vitals/` — transient diagnostics, never backed up.

## What travels with a normal home-directory backup

- `~/.config/xx-vitals/config.json` — goals, units, height, birth year,
  tab visibility, NAS host, step baseline. Plain JSON, safe to back up.
  The shell's own backup (`xx-wm-backup-*.json`) covers shell settings
  only and never touches this file.

## Opting in

If you back up `~/.local/share` wholesale, `vitals.db` rides along —
harmless as long as you sync after restoring (the sync watermark in
`sync_state` resumes the delta from where the backup was taken; queued
writes replay in order, server dedupes replays on its UNIQUE
constraints). The token still needs a fresh sign-in.
