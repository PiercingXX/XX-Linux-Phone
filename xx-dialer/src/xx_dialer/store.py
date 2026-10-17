"""SQLite store: schema + DAO helpers, stdlib only.

Database lives at ~/.local/share/xx-dialer/calls.db (SQLite, WAL mode).
Tables mirror design §11 one-to-one (same table/column names), plus:

- ``blocked_number`` — app-local exact-number blocklist (replaces
  BlockedNumberContract, which does not exist on Linux).
- ``star_overlay`` — stars kept when EDS is read-only (lane 2 unions this
  with the EDS star; the engine treats both identically).
- ``call_log`` — own call history (number/type/date/duration/cachedName,
  the CallLogEntrySnapshot shape); there is no system call log on Linux.

``channel_registry`` is dropped (no notification channels on Linux; two
tone-path keys in config.json instead). Settings live in config.json, not
in a ``setting`` table — the pure settings seams (window JSON, safe enums,
observe-week offer, Rules assembly) live here so tests pin them without a
database.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from xx_dialer import policy

APP_DIR_NAME = "xx-dialer"
DB_NAME = "calls.db"
SCHEMA_VERSION = 1

SCREEN_LOG_CAP = 1000

EMERGENCY_WINDOW_MS = 24 * 60 * 60 * 1000
REPEAT_MS = 15 * 60 * 1000
RECENT_OUTGOING_MS = 48 * 60 * 60 * 1000

BLOCKED_GROUP = "Blocked"
BIZ_TIER = "biz"

CALL_TYPE_INCOMING = 1
CALL_TYPE_OUTGOING = 2
CALL_TYPE_MISSED = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pattern_rule (
    id INTEGER PRIMARY KEY,
    e164Prefix TEXT NOT NULL,
    wildcards INTEGER NOT NULL DEFAULT 0,
    action TEXT NOT NULL,
    preset TEXT,
    createdAt INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tier_member (
    lookupKey TEXT NOT NULL,
    tier TEXT NOT NULL,
    addedAt INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (lookupKey, tier)
);
CREATE TABLE IF NOT EXISTS contact_mirror (
    lookupKey TEXT NOT NULL,
    e164 TEXT NOT NULL,
    displayName TEXT NOT NULL DEFAULT '',
    starred INTEGER NOT NULL DEFAULT 0,
    customRingtone TEXT,
    sendToVoicemail INTEGER NOT NULL DEFAULT 0,
    refreshedAt INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (lookupKey, e164)
);
CREATE INDEX IF NOT EXISTS index_contact_mirror_e164
    ON contact_mirror (e164);
CREATE INDEX IF NOT EXISTS index_contact_mirror_lookupKey
    ON contact_mirror (lookupKey);
CREATE TABLE IF NOT EXISTS screen_log (
    id INTEGER PRIMARY KEY,
    at INTEGER NOT NULL,
    e164 TEXT,
    presentation INTEGER NOT NULL DEFAULT 0,
    verdict TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    tier TEXT,
    stir TEXT NOT NULL DEFAULT '',
    cnapName TEXT,
    mode TEXT NOT NULL DEFAULT 'observed',
    answered INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS index_screen_log_e164_at
    ON screen_log (e164, at);
CREATE TABLE IF NOT EXISTS call_log (
    id INTEGER PRIMARY KEY,
    number TEXT,
    type INTEGER NOT NULL DEFAULT 0,
    date INTEGER NOT NULL,
    duration INTEGER NOT NULL DEFAULT 0,
    cachedName TEXT
);
CREATE INDEX IF NOT EXISTS index_call_log_number_date
    ON call_log (number, date);
CREATE TABLE IF NOT EXISTS blocked_number (
    e164 TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS star_overlay (
    uid TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS pending_tag_edit (
    uid TEXT PRIMARY KEY,
    tags TEXT NOT NULL DEFAULT '[]',
    added TEXT NOT NULL DEFAULT '[]',
    removed TEXT NOT NULL DEFAULT '[]',
    updatedAt INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS emergency_marker (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    lastEmergencyCallAt INTEGER NOT NULL,
    lastEmergencyElapsed INTEGER NOT NULL,
    lastEmergencyBoot TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS recents_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    viewedAt INTEGER NOT NULL DEFAULT 0,
    digestFlushedAt INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS silence_digest (
    id INTEGER PRIMARY KEY,
    at INTEGER NOT NULL,
    e164 TEXT,
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS posted_notif (
    serverId INTEGER PRIMARY KEY,
    e164 TEXT NOT NULL DEFAULT '',
    postedAt INTEGER NOT NULL DEFAULT 0
);
"""


