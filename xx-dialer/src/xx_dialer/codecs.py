"""Android backup JSON codecs: ring_policy.json + calllog.json, stdlib only.

Ports backup/RingPolicyJson.kt and backup/CallLogJson.kt field-for-field,
including the decode discipline: garbage or a future schema never throws
and never half-decodes (policy decode yields None, call-log decode yields
[]), rows that fail validation are dropped, missing fields fill the
design defaults. Stars never appear here — they are owned by the
contacts backup, never this app's.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

POLICY_SCHEMA = 1
CALLLOG_SCHEMA = 1

_ALL_DAYS = 0b1111111
_DEFAULT_UNKNOWN = (9 * 60, 17 * 60)
_DEFAULT_BUSINESS = (9 * 60, 19 * 60)


@dataclass
class WindowSnapshot:
    start_minute: int = 540
    end_minute: int = 1020
    days_mask: int = _ALL_DAYS


@dataclass
class TierSnapshot:
    lookup_key: str = ""
    tier: str = ""
    added_at: int = 0


@dataclass
class PatternSnapshot:
    e164_prefix: str = ""
    wildcards: int = 0
    action: str = ""
    preset: str | None = None
    created_at: int = 0


@dataclass
class RingPolicySnapshot:
    enforcement_mode: str = "observing"
    unknown_window: WindowSnapshot = field(
        default_factory=lambda: WindowSnapshot(*_DEFAULT_UNKNOWN,
                                               _ALL_DAYS))
    business_window: WindowSnapshot = field(
        default_factory=lambda: WindowSnapshot(*_DEFAULT_BUSINESS,
                                               _ALL_DAYS))
    hidden_caller_policy: str = "UNKNOWN"
    stir_action: str = "BLOCK"
    repeat_caller_enabled: bool = True
    ring_repeat: str = "once"
    silenced_notif_policy: str = "immediate"
    answer_interaction: str = "tap"
    group_recents_enabled: bool = True
    tiers: list[TierSnapshot] = field(default_factory=list)
    patterns: list[PatternSnapshot] = field(default_factory=list)


@dataclass
class CallLogEntry:
    number: str | None = None
    call_type: int = 0
    date: int = 0
    duration: int = 0
    cached_name: str | None = None


def encode_policy(policy: RingPolicySnapshot) -> str:
    return json.dumps({
        "schema": POLICY_SCHEMA,
        "enforcementMode": policy.enforcement_mode,
        "unknownWindow": _encode_window(policy.unknown_window),
        "businessWindow": _encode_window(policy.business_window),
        "hiddenCallerPolicy": policy.hidden_caller_policy,
        "stirAction": policy.stir_action,
        "repeatCallerEnabled": policy.repeat_caller_enabled,
        "ringRepeat": policy.ring_repeat,
        "silencedNotifPolicy": policy.silenced_notif_policy,
        "answerInteraction": policy.answer_interaction,
        "groupRecentsEnabled": policy.group_recents_enabled,
        "tiers": [{"lookupKey": t.lookup_key, "tier": t.tier,
                   "addedAt": t.added_at} for t in policy.tiers],
        "patterns": [{"e164Prefix": p.e164_prefix, "wildcards": p.wildcards,
                      "action": p.action, "preset": p.preset,
                      "createdAt": p.created_at}
                     for p in policy.patterns],
    }, separators=(",", ":"))


def _encode_window(window: WindowSnapshot) -> dict:
    return {"startMinute": window.start_minute,
            "endMinute": window.end_minute, "daysMask": window.days_mask}


def decode_policy(text: str) -> RingPolicySnapshot | None:
    """Garbage or a future schema yields None; never raises."""
    try:
        env = json.loads(text.strip())
    except ValueError:
        return None
    if not isinstance(env, dict):
        return None
    schema = env.get("schema")
    if schema is not None:
        if isinstance(schema, bool) or not isinstance(schema, int):
            return None
        if schema > POLICY_SCHEMA:
            return None
    tiers = []
    for raw in env.get("tiers") or []:
        if not isinstance(raw, dict):
            continue
        key = raw.get("lookupKey")
        tier = raw.get("tier")
        if not key or not str(key).strip():
            continue
        if not tier or not str(tier).strip() or tier == "star":
            continue
        tiers.append(TierSnapshot(str(key), str(tier),
                                  _as_int(raw.get("addedAt"))))
    patterns = []
    for raw in env.get("patterns") or []:
        if not isinstance(raw, dict):
            continue
        prefix = raw.get("e164Prefix")
        wildcards = raw.get("wildcards")
        action = raw.get("action")
        if not prefix or not str(prefix).strip():
            continue
        if isinstance(wildcards, bool) \
                or not isinstance(wildcards, int) or wildcards < 0:
            continue
        if action not in ("block", "silence"):
            continue
        patterns.append(PatternSnapshot(
            str(prefix), wildcards, str(action), raw.get("preset"),
            _as_int(raw.get("createdAt"))))
    return RingPolicySnapshot(
        enforcement_mode=str(env.get("enforcementMode", "observing")),
        unknown_window=_decode_window(env.get("unknownWindow"),
                                      *_DEFAULT_UNKNOWN),
        business_window=_decode_window(env.get("businessWindow"),
                                       *_DEFAULT_BUSINESS),
        hidden_caller_policy=str(env.get("hiddenCallerPolicy", "UNKNOWN")),
        stir_action=str(env.get("stirAction", "BLOCK")),
        repeat_caller_enabled=bool(env.get("repeatCallerEnabled", True)),
        ring_repeat=str(env.get("ringRepeat", "once")),
        silenced_notif_policy=str(env.get("silencedNotifPolicy",
                                           "immediate")),
        answer_interaction=str(env.get("answerInteraction", "tap")),
        group_recents_enabled=bool(env.get("groupRecentsEnabled", True)),
        tiers=tiers,
        patterns=patterns,
    )


def _decode_window(raw: object, start: int, end: int) -> WindowSnapshot:
    if not isinstance(raw, dict):
        return WindowSnapshot(start, end, _ALL_DAYS)
    mask = raw.get("daysMask")
    if isinstance(mask, bool) or not isinstance(mask, int) \
            or not 1 <= mask <= _ALL_DAYS:
        mask = _ALL_DAYS
    return WindowSnapshot(_as_int(raw.get("startMinute"), start),
                          _as_int(raw.get("endMinute"), end), mask)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def encode_calllog(entries: list[CallLogEntry]) -> str:
    return json.dumps({
        "schema": CALLLOG_SCHEMA,
        "entries": [{"number": e.number, "type": e.call_type, "date": e.date,
                     "duration": e.duration, "cachedName": e.cached_name}
                    for e in entries],
    }, separators=(",", ":"))


def decode_calllog(text: str) -> list[CallLogEntry]:
    """Garbage or a future schema yields []; rows missing a date are
    dropped (match-on-restore needs it); never raises."""
    try:
        env = json.loads(text.strip())
    except ValueError:
        return []
    if not isinstance(env, dict):
        return []
    schema = env.get("schema")
    if isinstance(schema, int) and schema > CALLLOG_SCHEMA:
        return []
    out = []
    for raw in env.get("entries") or []:
        if not isinstance(raw, dict):
            continue
        date = raw.get("date")
        if isinstance(date, bool) or not isinstance(date, int):
            continue
        call_type = raw.get("type", 0)
        duration = raw.get("duration", 0)
        out.append(CallLogEntry(
            number=raw.get("number"),
            call_type=call_type if _is_int(call_type) else 0,
            date=date,
            duration=duration if _is_int(duration) else 0,
            cached_name=raw.get("cachedName"),
        ))
    return out
