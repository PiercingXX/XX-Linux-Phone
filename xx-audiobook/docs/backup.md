# XX-Audiobook — Backup and restore

Siblings do this by documentation + file posture, not by a provider API:
the xx-wm backup picks the files below up. The Android suite TAR
provider (`AudiobookBackupProvider`, `HistoryJson`, `BackupExclusions`)
has no Linux equivalent and is not ported.

## What is included

- `~/.config/xx-audiobook/config.json` — playback prefs (skip
  back/forward, default speed, auto-rewind, Wi-Fi-only, last book +
  position, settings stamps, `owner_user_id`). Plain JSON, safe to back
  up. A restore with a different `owner_user_id` wipes local state
  first (same as `SessionManager.adopt()`).
- `~/.local/share/xx-audiobook/audiobook.db` — SQLite v1 (8 tables:
  `items`, `item_detail`, `progress`, `pending_sync`, `downloads`,
  `download_files`, `bookmarks`, `history`; WAL mode). Back it up as one
  file after a WAL checkpoint. The `downloads` / `download_files` rows
  point at media that never travels: a restore drops those rows (the
  books are re-downloaded) and deletes `books/` + `covers/` below.
- `export/history.json` — per-book listening state
  (`item_id`, `position_s`, `duration_s`, `finished`,
  `last_played_at_ms`, `updated_at_ms`, `speed`; `{"schema": 1,
  "entries": [...]}` sorted by `item_id`). Generated with
  `download.export_history(db)` from the `progress` + `history` tables.
  Restore merges with `download.import_history(db, text)`: last-write-
  wins on `updated_at_ms` (a newer local row is kept), `schema > 1`
  exports are refused before anything on disk changes.

## What is excluded (never backed up)

- Estate token (libsecret, `io.piercingxx.XXAudiobook`) — never leaves
  the keyring. After a restore, sign in again; the password is never
  stored anywhere.
- Downloaded audio `~/.local/share/xx-audiobook/books/{safeId}/` —
  re-download after restore.
- Offline covers `~/.local/share/xx-audiobook/covers/{id}.jpg` —
  re-cached on the next completed download.
- Logs `~/.local/state/xx-audiobook/xx-audiobook.log` (+ rotated
  sibling, `last-crash.txt`) — transient diagnostics.
- Cache `~/.cache/xx-audiobook/` (image cache) — transient.
- Debug API-base override (`XX_AUDIOBOOK_API_BASE` env) — never stored,
  never backed up, by construction.

## Restore order

1. Refuse `export/history.json` with `schema > 1` before touching disk.
2. Restore `config.json` and `audiobook.db`; delete `books/` and
   `covers/`; drop `downloads` / `download_files` rows.
3. Merge `export/history.json` with `download.import_history` (LWW).
4. Sign in again; re-download books from Downloads.