def db_path(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return base / ".local" / "share" / APP_DIR_NAME / DB_NAME


@dataclass
class PatternRow:
    e164_prefix: str
    wildcards: int
    action: str
    preset: str | None = None
    created_at: int = 0
    row_id: int = 0


@dataclass
class MirrorRow:
    lookup_key: str
    e164: str
    display_name: str = ""
    starred: bool = False
    custom_ringtone: str | None = None
    send_to_voicemail: bool = False
    refreshed_at: int = 0
    biz_tier: bool = False


@dataclass
class ScreenRow:
    at: int
    verdict: str
    reason: str = ""
    e164: str | None = None
    presentation: int = 0
    tier: str | None = None
    stir: str = ""
    cnap_name: str | None = None
    mode: str = "observed"
    answered: bool = False
    row_id: int = 0


@dataclass
class CallRow:
    date: int
    number: str | None = None
    call_type: int = 0
    duration: int = 0
    cached_name: str | None = None
    row_id: int = 0


class Store:
    """One SQLite file; short-lived instances, no thread affinity."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        # The service process holds one long-lived handle for the
        # contacts sync while worker threads open short-lived handles
        # on the same file; wait on locks instead of raising BUSY.
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._db.executescript(_SCHEMA)
        self._ensure_emergency_boot_column()
        self._ensure_pending_delta_columns()
        self._db.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
        self._db.commit()

    def _ensure_emergency_boot_column(self) -> None:
        cols = {row[1] for row in self._db.execute(
            "PRAGMA table_info(emergency_marker)")}
        if "lastEmergencyBoot" not in cols:
            self._db.execute(
                "ALTER TABLE emergency_marker ADD COLUMN"
                " lastEmergencyBoot TEXT NOT NULL DEFAULT ''")

    def _ensure_pending_delta_columns(self) -> None:
        cols = {row[1] for row in self._db.execute(
            "PRAGMA table_info(pending_tag_edit)")}
        migrated = False
        for column in ("added", "removed"):
            if column not in cols:
                self._db.execute(
                    "ALTER TABLE pending_tag_edit ADD COLUMN %s"
                    " TEXT NOT NULL DEFAULT '[]'" % column)
                migrated = True
        if migrated and "tags" in cols:
            self._db.execute(
                "UPDATE pending_tag_edit SET added = tags"
                " WHERE added = '[]' AND removed = '[]'"
                " AND tags IS NOT NULL AND tags <> '[]'")

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self._db.execute(sql, params)
        self._db.commit()
        return cur

    # ---- pattern_rule ----------------------------------------------------

    def add_pattern(self, row: PatternRow) -> int:
        cur = self._execute(
            "INSERT INTO pattern_rule"
            " (e164Prefix, wildcards, action, preset, createdAt)"
            " VALUES (?, ?, ?, ?, ?)",
            (row.e164_prefix, row.wildcards, row.action,
             row.preset, row.created_at),
        )
        return cur.lastrowid or 0

    def all_patterns(self) -> list[PatternRow]:
        cur = self._db.execute(
            "SELECT id, e164Prefix, wildcards, action, preset, createdAt"
            " FROM pattern_rule ORDER BY createdAt")
        return [PatternRow(e164_prefix=r["e164Prefix"],
                           wildcards=r["wildcards"], action=r["action"],
                           preset=r["preset"], created_at=r["createdAt"],
                           row_id=r["id"]) for r in cur.fetchall()]

    def replace_policy_tables(self, patterns: list[PatternRow],
                              tiers: list[tuple[str, str, int]]) -> None:
        """Wholesale replace of patterns + tiers inside one transaction,
        so a bad payload can never leave half a restore behind."""
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute("DELETE FROM pattern_rule")
            self._db.executemany(
                "INSERT INTO pattern_rule (e164Prefix, wildcards, action,"
                " preset, createdAt) VALUES (?, ?, ?, ?, ?)",
                [(p.e164_prefix, p.wildcards, p.action, p.preset,
                  p.created_at) for p in patterns])
            self._db.execute("DELETE FROM tier_member")
            self._db.executemany(
                "INSERT INTO tier_member (lookupKey, tier, addedAt)"
                " VALUES (?, ?, ?)", tiers)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def delete_pattern(self, row_id: int) -> None:
        self._execute("DELETE FROM pattern_rule WHERE id = ?", (row_id,))

    def clear_patterns(self) -> None:
        self._execute("DELETE FROM pattern_rule")

    # ---- tier_member ------------------------------------------------------

    def upsert_tier(self, lookup_key: str, tier: str, added_at: int = 0) -> None:
        self._execute(
            "INSERT INTO tier_member (lookupKey, tier, addedAt)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT (lookupKey, tier) DO UPDATE SET addedAt = ?",
            (lookup_key, tier, added_at, added_at),
        )

    def remove_tier(self, lookup_key: str, tier: str) -> None:
        self._execute(
            "DELETE FROM tier_member WHERE lookupKey = ? AND tier = ?",
            (lookup_key, tier),
        )

    def remove_tiers_for_key(self, lookup_key: str) -> None:
        self._execute("DELETE FROM tier_member WHERE lookupKey = ?",
                      (lookup_key,))

    def remove_tier_name(self, tier: str) -> None:
        self._execute("DELETE FROM tier_member WHERE tier = ?", (tier,))

    def keys_for_tier(self, tier: str) -> list[str]:
        cur = self._db.execute(
            "SELECT lookupKey FROM tier_member WHERE tier = ?", (tier,))
        return [r["lookupKey"] for r in cur.fetchall()]

    def all_tiers(self) -> list[tuple[str, str, int]]:
        cur = self._db.execute(
            "SELECT lookupKey, tier, addedAt FROM tier_member")
        return [(r["lookupKey"], r["tier"], r["addedAt"])
                for r in cur.fetchall()]

    def clear_tiers(self) -> None:
        self._execute("DELETE FROM tier_member")

    # ---- blocked_number ----------------------------------------------------

    def add_blocked(self, e164: str) -> None:
        self._execute("INSERT OR IGNORE INTO blocked_number (e164)"
                      " VALUES (?)", (e164,))

    def remove_blocked(self, e164: str) -> None:
        self._execute("DELETE FROM blocked_number WHERE e164 = ?", (e164,))

    def is_blocked(self, e164: str) -> bool:
        cur = self._db.execute(
            "SELECT 1 FROM blocked_number WHERE e164 = ?", (e164,))
        return cur.fetchone() is not None

    def all_blocked(self) -> list[str]:
        cur = self._db.execute("SELECT e164 FROM blocked_number")
        return [r["e164"] for r in cur.fetchall()]

    def clear_blocked(self) -> None:
        self._execute("DELETE FROM blocked_number")

    # ---- star_overlay ------------------------------------------------------

    def add_star(self, uid: str) -> None:
        self._execute("INSERT OR IGNORE INTO star_overlay (uid)"
                      " VALUES (?)", (uid,))

    def remove_star(self, uid: str) -> None:
        self._execute("DELETE FROM star_overlay WHERE uid = ?", (uid,))

    def is_starred(self, uid: str) -> bool:
        cur = self._db.execute(
            "SELECT 1 FROM star_overlay WHERE uid = ?", (uid,))
        return cur.fetchone() is not None

    def all_starred(self) -> list[str]:
        cur = self._db.execute("SELECT uid FROM star_overlay")
        return [r["uid"] for r in cur.fetchall()]

    def clear_stars(self) -> None:
        self._execute("DELETE FROM star_overlay")

    # ---- pending_tag_edit --------------------------------------------------
    # Edits recorded while xx-contacts is absent. Each row holds the exact
    # intended tag set for one uid plus a timestamp; the client pushes them
    # once on the absent -> present transition and then clears them.

    def record_pending_edit(self, uid: str, tags: list[str],
                            updated_at: int, add=None,
                            remove=None) -> None:
        """Queue an offline tag edit; repeat toggles merge to one delta.

        ``tags`` stays the full intended set while ``add``/``remove``
        carry the toggle itself, so replay goes out as an atomic
        UpdateTags delta instead of a full-set rewrite that could undo
        newer edits. An unknown delta degrades to additive-only.
        """
        if add is None and remove is None:
            net_add, net_remove = set(tags), set()
        else:
            add_set = set(add or [])
            remove_set = set(remove or []) - add_set
            cur = self._db.execute(
                "SELECT added, removed FROM pending_tag_edit"
                " WHERE uid = ?", (uid,))
            row = cur.fetchone()
            if row is None:
                net_add, net_remove = add_set, remove_set
            else:
                try:
                    old_add = set(json.loads(row["added"]))
                except ValueError:
                    old_add = set()
                try:
                    old_remove = set(json.loads(row["removed"]))
                except ValueError:
                    old_remove = set()
                net_add = (old_add - remove_set) | add_set
                net_remove = (old_remove - add_set) | remove_set
        payload = json.dumps(sorted(tags))
        added = json.dumps(sorted(net_add))
        removed = json.dumps(sorted(net_remove))
        self._execute(
            "INSERT INTO pending_tag_edit (uid, tags, added, removed,"
            " updatedAt) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (uid) DO UPDATE SET tags = ?, added = ?,"
            " removed = ?, updatedAt = ?",
            (uid, payload, added, removed, int(updated_at),
             payload, added, removed, int(updated_at)),
        )

    def pending_deltas(self) -> list[tuple[str, list[str], list[str],
                                           int]]:
        """Queued (uid, add, remove, updatedAt) for UpdateTags replay."""
        cur = self._db.execute(
            "SELECT uid, added, removed, updatedAt FROM pending_tag_edit"
            " ORDER BY updatedAt, uid")
        out = []
        for r in cur.fetchall():
            add: list[str] = []
            remove: list[str] = []
            try:
                parsed = json.loads(r["added"])
                if isinstance(parsed, list):
                    add = [str(t) for t in parsed]
            except ValueError:
                pass
            try:
                parsed = json.loads(r["removed"])
                if isinstance(parsed, list):
                    remove = [str(t) for t in parsed]
            except ValueError:
                pass
            out.append((r["uid"], add, remove, int(r["updatedAt"])))
        return out

    def pending_edits(self) -> list[tuple[str, list[str], int]]:
        cur = self._db.execute(
            "SELECT uid, tags, updatedAt FROM pending_tag_edit"
            " ORDER BY updatedAt, uid")
        out = []
        for r in cur.fetchall():
            try:
                tags = json.loads(r["tags"])
            except ValueError:
                tags = []
            if not isinstance(tags, list):
                tags = []
            out.append((r["uid"], [str(t) for t in tags],
                        int(r["updatedAt"])))
        return out

    def clear_pending_edit(self, uid: str) -> None:
        self._execute("DELETE FROM pending_tag_edit WHERE uid = ?", (uid,))

    def clear_pending_edits(self) -> None:
        self._execute("DELETE FROM pending_tag_edit")

    # ---- contact_mirror ----------------------------------------------------

    def upsert_mirror(self, rows: list[MirrorRow]) -> None:
        self._db.executemany(
            "INSERT INTO contact_mirror (lookupKey, e164, displayName,"
            " starred, customRingtone, sendToVoicemail, refreshedAt)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (lookupKey, e164) DO UPDATE SET"
            " displayName = excluded.displayName,"
            " starred = excluded.starred,"
            " customRingtone = excluded.customRingtone,"
            " sendToVoicemail = excluded.sendToVoicemail,"
            " refreshedAt = excluded.refreshedAt",
            [(r.lookup_key, r.e164, r.display_name, int(r.starred),
              r.custom_ringtone, int(r.send_to_voicemail), r.refreshed_at)
             for r in rows],
        )
        self._db.commit()

    def find_mirror(self, e164: str) -> MirrorRow | None:
        cur = self._db.execute(
            "SELECT contact_mirror.*,"
            " EXISTS(SELECT 1 FROM tier_member"
            " WHERE tier_member.lookupKey = contact_mirror.lookupKey"
            " AND tier_member.tier = 'biz') AS bizTier"
            " FROM contact_mirror WHERE e164 = ? LIMIT 1", (e164,))
        r = cur.fetchone()
        if r is None:
            return None
        return MirrorRow(lookup_key=r["lookupKey"], e164=r["e164"],
                         display_name=r["displayName"],
                         starred=bool(r["starred"]),
                         custom_ringtone=r["customRingtone"],
                         send_to_voicemail=bool(r["sendToVoicemail"]),
                         refreshed_at=r["refreshedAt"],
                         biz_tier=bool(r["bizTier"]))

    def mirror_numbers_for_key(self, lookup_key: str) -> list[str]:
        cur = self._db.execute(
            "SELECT e164 FROM contact_mirror WHERE lookupKey = ?",
            (lookup_key,))
        return [r["e164"] for r in cur.fetchall()]

    def all_mirror(self) -> list[MirrorRow]:
        cur = self._db.execute(
            "SELECT lookupKey, e164, displayName, starred, customRingtone,"
            " sendToVoicemail, refreshedAt FROM contact_mirror"
            " ORDER BY displayName, e164")
        return [MirrorRow(lookup_key=r["lookupKey"], e164=r["e164"],
                         display_name=r["displayName"],
                         starred=bool(r["starred"]),
                         custom_ringtone=r["customRingtone"],
                         send_to_voicemail=bool(r["sendToVoicemail"]),
                         refreshed_at=r["refreshedAt"])
                for r in cur.fetchall()]

    def delete_mirror_for_key(self, lookup_key: str) -> None:
        self._execute("DELETE FROM contact_mirror WHERE lookupKey = ?",
                      (lookup_key,))

    def delete_mirror_row(self, lookup_key: str, e164: str) -> None:
        self._execute("DELETE FROM contact_mirror"
                      " WHERE lookupKey = ? AND e164 = ?",
                      (lookup_key, e164))

    def mirror_count(self) -> int:
        cur = self._db.execute("SELECT COUNT(*) AS n FROM contact_mirror")
        row = cur.fetchone()
        return int(row["n"]) if row else 0

    def delete_stale_mirror(self, before_ms: int) -> None:
        self._execute("DELETE FROM contact_mirror WHERE refreshedAt < ?",
                      (before_ms,))

    def clear_mirror(self) -> None:
        self._execute("DELETE FROM contact_mirror")

    # ---- screen_log --------------------------------------------------------

    def log_screen(self, row: ScreenRow) -> int:
        cur = self._execute(
            "INSERT INTO screen_log (at, e164, presentation, verdict, reason,"
            " tier, stir, cnapName, mode, answered)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row.at, row.e164, row.presentation, row.verdict, row.reason,
             row.tier, row.stir, row.cnap_name, row.mode,
             int(row.answered)),
        )
        row_id = cur.lastrowid or 0
        self.prune_screen_log()
        return row_id

    def prune_screen_log(self) -> None:
        self._execute(
            "DELETE FROM screen_log WHERE id NOT IN"
            " (SELECT id FROM screen_log ORDER BY at DESC LIMIT %d)"
            % SCREEN_LOG_CAP)

    def last_screened(self, limit: int) -> list[ScreenRow]:
        cur = self._db.execute(
            "SELECT id, at, e164, presentation, verdict, reason, tier, stir,"
            " cnapName, mode, answered FROM screen_log"
            " ORDER BY at DESC LIMIT ?", (limit,))
        return [_screen_from(r) for r in cur.fetchall()]

    def latest_for(self, e164: str) -> ScreenRow | None:
        cur = self._db.execute(
            "SELECT id, at, e164, presentation, verdict, reason, tier, stir,"
            " cnapName, mode, answered FROM screen_log"
            " WHERE e164 = ? ORDER BY at DESC LIMIT 1", (e164,))
        r = cur.fetchone()
        return _screen_from(r) if r is not None else None

    def set_answered(self, row_id: int, answered: bool) -> None:
        self._execute("UPDATE screen_log SET answered = ? WHERE id = ?",
                      (int(answered), row_id))

    def silence_count_since(self, e164: str, since_ms: int) -> int:
        cur = self._db.execute(
            "SELECT COUNT(*) AS n FROM screen_log"
            " WHERE e164 = ? AND at >= ? AND verdict != 'Block'",
            (e164, since_ms))
        row = cur.fetchone()
        return int(row["n"]) if row else 0

    def tallies_since(self, since_ms: int) -> dict[str, int]:
        cur = self._db.execute(
            "SELECT verdict, COUNT(*) AS n FROM screen_log"
            " WHERE at >= ? GROUP BY verdict", (since_ms,))
        return {r["verdict"]: r["n"] for r in cur.fetchall()}

    def clear_screen_log(self) -> None:
        self._execute("DELETE FROM screen_log")

    # ---- call_log ----------------------------------------------------------

    def add_call(self, row: CallRow) -> int:
        cur = self._execute(
            "INSERT INTO call_log (number, type, date, duration, cachedName)"
            " VALUES (?, ?, ?, ?, ?)",
            (row.number, row.call_type, row.date, row.duration,
             row.cached_name),
        )
        return cur.lastrowid or 0

    def call_count(self) -> int:
        cur = self._db.execute("SELECT COUNT(*) AS n FROM call_log")
        row = cur.fetchone()
        return int(row["n"]) if row else 0

    def has_call(self, number: str | None, date: int) -> bool:
        if number is None:
            cur = self._db.execute(
                "SELECT 1 FROM call_log WHERE number IS NULL AND date = ?",
                (date,))
        else:
            cur = self._db.execute(
                "SELECT 1 FROM call_log WHERE number = ? AND date = ?",
                (number, date))
        return cur.fetchone() is not None

    def has_outgoing_since(self, number: str, since_ms: int) -> bool:
        cur = self._db.execute(
            "SELECT 1 FROM call_log WHERE number = ? AND type = ?"
            " AND date >= ? LIMIT 1",
            (number, CALL_TYPE_OUTGOING, since_ms))
        return cur.fetchone() is not None

    def calls_for(self, number: str, limit: int = 50) -> list[CallRow]:
        cur = self._db.execute(
            "SELECT id, number, type, date, duration, cachedName"
            " FROM call_log WHERE number = ? ORDER BY date DESC LIMIT ?",
            (number, limit))
        return [_call_from(r) for r in cur.fetchall()]

    def clear_calls(self) -> None:
        self._execute("DELETE FROM call_log")

    def recent_calls(self, limit: int = 200) -> list[CallRow]:
        cur = self._db.execute(
            "SELECT id, number, type, date, duration, cachedName"
            " FROM call_log ORDER BY date DESC LIMIT ?", (limit,))
        return [_call_from(r) for r in cur.fetchall()]

    def delete_call(self, row_id: int) -> bool:
        """Delete THIS call_log row; the screen_log audit row stays."""
        cur = self._execute("DELETE FROM call_log WHERE id = ?",
                            (row_id,))
        return (cur.rowcount or 0) > 0

    # ---- recents_state (missed badge watermark + digest clock) -----------

    def get_recents_viewed(self) -> int:
        cur = self._db.execute(
            "SELECT viewedAt FROM recents_state WHERE id = 1")
        row = cur.fetchone()
        return int(row["viewedAt"]) if row is not None else 0

    def set_recents_viewed(self, at_ms: int) -> None:
        self._execute(
            "INSERT INTO recents_state (id, viewedAt, digestFlushedAt)"
            " VALUES (1, ?, 0)"
            " ON CONFLICT (id) DO UPDATE SET viewedAt = ?",
            (int(at_ms), int(at_ms)),
        )

    def get_digest_flushed_at(self) -> int:
        cur = self._db.execute(
            "SELECT digestFlushedAt FROM recents_state WHERE id = 1")
        row = cur.fetchone()
        return int(row["digestFlushedAt"]) if row is not None else 0

    def set_digest_flushed_at(self, at_ms: int) -> None:
        self._execute(
            "INSERT INTO recents_state (id, viewedAt, digestFlushedAt)"
            " VALUES (1, 0, ?)"
            " ON CONFLICT (id) DO UPDATE SET digestFlushedAt = ?",
            (int(at_ms), int(at_ms)),
        )

    # ---- silence_digest (daily-summary stash) ------------------------------

    def stash_silenced(self, at_ms: int, e164: str | None,
                       reason: str, cap: int = 200) -> None:
        self._execute(
            "INSERT INTO silence_digest (at, e164, reason)"
            " VALUES (?, ?, ?)", (int(at_ms), e164, reason or ""))
        self._execute(
            "DELETE FROM silence_digest WHERE id NOT IN"
            " (SELECT id FROM silence_digest ORDER BY at DESC LIMIT ?)",
            (int(cap),))

    def read_digest(self) -> list[tuple[int, str | None, str]]:
        cur = self._db.execute(
            "SELECT at, e164, reason FROM silence_digest ORDER BY at")
        return [(int(r["at"]), r["e164"], r["reason"])
                for r in cur.fetchall()]

    def clear_digest(self) -> None:
        self._execute("DELETE FROM silence_digest")

    # ---- posted_notif (silenced-card server ids) ---------------------------
    # Server ids the policy service posted its Call back / Ring next time
    # / Block cards under. HandleNotificationAction only acts on these, so
    # a tap still works after the service idle-exited and the bus
    # re-activated it (memory alone would not survive the restart).

    def record_posted_notif(self, server_id: int, e164: str | None,
                            posted_at: int, cap: int = 64) -> None:
        try:
            notif_id = int(server_id)
        except (TypeError, ValueError):
            return
        if notif_id <= 0:
            return
        self._execute(
            "INSERT INTO posted_notif (serverId, e164, postedAt)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT (serverId) DO UPDATE SET e164 = ?, postedAt = ?",
            (notif_id, e164 or "", int(posted_at), e164 or "",
             int(posted_at)),
        )
        self._execute(
            "DELETE FROM posted_notif WHERE serverId NOT IN"
            " (SELECT serverId FROM posted_notif ORDER BY postedAt DESC"
            " LIMIT ?)",
            (int(cap),),
        )

    def is_posted_notif(self, server_id: int) -> bool:
        try:
            notif_id = int(server_id)
        except (TypeError, ValueError):
            return False
        cur = self._db.execute(
            "SELECT 1 FROM posted_notif WHERE serverId = ?",
            (notif_id,))
        return cur.fetchone() is not None

    def notif_number(self, server_id: int) -> str:
        """E.164 recorded for a posted card, or "" when unknown."""
        try:
            notif_id = int(server_id)
        except (TypeError, ValueError):
            return ""
        cur = self._db.execute(
            "SELECT e164 FROM posted_notif WHERE serverId = ?",
            (notif_id,))
        row = cur.fetchone()
        return str(row["e164"] or "") if row is not None else ""

    def notif_posted_at(self, server_id: int) -> int:
        """When a posted card went up, or 0 when unknown.

        Acknowledging a card advances the seen watermark to this, which
        covers the card without touching calls that arrived after it.
        """
        try:
            notif_id = int(server_id)
        except (TypeError, ValueError):
            return 0
        cur = self._db.execute(
            "SELECT postedAt FROM posted_notif WHERE serverId = ?",
            (notif_id,))
        row = cur.fetchone()
        try:
            return int(row["postedAt"]) if row is not None else 0
        except (TypeError, ValueError):
            return 0

    # ---- emergency_marker ---------------------------------------------------

    def record_emergency(self, wall_ms: int, elapsed_ms: int,
                         boot_id: str | None = None) -> None:
        boot = boot_id if boot_id is not None else current_boot_id()
        self._execute(
            "INSERT INTO emergency_marker"
            " (id, lastEmergencyCallAt, lastEmergencyElapsed,"
            " lastEmergencyBoot)"
            " VALUES (1, ?, ?, ?)"
            " ON CONFLICT (id) DO UPDATE SET"
            " lastEmergencyCallAt = excluded.lastEmergencyCallAt,"
            " lastEmergencyElapsed = excluded.lastEmergencyElapsed,"
            " lastEmergencyBoot = excluded.lastEmergencyBoot",
            (wall_ms, elapsed_ms, boot or ""),
        )

    def read_emergency(self) -> tuple[int, int] | None:
        cur = self._db.execute(
            "SELECT lastEmergencyCallAt, lastEmergencyElapsed"
            " FROM emergency_marker WHERE id = 1")
        r = cur.fetchone()
        if r is None:
            return None
        return (r["lastEmergencyCallAt"], r["lastEmergencyElapsed"])

    def read_emergency_boot(self) -> str | None:
        cur = self._db.execute(
            "SELECT lastEmergencyBoot FROM emergency_marker WHERE id = 1")
        r = cur.fetchone()
        if r is None:
            return None
        return str(r["lastEmergencyBoot"] or "") or None

    def emergency_active(self, now_wall_ms: int,
                         now_elapsed_ms: int,
                         now_boot_id: str | None = None) -> bool:
        marker = self.read_emergency()
        if marker is None:
            return False
        return emergency_window_active(
            now_wall_ms, marker[0], now_elapsed_ms, marker[1],
            now_boot_id, self.read_emergency_boot())


def _screen_from(r: sqlite3.Row) -> ScreenRow:
    return ScreenRow(at=r["at"], verdict=r["verdict"], reason=r["reason"],
                     e164=r["e164"], presentation=r["presentation"],
                     tier=r["tier"], stir=r["stir"],
                     cnap_name=r["cnapName"], mode=r["mode"],
                     answered=bool(r["answered"]), row_id=r["id"])


def _call_from(r: sqlite3.Row) -> CallRow:
    return CallRow(date=r["date"], number=r["number"],
                   call_type=r["type"], duration=r["duration"],
                   cached_name=r["cachedName"], row_id=r["id"])


# ---- pure settings seams (ports of SettingsRepository / RulesProvider) -----

def window_to_json(window: policy.Window) -> str:
    """Canonical stored form: {"startMinute":..,"endMinute":..,"daysMask":..}."""
    return json.dumps({"startMinute": window.start_minute,
                       "endMinute": window.end_minute,
                       "daysMask": window.days_mask},
                      separators=(",", ":"))


def parse_window(raw: str | None) -> policy.Window | None:
    """Stored JSON back to a Window; garbage yields None, never raises."""
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    try:
        return policy.Window(int(parsed["startMinute"]),
                             int(parsed["endMinute"]),
                             int(parsed["daysMask"]))
    except (KeyError, TypeError, ValueError):
        return None


def safe_enum(raw: str | None, allowed: set[str], default: str) -> str:
    """Case-insensitive enum parse with surrounding-whitespace tolerance."""
    if raw is not None:
        candidate = raw.strip().lower()
        if candidate in allowed:
            return candidate
    return default


def should_offer_enforcement(mode: str, now_ms: int,
                             week_end_ms: int | float | None) -> bool:
    """True only while observing and at/past the seeded week boundary.

    A missing or zero week end means Setup never seeded the observe week,
    which reads as unseeded — never offered.
    """
    if not week_end_ms:
        return False
    return mode == "observing" and now_ms >= week_end_ms


def observe_week_end_seed(now_ms: int) -> str:
    return str(now_ms + 7 * 24 * 60 * 60 * 1000)


_DEFAULT_UNKNOWN = policy.Window(9 * 60, 17 * 60, policy.ALL_DAYS)
_DEFAULT_BUSINESS = policy.Window(9 * 60, 19 * 60, policy.ALL_DAYS)


def assemble_rules(settings: dict, rows: list[PatternRow]) -> policy.Rules:
    """Stored strings + pattern rows → engine Rules; never raises.

    Every parse failure degrades to that field's shipped default (fail open
    toward ringing). Corrupt pattern rows are skipped, never rewritten into
    different rules — a rule the user never wrote must never block.
    """
    def window(key: str, fallback: policy.Window) -> policy.Window:
        return parse_window(settings.get(key)) or fallback

    def patterns(action: str) -> list[policy.PatternRule]:
        out = []
        for row in rows:
            if row.action != action:
                continue
            prefix = row.e164_prefix.strip()
            if not prefix or not prefix.isdigit():
                continue
            try:
                out.append(policy.PatternRule(prefix, row.wildcards))
            except ValueError:
                continue
        return out

    return policy.Rules(
        unknown_window=window("unknown_window", _DEFAULT_UNKNOWN),
        business_window=window("business_window", _DEFAULT_BUSINESS),
        hidden_caller_policy=policy.HiddenCallerPolicy(
            safe_enum(settings.get("hidden_caller_policy"),
                      {"unknown", "silence", "block"}, "unknown")),
        stir_action=policy.StirAction(
            safe_enum(settings.get("stir_action"),
                      {"block", "silence", "off"}, "block")),
        repeat_caller_enabled=settings.get("repeat_caller_enabled") != "0",
        bypass_until=_parse_bypass(settings.get("bypass_until")),
        block_patterns=patterns("block"),
        silence_patterns=patterns("silence"),
    )


def _parse_bypass(raw: object) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromtimestamp(int(float(text)) / 1000)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def load_rules(store: Store, config: dict) -> policy.Rules:
    """Assemble engine Rules from the JSON config + stored pattern rows."""
    settings = config_to_settings(config)
    return assemble_rules(settings, store.all_patterns())


def config_to_settings(config: dict) -> dict:
    """Map config.json keys onto the Android setting-key vocabulary."""
    settings: dict = {}
    for key, start, end in (("unknown_window", 540, 1020),
                            ("business_window", 540, 1140)):
        window = config.get(key)
        if not isinstance(window, dict):
            continue
        try:
            settings[key] = window_to_json(
                policy.Window(window.get("start", start),
                               window.get("end", end),
                               window.get("days", policy.ALL_DAYS)))
        except (TypeError, ValueError):
            continue
    for key in ("hidden_caller_policy", "stir_action"):
        value = config.get(key)
        if isinstance(value, str):
            settings[key] = value
    repeat = config.get("repeat_caller_enabled")
    if isinstance(repeat, bool):
        settings["repeat_caller_enabled"] = "1" if repeat else "0"
    bypass = config.get("bypass_until")
    if bypass is not None:
        settings["bypass_until"] = str(bypass)
    return settings


# ---- emergency window (port of telecom/EmergencyWindow) --------------------

_BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
_boot_id_read = False
_cached_boot_id: str | None = None


def current_boot_id() -> str | None:
    """Kernel boot id, cached: None where the platform has no boot id."""
    global _boot_id_read, _cached_boot_id
    if _boot_id_read:
        return _cached_boot_id
    _boot_id_read = True
    try:
        with open(_BOOT_ID_PATH, encoding="utf-8") as handle:
            _cached_boot_id = handle.read().strip() or None
    except OSError:
        _cached_boot_id = None
    return _cached_boot_id


def boottime_ms() -> int:
    """ms since boot including suspend; monotonic fallback off Linux."""
    try:
        return int(time.clock_gettime(time.CLOCK_BOOTTIME) * 1000)
    except (AttributeError, OSError):
        return int(time.monotonic() * 1000)


def _boot_mismatch(now_boot_id: str | None,
                   marker_boot_id: str | None) -> bool:
    """True when the elapsed reading provably comes from another boot."""
    return now_boot_id is not None and marker_boot_id != now_boot_id


def emergency_window_active(now_wall_ms: int, marker_wall_ms: int | None,
                            now_elapsed_ms: int,
                            marker_elapsed_ms: int | None,
                            now_boot_id: str | None = None,
                            marker_boot_id: str | None = None) -> bool:
    """The 24 h post-emergency bypass closes only when both usable readings
    agree it is over; every single-reading corruption errs toward open.

    The elapsed reading is usable only on the boot that wrote it: the
    monotonic/boottime clock restarts at zero on every boot, so a
    post-reboot value that merely aliases the marker must not reopen
    the window — the wall clock alone decides then.
    """
    wall_fresh = marker_wall_ms is not None \
        and _fresh(marker_wall_ms, now_wall_ms)
    elapsed_usable = marker_elapsed_ms is not None \
        and now_elapsed_ms >= marker_elapsed_ms \
        and not _boot_mismatch(now_boot_id, marker_boot_id)
    elapsed_fresh = elapsed_usable \
        and _fresh(marker_elapsed_ms, now_elapsed_ms)
    return wall_fresh or elapsed_fresh


def _fresh(marker_ms: int, now_ms: int) -> bool:
    return now_ms - marker_ms < EMERGENCY_WINDOW_MS


def in_repeat_window(now_ms: int, last_at_ms: int) -> bool:
    age = now_ms - last_at_ms
    return 0 <= age < REPEAT_MS


def in_recent_outgoing_window(now_ms: int, last_at_ms: int) -> bool:
    age = now_ms - last_at_ms
    return 0 <= age < RECENT_OUTGOING_MS
