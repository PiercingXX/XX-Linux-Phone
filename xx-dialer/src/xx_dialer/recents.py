"""Recents list logic: pure port of the Android Recents tab, stdlib only.

Covers ``ui/RecentsMerge.kt`` (merge + filters + grouping),
``ui/VerdictLines.kt`` (the per-row reason line), ``ring/SilenceBatcher.kt``
(burst batching), ``ring/MissedNotifContent.kt`` (missed copy) and
``ring/MissedCallsClear.kt`` (viewed watermark semantics), plus the small
formatters the rows need (relative day labels, caller names, the recent-
outgoing weekday hint).

Nothing here imports gi or touches D-Bus: the GTK pages in
``ui_recents.py`` and the notifier in ``ringer.py`` consume these shapes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re

from xx_dialer.store import (
    CALL_TYPE_INCOMING,
    CALL_TYPE_MISSED,
    CALL_TYPE_OUTGOING,
)

PROXIMITY_MS = 2 * 60 * 1000

BATCH_WINDOW_MS = 60_000

DIGEST_CAP = 200
DIGEST_PERIOD_MS = 24 * 60 * 60 * 1000

INBOX_LIMIT = 5

RECENT_OUTGOING_MS = 48 * 60 * 60 * 1000

TOKEN_BLOCK = "Block"
TOKEN_SILENCE = "Silence"
TOKEN_RING = "Ring"

GLYPH_BLOCKED = "blocked"
GLYPH_SILENCED = "silenced"
GLYPH_RING = "ring"
GLYPH_NONE = "none"

FILTER_ALL = "all"
FILTER_MISSED = "missed"
FILTER_SILENCED = "silenced"
FILTER_BLOCKED = "blocked"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass
class PlatformCall:
    """One call_log row normalized for the merge."""

    key: int
    time_ms: int
    raw: str | None = None
    e164: str | None = None
    cached_name: str | None = None
    missed: bool = False
    outgoing: bool = False
    duration_s: int = 0


@dataclass
class ScreenEntry:
    """One screen_log row normalized for the merge."""

    row_id: int
    at: int
    e164: str | None = None
    verdict: str = ""
    reason: str = ""
    mode: str = "observed"
    answered: bool = False


@dataclass
class MergedCall:
    """One Recents row: the call plus the rule that fired, if any."""

    key: int
    e164: str | None
    raw: str | None
    cached_name: str | None
    display_name: str | None
    time_ms: int
    missed: bool
    outgoing: bool
    duration_s: int
    glyph: str = GLYPH_NONE
    reason: str = ""
    observed: bool = False
    screen_id: int | None = None

    @property
    def identity(self) -> str | None:
        """Withheld numbers never group and never deep-link-match."""
        return self.e164


@dataclass
class Single:
    call: MergedCall


@dataclass
class Group:
    """A collapsed run of consecutive same-number calls, newest first."""

    identity: str
    calls: list[MergedCall] = field(default_factory=list)
    expanded: bool = False


@dataclass
class BatchState:
    last_post_ms: int | None = None
    count: int = 0


@dataclass(frozen=True)
class BatchSingle:
    state: BatchState


@dataclass(frozen=True)
class BatchBatch:
    count: int
    state: BatchState


@dataclass(frozen=True)
class DigestEntry:
    at: int
    e164: str | None
    reason: str


def glyph_of(verdict: str | None) -> str:
    if verdict == TOKEN_BLOCK:
        return GLYPH_BLOCKED
    if verdict == TOKEN_SILENCE:
        return GLYPH_SILENCED
    if verdict == TOKEN_RING:
        return GLYPH_RING
    return GLYPH_NONE


def merge(calls: list[PlatformCall], logs: list[ScreenEntry],
          proximity_ms: int = PROXIMITY_MS) -> list[MergedCall]:
    """Nearest-in-time pairing, each side consumed at most once.

    Candidate pairs inside the proximity window are taken closest-first
    so a rapid redial pairs with its own log row rather than its
    neighbor's. Newest calls come first.
    """
    candidates: list[tuple[int, int, int]] = []
    for ci, call in enumerate(calls):
        for li, log in enumerate(logs):
            if call.e164 is not None or log.e164 is not None:
                if call.e164 != log.e164:
                    continue
            dist = abs(log.at - call.time_ms)
            if dist <= proximity_ms:
                candidates.append((dist, -call.time_ms, (ci, li)))
    candidates.sort(key=lambda item: (item[0], item[1]))
    used_call = [False] * len(calls)
    used_log = [False] * len(logs)
    match_of: dict[int, ScreenEntry] = {}
    for _, _, (ci, li) in candidates:
        if not used_call[ci] and not used_log[li]:
            used_call[ci] = True
            used_log[li] = True
            match_of[ci] = logs[li]
    merged = []
    for ci, call in enumerate(calls):
        log = match_of.get(ci)
        merged.append(MergedCall(
            key=call.key, e164=call.e164, raw=call.raw,
            cached_name=call.cached_name, display_name=None,
            time_ms=call.time_ms, missed=call.missed,
            outgoing=call.outgoing, duration_s=call.duration_s,
            glyph=glyph_of(log.verdict) if log is not None else GLYPH_NONE,
            reason=log.reason if log is not None else "",
            observed=log.mode == "observed" if log is not None else False,
            screen_id=log.row_id if log is not None else None,
        ))
    merged.sort(key=lambda row: row.time_ms, reverse=True)
    return merged


def passes(filter_name: str, call: MergedCall) -> bool:
    if filter_name == FILTER_MISSED:
        return call.missed
    if filter_name == FILTER_SILENCED:
        return call.glyph == GLYPH_SILENCED
    if filter_name == FILTER_BLOCKED:
        return call.glyph == GLYPH_BLOCKED
    return True


def group(items: list[MergedCall], grouping_on: bool,
          expanded: set[str]) -> list[Single | Group]:
    """Collapse consecutive runs sharing one identity.

    Null identities (withheld/unparseable) always stand alone; expansion
    state rides along so toggling never re-reads the store.
    """
    if not grouping_on:
        return [Single(call) for call in items]
    out: list[Single | Group] = []
    i = 0
    while i < len(items):
        identity = items[i].identity
        if identity is None:
            out.append(Single(items[i]))
            i += 1
            continue
        j = i + 1
        while j < len(items) and items[j].identity == identity:
            j += 1
        run = items[i:j]
        if len(run) == 1:
            out.append(Single(run[0]))
        else:
            out.append(Group(identity, list(run),
                             identity in expanded))
        i = j
    return out


def verdict_line(glyph: str, reason: str, observed: bool) -> str:
    """The Recents reason line: who decided, then why.

    The stored reason is already display text (``facts.describe_reason``),
    so this only frames it per disposition. A rung call in watch mode
    loses the suffix: it rang either way, so the suffix only restated the
    glyph. Silenced/blocked rows keep it — there it is the honest
    correction that the phone rang anyway.
    """
    text = (reason or "").strip()
    if glyph == GLYPH_BLOCKED:
        line = "Blocked: %s" % (text or "blocklist")
    elif glyph == GLYPH_SILENCED:
        line = "Silenced: %s" % (text or "policy")
    elif glyph == GLYPH_RING and text:
        line = "Rang: %s" % text
    else:
        return ""
    if observed and glyph in (GLYPH_BLOCKED, GLYPH_SILENCED):
        line += " · watching — it rang"
    return line[:1].upper() + line[1:] if line else line


def direction_chip(call: MergedCall) -> str:
    if call.glyph == GLYPH_BLOCKED:
        return "Blocked"
    if call.missed:
        return "Missed"
    if call.glyph == GLYPH_SILENCED:
        return "Silenced"
    if call.outgoing:
        return "Outgoing"
    return "Incoming"


def is_missed_call(call_type: int, duration_s: int,
                   screen_answered: bool | None) -> bool:
    """Missed-ness for a call_log row plus its paired screen row.

    NoteCall ingests every incoming call as INCOMING (never MISSED), so
    an unanswered incoming reads as missed unless its screen row says it
    connected. Imported rows keep their own MISSED type.
    """
    if call_type == CALL_TYPE_OUTGOING:
        return False
    if call_type == CALL_TYPE_MISSED:
        return True
    if call_type != CALL_TYPE_INCOMING:
        return False
    if screen_answered is True:
        return False
    return duration_s <= 0


def unread_missed(calls: list[MergedCall], viewed_at_ms: int) -> int:
    """Missed rows newer than the Recents watermark: the unread badge."""
    return sum(1 for call in calls
               if call.missed and call.time_ms > viewed_at_ms)


def should_clear(recents_visible: bool,
                 missed_filter_active: bool) -> bool:
    """Recents on screen, or the Missed filter active, clears the badge."""
    return recents_visible or missed_filter_active


_NANP_FORMAT_REGIONS = frozenset({"US", "CA"})

_KNOWN_COUNTRY_CODES = frozenset(
    {"1", "44", "49", "33", "34", "39", "91", "61", "55", "52"})


def _group_national(national: str) -> str:
    """Space-group a national digit string for reading (3-3-rest)."""
    chunks: list[str] = []
    rest = national
    while len(rest) > 4:
        chunks.append(rest[:3])
        rest = rest[3:]
    chunks.append(rest)
    return " ".join(chunks)


def _format_nanp(national10: str) -> str:
    return "(%s) %s-%s" % (
        national10[:3], national10[3:6], national10[6:10])


def display_name_of(call: MergedCall, region: str = "US") -> str:
    if call.display_name:
        return call.display_name
    if call.cached_name and call.cached_name.strip():
        return call.cached_name.strip()
    if call.raw and call.raw.strip():
        return format_number(call.raw, region)
    if call.e164 and call.e164.strip():
        return format_number(call.e164, region)
    return "Withheld"


def format_number(raw: str | None, region: str = "US") -> str:
    """Display-only grouping; the dialed/stored string stays untouched.

    NANP numbers in a NANP region render as ``(614) 555-0142``.
    Other international numbers render as ``+44 207 946 0958``-style
    groupings. Short codes and unknown shapes come back as-is.
    Checked against xx-contacts' ``format_display_number``: its
    international/short-code handling matches, but its NANP shape
    (``+1 555 010 0200``) differs, so the dialer keeps its own copy
    to match the Android ``(206) 555-0133`` reference.
    """
    if raw is None:
        return ""
    text = raw.strip() if isinstance(raw, str) else str(raw).strip()
    if not text:
        return ""
    if "*" in text or "#" in text:
        return text
    if any(ch.isalpha() for ch in text):
        return text
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return text
    region_up = (region or "US").upper()
    is_plus = text.startswith("+")
    is_idd = text.startswith("00")
    if is_plus:
        if not 7 <= len(digits) <= 15:
            return text
        if (region_up in _NANP_FORMAT_REGIONS
                and digits.startswith("1") and len(digits) == 11):
            return _format_nanp(digits[1:])
        for code in sorted(_KNOWN_COUNTRY_CODES,
                           key=len, reverse=True):
            if digits.startswith(code) and len(digits) > len(code):
                return "+%s %s" % (
                    code, _group_national(digits[len(code):]))
        return "+%s" % _group_national(digits)
    if is_idd:
        rest = digits[2:] if digits.startswith("00") else digits
        if not 7 <= len(rest) <= 15:
            return text
        if (region_up in _NANP_FORMAT_REGIONS
                and rest.startswith("1") and len(rest) == 11):
            return _format_nanp(rest[1:])
        for code in sorted(_KNOWN_COUNTRY_CODES,
                           key=len, reverse=True):
            if rest.startswith(code) and len(rest) > len(code):
                return "+%s %s" % (
                    code, _group_national(rest[len(code):]))
        return "+%s" % _group_national(rest)
    if len(digits) > 15:
        return text
    if len(digits) <= 3:
        return text
    if region_up in _NANP_FORMAT_REGIONS:
        if len(digits) == 10:
            return _format_nanp(digits)
        if len(digits) == 11 and digits.startswith("1"):
            return _format_nanp(digits[1:])
    if 4 <= len(digits) <= 15:
        return _group_national(digits)
    return text


def duration_text(duration_s: int, missed: bool) -> str:
    if missed or duration_s <= 0:
        return ""
    return "%d:%02d" % (duration_s // 60, duration_s % 60)


def _day_start_ms(moment_ms: int, tz) -> int:
    local = datetime.fromtimestamp(moment_ms / 1000, tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp() * 1000)


def format_when(date_ms: int, now_ms: int,
                tz=timezone.utc) -> str:
    """Relative day label: today reads HH:MM, yesterday "Yest", and so on."""
    local = datetime.fromtimestamp(date_ms / 1000, tz)
    today_start = _day_start_ms(now_ms, tz)
    day_ms = 24 * 60 * 60 * 1000
    if date_ms >= today_start:
        return "%02d:%02d" % (local.hour, local.minute)
    if date_ms >= today_start - day_ms:
        return "Yest"
    if date_ms >= today_start - 6 * day_ms:
        return local.strftime("%a")
    return "%s %d" % (_MONTHS[local.month - 1], local.day)


def recent_outgoing_weekday(outgoing_times_ms: list[int], call_ms: int,
                            window_ms: int = RECENT_OUTGOING_MS) -> str | None:
    """Weekday of the latest outgoing before this call inside the window."""
    latest = None
    for moment in outgoing_times_ms:
        if moment < call_ms and call_ms - moment <= window_ms:
            if latest is None or moment > latest:
                latest = moment
    if latest is None:
        return None
    return datetime.fromtimestamp(latest / 1000,
                                  timezone.utc).strftime("%a")


def starred_strip(rows: list) -> list[tuple[str, str, str]]:
    """Favorites strip: distinct starred uids, first-seen order."""
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for row in rows:
        if not row.starred or row.lookup_key in seen:
            continue
        seen.add(row.lookup_key)
        out.append((row.lookup_key, row.display_name, row.e164))
    return out


def batch_next(state: BatchState, now_ms: int) -> BatchSingle | BatchBatch:
    """Burst batching: consecutive silences inside the window share a card.

    The window is measured against the last posted card, so a steady drip
    keeps one card refreshing instead of stacking.
    """
    last = state.last_post_ms
    if last is not None and now_ms - last <= BATCH_WINDOW_MS:
        fresh = BatchState(last_post_ms=now_ms, count=state.count + 1)
        return BatchBatch(fresh.count, fresh)
    return BatchSingle(BatchState(last_post_ms=now_ms, count=1))


def missed_title(count: int) -> str:
    if count > 1:
        return "%d missed calls" % count
    return "Missed call"


def missed_display_count(unseen_missed: int) -> int:
    """Prefer Recents-unseen rows; a bare event is still one new call."""
    if unseen_missed > 0:
        return unseen_missed
    return 1


def uses_inbox_style(count: int) -> bool:
    return count > 1


def inbox_lines(entries: list[str],
                limit: int = INBOX_LIMIT) -> list[str]:
    return [line for line in entries if line.strip()][:limit]


def silenced_title(count: int) -> str:
    if count > 1:
        return "%d silenced calls" % count
    return "Silenced call"


def silenced_card_line(number: str | None, reason: str,
                       region: str = "US") -> str:
    """Card body: who, then why — the title already says Silenced."""
    why = (reason or "").strip()
    if why.lower().startswith("silenced"):
        why = why[len("silenced"):].lstrip(" ·:").strip()
    who = format_number(number, region) \
        if number and number.strip() else ""
    return " · ".join(part for part in (who, why) if part) or "Silenced"


def stash_digest(current: list[DigestEntry],
                 entry: DigestEntry,
                 cap: int = DIGEST_CAP) -> list[DigestEntry]:
    return (list(current) + [entry])[-cap:]


def digest_due(now_ms: int, last_flush_ms: int,
               period_ms: int = DIGEST_PERIOD_MS) -> bool:
    return now_ms - last_flush_ms >= period_ms
