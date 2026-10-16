"""Importers: Android exports, blocklist files, GNOME Calls history.

All entry points are tolerant: garbage in yields an empty result plus a
warning count, never an exception for malformed *content* (missing files,
unreadable databases and corrupt envelopes degrade to zero imports).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from xx_dialer import codecs, config as config_mod
from xx_dialer.codecs import (
    CallLogEntry,
    PatternSnapshot,
    RingPolicySnapshot,
    TierSnapshot,
)
from xx_dialer.store import (
    BIZ_TIER,
    BLOCKED_GROUP,
    CALL_TYPE_INCOMING,
    CALL_TYPE_MISSED,
    CALL_TYPE_OUTGOING,
    CallRow,
    PatternRow,
    Store,
)

_NON_DIGIT = re.compile(r"[^\d]")

GNOME_CALLS_DB = Path.home() / ".local" / "share" / "calls" / "calls.db"
GNOME_TABLE = "calls_call_records"


@dataclass
class ApplyReport:
    patterns_added: int = 0
    tiers_added: int = 0
    config_keys_written: int = 0


@dataclass
class CallLogReport:
    inserted: int = 0
    duplicates: int = 0


@dataclass
class ParsedBlocklist:
    numbers: list[str] = field(default_factory=list)
    skipped: int = 0


@dataclass
class GnomeImportResult:
    imported: int = 0
    skipped: int = 0


# ---- policy snapshot: validate / sanitize / apply / export ----------------

def validate_snapshot(snapshot: RingPolicySnapshot) -> list[str]:
    """Structural check before any write; each violation names itself."""
    errors = []
    for i, tier in enumerate(snapshot.tiers):
        if not tier.lookup_key.strip():
            errors.append(f"tier[{i}]: blank lookupKey")
        elif tier.tier.strip() in ("", "star"):
            errors.append(f"tier[{i}]: bad tier '{tier.tier}'")
    for i, pattern in enumerate(snapshot.patterns):
        if not pattern.e164_prefix.strip():
            errors.append(f"pattern[{i}]: blank prefix")
        elif pattern.wildcards < 0:
            errors.append(f"pattern[{i}]: negative wildcards"
                          f" {pattern.wildcards}")
        elif pattern.action not in ("block", "silence"):
            errors.append(f"pattern[{i}]: unknown action"
                          f" '{pattern.action}'")
    return errors


def sanitize_for_import(snapshot: RingPolicySnapshot) -> RingPolicySnapshot:
    """A backup is user data, never a mandate: enforcement is forced back
    to observing no matter what the payload says."""
    snapshot.enforcement_mode = "observing"
    return snapshot


def apply_policy(snapshot: RingPolicySnapshot, app_config: dict,
                 store: Store) -> ApplyReport:
    """Write a decoded snapshot into the live config + database.

    Windows, hidden/STIR/repeat/ring/tone policies land in config.json;
    patterns replace ``pattern_rule`` wholesale and tiers replace
    ``tier_member`` wholesale, inside one SQLite transaction, so a bad
    payload can never leave half a restore behind. Enforcement always
    lands observing (imported enforcement is offered, never flipped).
    """
    report = ApplyReport()
    errors = validate_snapshot(snapshot)
    if errors:
        raise ValueError("; ".join(errors))
    sanitize_for_import(snapshot)

    window = snapshot.unknown_window
    if _valid_window(window.start_minute, window.end_minute,
                      window.days_mask):
        app_config["unknown_window"] = {"start": window.start_minute,
                                        "end": window.end_minute,
                                        "days": window.days_mask}
        report.config_keys_written += 1
    window = snapshot.business_window
    if _valid_window(window.start_minute, window.end_minute,
                      window.days_mask):
        app_config["business_window"] = {"start": window.start_minute,
                                         "end": window.end_minute,
                                         "days": window.days_mask}
        report.config_keys_written += 1
    enums = (
        ("hidden_caller_policy",
         {v.lower() for v in ("unknown", "silence", "block")},
         snapshot.hidden_caller_policy),
        ("stir_action", {"block", "silence", "off"}, snapshot.stir_action),
        ("ring_repeat", {"once", "twice", "until_vm"}, snapshot.ring_repeat),
        ("silenced_notif_policy", {"immediate", "daily", "never"},
         snapshot.silenced_notif_policy),
        ("answer_interaction", {"tap", "slider"},
         snapshot.answer_interaction),
    )
    for key, allowed, raw in enums:
        value = str(raw).strip().lower()
        if value in allowed:
            app_config[key] = value
            report.config_keys_written += 1
    app_config["repeat_caller_enabled"] = bool(snapshot.repeat_caller_enabled)
    report.config_keys_written += 1
    app_config["group_recents"] = bool(snapshot.group_recents_enabled)
    report.config_keys_written += 1
    app_config["enforcement_mode"] = "observing"
    report.config_keys_written += 1

    patterns = [PatternRow(e164_prefix=p.e164_prefix,
                           wildcards=p.wildcards, action=p.action,
                           preset=p.preset, created_at=p.created_at)
                for p in snapshot.patterns]
    tiers = [(t.lookup_key, t.tier, t.added_at) for t in snapshot.tiers]
    store.replace_policy_tables(patterns, tiers)
    report.patterns_added = len(patterns)
    report.tiers_added = len(tiers)
    return report


def _valid_window(start: int, end: int, days: int) -> bool:
    return (isinstance(start, int) and isinstance(end, int)
            and isinstance(days, int)
            and not isinstance(start, bool) and not isinstance(end, bool)
            and not isinstance(days, bool)
            and 0 <= start < 24 * 60 and 0 <= end < 24 * 60
            and 1 <= days <= 127)


def export_policy(app_config: dict, store: Store) -> RingPolicySnapshot:
    """Read the live config + database back into an exportable snapshot."""
    unknown = app_config.get("unknown_window", {})
    business = app_config.get("business_window", {})
    patterns = [PatternSnapshot(p.e164_prefix, p.wildcards, p.action,
                                p.preset, p.created_at)
                for p in store.all_patterns()]
    tiers = [TierSnapshot(key, tier, added)
             for key, tier, added in store.all_tiers()]
    return RingPolicySnapshot(
        enforcement_mode=str(app_config.get("enforcement_mode",
                                            "observing")),
        unknown_window=codecs.WindowSnapshot(
            _window_part(unknown, "start", 540),
            _window_part(unknown, "end", 1020),
            _window_part(unknown, "days", 127)),
        business_window=codecs.WindowSnapshot(
            _window_part(business, "start", 540),
            _window_part(business, "end", 1140),
            _window_part(business, "days", 127)),
        hidden_caller_policy=str(app_config.get("hidden_caller_policy",
                                                "unknown")),
        stir_action=str(app_config.get("stir_action", "block")),
        repeat_caller_enabled=bool(
            app_config.get("repeat_caller_enabled", True)),
        ring_repeat=str(app_config.get("ring_repeat", "once")),
        silenced_notif_policy=str(app_config.get("silenced_notif_policy",
                                                 "immediate")),
        answer_interaction=str(app_config.get("answer_interaction", "tap")),
        group_recents_enabled=bool(app_config.get("group_recents", True)),
        tiers=tiers,
        patterns=patterns,
    )


def _window_part(window: object, name: str, default: int) -> int:
    if isinstance(window, dict):
        value = window.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return default


def import_policy_text(text: str, app_config: dict,
                       store: Store) -> ApplyReport | None:
    """Decode ring_policy.json text and apply it; None when undecodable."""
    snapshot = codecs.decode_policy(text)
    if snapshot is None:
        return None
    return apply_policy(snapshot, app_config, store)


def default_config() -> dict:
    return {k: (dict(v) if isinstance(v, dict) else
                list(v) if isinstance(v, list) else v)
            for k, v in config_mod.DEFAULTS.items()}


# ---- call log --------------------------------------------------------------

def import_calllog(entries: list[CallLogEntry],
                   store: Store) -> CallLogReport:
    """Insert entries not already present, matched on number+date."""
    report = CallLogReport()
    for entry in entries:
        if store.has_call(entry.number, entry.date):
            report.duplicates += 1
            continue
        store.add_call(CallRow(date=entry.date, number=entry.number,
                               call_type=entry.call_type,
                               duration=entry.duration,
                               cached_name=entry.cached_name))
        report.inserted += 1
    return report


def import_calllog_text(text: str, store: Store) -> CallLogReport:
    return import_calllog(codecs.decode_calllog(text), store)


def export_calllog(store: Store, limit: int = 2000) -> str:
    """Read the live call_log back into ``calllog.json`` text (schema 1).

    Same shape the Android exporter writes, so the file restores on
    either side; newest first.
    """
    entries = [CallLogEntry(number=row.number, call_type=row.call_type,
                            date=row.date, duration=row.duration,
                            cached_name=row.cached_name)
               for row in store.recent_calls(limit)]
    return codecs.encode_calllog(entries)


# ---- blocklist file (one number per line) -----------------------------------

NANP_REGIONS = frozenset({
    "US", "CA", "AG", "AI", "AS", "BB", "BM", "BS", "DM", "DO", "GD", "GU",
    "JM", "KN", "KY", "LC", "MP", "MS", "PR", "TC", "TT", "VC", "VG", "VI",
})


def normalize_blocklist_number(line: str,
                               default_region: str = "US") -> str | None:
    """Region-aware normalization for offline blocklist imports.

    International forms (leading ``+``/``00``) parse anywhere; bare
    national numbers need the region's trunk rule — only NANP (+1, ten
    digits) is implemented, anything else must arrive international.
    Garbage yields None: unparseable lines are counted, never fatal.
    """
    text = line.strip()
    if not text:
        return None
    if text.startswith("+"):
        digits = _NON_DIGIT.sub("", text)
        if 7 <= len(digits) <= 15:
            return "+" + digits
        return None
    if text.startswith("00"):
        digits = _NON_DIGIT.sub("", text)[2:]
        if 7 <= len(digits) <= 15:
            return "+" + digits
        return None
    digits = _NON_DIGIT.sub("", text)
    region = (default_region or "US").upper()
    if region in NANP_REGIONS:
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
        return None
    if 7 <= len(digits) <= 15 and digits.startswith("0"):
        return None
    return None if len(digits) < 7 or len(digits) > 15 else digits


def parse_blocklist(text: str, default_region: str = "US") -> ParsedBlocklist:
    """Lines to E.164 numbers. Blank lines and ``#`` comments are file
    structure, not garbage; duplicates are kept for the insert layer
    (INSERT OR IGNORE) to reconcile."""
    parsed = ParsedBlocklist()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        number = normalize_blocklist_number(stripped, default_region)
        if number is None:
            parsed.skipped += 1
        else:
            parsed.numbers.append(number)
    return parsed


def import_blocklist_text(text: str, store: Store,
                          default_region: str = "US") -> ParsedBlocklist:
    parsed = parse_blocklist(text, default_region)
    for number in parsed.numbers:
        store.add_blocked(number)
    return parsed


# ---- GNOME Calls history ----------------------------------------------------
#
# Assumed shape (verified against upstream GNOME/calls
# src/calls-call-record.c: GomResource "CallsCallRecord" with properties
# id/target/inbound/start/answered/end/protocol, persisted by libgom):
#   table "calls_call_records": target TEXT, inbound INTEGER 0/1,
#   start/answered/end GDateTime-as-ISO-8601-TEXT (epoch ints tolerated),
#   protocol TEXT.
# Anything else — missing file, unknown tables, unparsable stamps — is
# ignored silently: history is a nice-to-have, and the repeat/recent
# queries work from the day of install regardless.

_NUMBER_COLUMNS = ("target", "number", "phone_number", "remote_number",
                   "remote", "address", "peer")
_INBOUND_COLUMNS = ("inbound", "incoming", "is_incoming")
_DIRECTION_COLUMNS = ("direction", "type", "disposition", "state")
_START_COLUMNS = ("start", "started", "timestamp", "date", "begin",
                  "created")
_ANSWERED_COLUMNS = ("answered",)
_END_COLUMNS = ("end", "ended", "finished", "stop")


def import_gnome_calls(store: Store, source: Path | None = None,
                       only_if_empty: bool = True) -> GnomeImportResult:
    """One-shot import of GNOME Calls history into call_log.

    Runs only when our call_log is empty (first run) unless asked
    otherwise; every failure mode returns zeros without raising.
    """
    result = GnomeImportResult()
    path = source if source is not None else GNOME_CALLS_DB
    try:
        if only_if_empty and store.call_count() > 0:
            return result
        if not Path(path).is_file():
            return result
        remote = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except Exception:
        return result
    try:
        table = _pick_table(remote)
        if table is None:
            return result
        columns = _columns(remote, table)
        number_col = _first_present(columns, _NUMBER_COLUMNS)
        start_col = _first_present(columns, _START_COLUMNS)
        if number_col is None or start_col is None:
            return result
        for row in remote.execute('SELECT * FROM "%s"' % table):
            entry = _gnome_row(dict(row), number_col, start_col, columns)
            if entry is None:
                result.skipped += 1
                continue
            if store.has_call(entry.number, entry.date):
                continue
            store.add_call(CallRow(date=entry.date, number=entry.number,
                                   call_type=entry.call_type,
                                   duration=entry.duration))
            result.imported += 1
    except Exception:
        return result
    finally:
        try:
            remote.close()
        except Exception:
            pass
    return result


def _pick_table(remote: sqlite3.Connection) -> str | None:
    remote.row_factory = sqlite3.Row
    cur = remote.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")
    tables = [r["name"] for r in cur.fetchall()]
    if GNOME_TABLE in tables:
        return GNOME_TABLE
    for name in tables:
        columns = _columns(remote, name)
        if _first_present(columns, _NUMBER_COLUMNS) is not None \
                and _first_present(columns, _START_COLUMNS) is not None:
            return name
    return None


def _columns(remote: sqlite3.Connection, table: str) -> list[str]:
    cur = remote.execute('PRAGMA table_info("%s")' % table)
    return [r["name"] for r in cur.fetchall()]


def _first_present(columns: list[str], names: tuple[str, ...]) -> str | None:
    lowered = {c.lower(): c for c in columns}
    for name in names:
        if name in lowered:
            return lowered[name]
    return None


def _gnome_row(row: dict, number_col: str, start_col: str,
               columns: list[str]) -> CallLogEntry | None:
    number = row.get(number_col)
    if number is not None:
        number = str(number).strip() or None
    start_ms = _to_epoch_ms(row.get(start_col))
    if start_ms is None:
        return None
    inbound_col = _first_present(columns, _INBOUND_COLUMNS)
    direction_col = _first_present(columns, _DIRECTION_COLUMNS)
    answered_col = _first_present(columns, _ANSWERED_COLUMNS)
    answered: bool | None = None
    if answered_col is not None:
        answered = _to_epoch_ms(row.get(answered_col)) is not None
    call_type = _gnome_type(row, inbound_col, direction_col, answered)
    duration = 0
    end_col = _first_present(columns, _END_COLUMNS)
    if end_col is not None and call_type != CALL_TYPE_MISSED:
        end_ms = _to_epoch_ms(row.get(end_col))
        if end_ms is not None and end_ms > start_ms:
            duration = (end_ms - start_ms) // 1000
    return CallLogEntry(number=number, call_type=call_type, date=start_ms,
                        duration=duration)


def _gnome_type(row: dict, inbound_col: str | None,
                direction_col: str | None,
                answered: bool | None) -> int:
    if inbound_col is not None:
        inbound = _to_bool(row.get(inbound_col))
        if inbound is True:
            return CALL_TYPE_INCOMING if answered else CALL_TYPE_MISSED
        if inbound is False:
            return CALL_TYPE_OUTGOING
    if direction_col is not None:
        text = str(row.get(direction_col) or "").strip().lower()
        if "out" in text:
            return CALL_TYPE_OUTGOING
        if "miss" in text or "reject" in text:
            return CALL_TYPE_MISSED
        if text.startswith("in"):
            return CALL_TYPE_INCOMING if answered is not False \
                else CALL_TYPE_MISSED
    if inbound_col is None and direction_col is None:
        return CALL_TYPE_INCOMING
    return CALL_TYPE_INCOMING if answered else CALL_TYPE_MISSED


def _to_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes"):
            return True
        if text in ("0", "false", "no"):
            return False
    return None


def _to_epoch_ms(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        if number >= 10**14:
            return number // 1000
        if number >= 10**11:
            return number
        if number >= 10**8:
            return number * 1000
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "0":
            return None
        if text.replace(".", "", 1).isdigit():
            try:
                return _to_epoch_ms(float(text))
            except (TypeError, ValueError):
                return None
        return _parse_iso8601(text)
    return None


def _parse_iso8601(text: str) -> int | None:
    candidate = text.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        return int(moment.timestamp() * 1000)
    except (OverflowError, OSError):
        return None


# ---- tier helpers shared with the tags cache --------------------------------

def is_blocked_group(name: str) -> bool:
    return name.strip().lower() in ("blocked", "block")


def blocked_keys(store: Store) -> set[str]:
    return set(store.keys_for_tier(BLOCKED_GROUP))


def biz_keys(store: Store) -> set[str]:
    return set(store.keys_for_tier(BIZ_TIER))
