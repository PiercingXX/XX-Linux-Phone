"""CallerFacts builder plus the CheckNumber/NoteCall engine core.

Warm SQLite reads only — the mirror, tier, blocklist, call-log,
screen-log and emergency tables plus the JSON config. This module never
touches D-Bus, EDS or the xx-contacts service, so the Policy1 handler
can run it on a worker and answer inside the shell's consult timeout
even on a cold cache. STIR has no modem source on Linux (PORT_SPEC
§2.5), so ``stir_failed`` is always false; the schema column stays for
a future QMI source.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from xx_dialer import policy
from xx_dialer.store import (
    BLOCKED_GROUP,
    CALL_TYPE_INCOMING,
    CALL_TYPE_OUTGOING,
    CallRow,
    ScreenRow,
    Store,
    emergency_window_active,
    in_recent_outgoing_window,
    in_repeat_window,
)

BUILTIN_EMERGENCY_NUMBERS = frozenset({
    "112", "911", "999", "000", "08", "110",
    "118", "119", "100", "101", "102", "108",
})

TIER_STARRED = "starred"
TIER_BUSINESS = "business"
TIER_SAVED = "saved"
TIER_UNKNOWN = "unknown"

DIRECTION_INCOMING = "incoming"
DIRECTION_OUTGOING = "outgoing"

DISPOSITION_ANSWERED = "answered"


def _digits_only(number: str) -> str:
    return "".join(c for c in number if c.isdigit())


def is_emergency_number(e164: str | None) -> bool:
    """Builtin emergency match on the digits-only form (exact, never prefix)."""
    if not e164:
        return False
    return _digits_only(e164) in BUILTIN_EMERGENCY_NUMBERS


def lookup_tier(store: Store, e164: str | None
                ) -> tuple[str, str]:
    """Warm-cache (display_name, tier) for a normalized number, no D-Bus."""
    if not e164:
        return ("", TIER_UNKNOWN)
    row = store.find_mirror(e164)
    if row is None:
        return ("", TIER_UNKNOWN)
    name = row.display_name or ""
    if row.starred or store.is_starred(row.lookup_key):
        return (name, TIER_STARRED)
    if row.biz_tier:
        return (name, TIER_BUSINESS)
    return (name, TIER_SAVED)


def build_facts(raw_number: str | None, now: datetime, store: Store,
                wall_ms: int | None = None,
                monotonic_ms: int | None = None) -> policy.CallerFacts:
    """Assemble engine facts from the warm tables; never raises on lookup."""
    wall = wall_ms if wall_ms is not None else int(time.time() * 1000)
    elapsed = monotonic_ms \
        if monotonic_ms is not None else int(time.monotonic() * 1000)
    if raw_number is None or not str(raw_number).strip():
        return policy.CallerFacts(
            withheld=True,
            emergency_window=store.emergency_active(wall, elapsed),
        )
    e164 = policy.normalize_number(str(raw_number))
    if e164 is None:
        return policy.CallerFacts(
            emergency_window=store.emergency_active(wall, elapsed),
        )
    row = store.find_mirror(e164)
    starred = bool(row is not None and (
        row.starred or store.is_starred(row.lookup_key)))
    biz_tier = bool(row is not None and row.biz_tier)
    send_to_voicemail = bool(row is not None and row.send_to_voicemail)
    group_blocked = False
    if row is not None:
        group_blocked = any(
            key == row.lookup_key and tier == BLOCKED_GROUP
            for key, tier, _ in store.all_tiers())
    return policy.CallerFacts(
        number=e164,
        saved=row is not None,
        starred=starred,
        biz_tier=biz_tier,
        send_to_voicemail=send_to_voicemail,
        user_blocked=store.is_blocked(e164),
        stir_failed=False,
        repeat_caller=store.silence_count_since(
            e164, wall - 15 * 60 * 1000) > 0,
        recent_outgoing=store.has_outgoing_since(
            e164, wall - 48 * 60 * 60 * 1000),
        cnap_name=(row.display_name or None) if row is not None else None,
        emergency_window=store.emergency_active(wall, elapsed),
        withheld=False,
        group_blocked=group_blocked,
    )


def _pierce_reason(facts: policy.CallerFacts, rules: policy.Rules,
                   now: datetime) -> policy.Reason | None:
    if facts.repeat_caller and rules.repeat_caller_enabled:
        return policy.Reason.REPEAT_CALLER
    until = rules.bypass_until
    if until is not None and now <= until \
            and until <= now + timedelta(hours=policy.MAX_BYPASS_HOURS):
        return policy.Reason.EXPECTING_A_CALL
    return None


def describe_reason(verdict: policy.Verdict, facts: policy.CallerFacts,
                    rules: policy.Rules, now: datetime) -> str:
    """Stable reason label for a verdict, walking the decide precedence."""
    if verdict.reason is not None:
        return verdict.reason.value
    if verdict.kind == "block":
        if facts.group_blocked or facts.user_blocked:
            return policy.Reason.USER_BLOCKED.value
        if facts.number is not None and not facts.saved and any(
                p.matches(facts.number) for p in rules.block_patterns):
            return policy.Reason.PATTERN_BLOCKED.value
        if facts.stir_failed:
            return policy.Reason.STIR_FAILED.value
        return policy.Reason.HIDDEN_POLICY.value
    if verdict.kind == "ring":
        if facts.emergency_window:
            return policy.Reason.EMERGENCY_CALLBACK.value
        if facts.starred:
            return policy.Reason.STARRED.value
        if facts.biz_tier:
            if rules.business_window.contains(now):
                return policy.Reason.BUSINESS_IN_WINDOW.value
            pierced = _pierce_reason(facts, rules, now)
            if pierced is not None:
                return pierced.value
            return policy.Reason.BUSINESS_OUTSIDE_WINDOW.value
        if facts.saved:
            return policy.Reason.SAVED.value
        if facts.recent_outgoing:
            return policy.Reason.RECENT_OUTGOING.value
        if facts.number is not None and any(
                p.matches(facts.number) for p in rules.silence_patterns):
            pierced = _pierce_reason(facts, rules, now)
            if pierced is not None:
                return pierced.value
            return policy.Reason.PATTERN_SILENCED.value
        if rules.unknown_window.contains(now):
            return policy.Reason.UNKNOWN_IN_WINDOW.value
        pierced = _pierce_reason(facts, rules, now)
        if pierced is not None:
            return pierced.value
        return policy.Reason.UNKNOWN_OUTSIDE_WINDOW.value
    return "silenced"


def screen_number(raw_number: str | None, now: datetime, store: Store,
                  config: dict, rules: policy.Rules | None = None,
                  wall_ms: int | None = None,
                  monotonic_ms: int | None = None,
                  log: bool = True
                  ) -> tuple[str, str, str]:
    """Full CheckNumber core: (applied verdict, reason, log mode).

    The logged screen row keeps the COMPUTED verdict while the returned
    verdict passes the observe gate, so the observe week rings everything
    yet records what would have happened. ``log=False`` previews the
    verdict without writing the audit row (Test-a-number: what the log
    would say, not a screening that happened).
    """
    from xx_dialer.store import load_rules

    wall = wall_ms if wall_ms is not None else int(time.time() * 1000)
    active_rules = rules if rules is not None \
        else load_rules(store, config)
    facts = build_facts(raw_number, now, store, wall, monotonic_ms)
    computed = policy.decide(now, facts, active_rules)
    enforcing = config.get("enforcement_mode") == "enforcing"
    applied, mode = policy.apply_mode(computed, enforcing)
    reason = describe_reason(computed, facts, active_rules, now)
    if log:
        name, tier = lookup_tier(store, facts.number)
        store.log_screen(ScreenRow(
            at=wall, verdict=computed.token(), reason=reason,
            e164=facts.number, tier=tier, cnap_name=name or None,
            mode=mode,
        ))
    return (applied.kind, reason, mode)


def note_call(store: Store, number: str, direction: str, started_ms: int,
              duration_ms: int, disposition: str,
              wall_ms: int | None = None,
              monotonic_ms: int | None = None) -> dict:
    """Ingest one shell call event into the call log (plus side tables).

    Returns the stored row summary. Unknown dispositions degrade to
    unanswered rather than raising; only an unknown direction raises
    ``ValueError`` (the caller cannot map it to a log type).
    """
    now_wall = wall_ms if wall_ms is not None else int(time.time() * 1000)
    facing = str(direction or "").strip().lower()
    if facing == DIRECTION_INCOMING:
        call_type = CALL_TYPE_INCOMING
    elif facing == DIRECTION_OUTGOING:
        call_type = CALL_TYPE_OUTGOING
    else:
        raise ValueError("unknown direction: %r" % (direction,))
    e164 = policy.normalize_number(number)
    logged = e164 if e164 is not None else str(number or "").strip()
    try:
        started = int(started_ms)
    except (TypeError, ValueError):
        started = 0
    if started <= 0:
        started = now_wall
    try:
        duration_s = max(0, int(duration_ms) // 1000)
    except (TypeError, ValueError):
        duration_s = 0
    answered = str(disposition or "").strip().lower() \
        == DISPOSITION_ANSWERED
    name, _tier = lookup_tier(store, e164)
    store.add_call(CallRow(date=started, number=logged or None,
                           call_type=call_type, duration=duration_s,
                           cached_name=name or None))
    if call_type == CALL_TYPE_INCOMING and logged:
        latest = store.latest_for(logged)
        if latest is not None:
            store.set_answered(latest.row_id, answered)
    if call_type == CALL_TYPE_OUTGOING and is_emergency_number(e164):
        elapsed = monotonic_ms \
            if monotonic_ms is not None else int(time.monotonic() * 1000)
        store.record_emergency(started, elapsed)
    return {"number": logged, "call_type": call_type, "date": started,
            "duration": duration_s, "answered": answered}


def repeat_due(now_ms: int, last_at_ms: int) -> bool:
    return in_repeat_window(now_ms, last_at_ms)


def recent_outgoing_due(now_ms: int, last_at_ms: int) -> bool:
    return in_recent_outgoing_window(now_ms, last_at_ms)


def emergency_due(now_wall_ms: int, marker_wall_ms: int | None,
                  now_elapsed_ms: int,
                  marker_elapsed_ms: int | None) -> bool:
    return emergency_window_active(now_wall_ms, marker_wall_ms,
                                   now_elapsed_ms, marker_elapsed_ms)
