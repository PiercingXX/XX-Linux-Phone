"""Ring-policy engine: pure Python, stdlib only.

A faithful port of the Android core
(`core/RingPolicy.kt`, `Rules.kt`, `Window.kt`, `Verdict.kt`,
`RingRepeat.kt`, `util/E164.kt`): first match wins, in exactly the printed
order, then the overrides. `decide()` is deterministic — the same
(now, facts, rules) always yields the same verdict.

Observe mode is a gate, not a branch: enforcement lives in the callers,
which consult `apply_mode()` after `decide()` has produced its verdict.
It never influences a verdict.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Tone(Enum):
    DEFAULT = "default"
    UNKNOWN = "unknown"


class Reason(Enum):
    EMERGENCY_CALLBACK = "emergency callback"
    SEND_TO_VOICEMAIL = "send to voicemail"
    USER_BLOCKED = "blocked"
    PATTERN_BLOCKED = "blocked by pattern"
    STIR_FAILED = "STIR failed"
    STARRED = "starred"
    BUSINESS_IN_WINDOW = "business"
    BUSINESS_OUTSIDE_WINDOW = "Business, outside 09–19"
    SAVED = "saved contact"
    RECENT_OUTGOING = "you called them Tue"
    PATTERN_SILENCED = "silenced by pattern"
    UNKNOWN_IN_WINDOW = "unknown caller"
    UNKNOWN_OUTSIDE_WINDOW = "Unknown, outside 09–17"
    HIDDEN_POLICY = "hidden caller"
    REPEAT_CALLER = "repeat caller"
    EXPECTING_A_CALL = "rang · expecting a call"


TOKEN_BLOCK = "Block"
TOKEN_RING = "Ring"
TOKEN_SILENCE = "Silence"


@dataclass(frozen=True)
class Verdict:
    kind: str
    tone: Tone | None = None
    reason: Reason | None = None

    @staticmethod
    def block() -> Verdict:
        return Verdict(kind="block")

    @staticmethod
    def ring(tone: Tone) -> Verdict:
        return Verdict(kind="ring", tone=tone)

    @staticmethod
    def silence(reason: Reason) -> Verdict:
        return Verdict(kind="silence", reason=reason)

    def token(self) -> str:
        if self.kind == "block":
            return TOKEN_BLOCK
        if self.kind == "ring":
            return TOKEN_RING
        return TOKEN_SILENCE


class HiddenCallerPolicy(Enum):
    UNKNOWN = "unknown"
    SILENCE = "silence"
    BLOCK = "block"


class StirAction(Enum):
    BLOCK = "block"
    SILENCE = "silence"
    OFF = "off"


@dataclass(frozen=True)
class Window:
    start_minute: int
    end_minute: int
    days_mask: int

    def __post_init__(self) -> None:
        if not 0 <= self.start_minute < 24 * 60:
            raise ValueError(f"start out of range: {self.start_minute}")
        if not 0 <= self.end_minute < 24 * 60:
            raise ValueError(f"end out of range: {self.end_minute}")
        if not 1 <= self.days_mask <= 0b1111111:
            raise ValueError(f"days_mask must enable a day: {self.days_mask}")

    def contains(self, now: datetime) -> bool:
        if self.days_mask & (1 << now.weekday()) == 0:
            return False
        minute = now.hour * 60 + now.minute
        if self.end_minute <= self.start_minute:
            return minute >= self.start_minute or minute < self.end_minute
        return self.start_minute <= minute < self.end_minute


ALL_DAYS = 0b1111111


@dataclass(frozen=True)
class PatternRule:
    prefix: str
    wildcard_count: int

    def __post_init__(self) -> None:
        if not self.prefix:
            raise ValueError("prefix must not be empty")
        if not self.prefix.isdigit():
            raise ValueError(f"prefix must be normalized digits: {self.prefix}")
        if self.wildcard_count < 0:
            raise ValueError("wildcard_count must be >= 0")

    def matches(self, e164_number: str) -> bool:
        digits = e164_number.removeprefix("+")
        return len(digits) == len(self.prefix) + self.wildcard_count \
            and digits.startswith(self.prefix)


@dataclass(frozen=True)
class CallerFacts:
    number: str | None = None
    saved: bool = False
    starred: bool = False
    biz_tier: bool = False
    send_to_voicemail: bool = False
    user_blocked: bool = False
    stir_failed: bool = False
    repeat_caller: bool = False
    recent_outgoing: bool = False
    cnap_name: str | None = None
    emergency_window: bool = False
    withheld: bool = False
    group_blocked: bool = False


@dataclass
class Rules:
    unknown_window: Window = field(
        default_factory=lambda: Window(9 * 60, 17 * 60, ALL_DAYS))
    business_window: Window = field(
        default_factory=lambda: Window(9 * 60, 19 * 60, ALL_DAYS))
    hidden_caller_policy: HiddenCallerPolicy = HiddenCallerPolicy.UNKNOWN
    stir_action: StirAction = StirAction.BLOCK
    repeat_caller_enabled: bool = True
    bypass_until: datetime | None = None
    block_patterns: list[PatternRule] = field(default_factory=list)
    silence_patterns: list[PatternRule] = field(default_factory=list)


MAX_BYPASS_HOURS = 8


def decide(now: datetime, facts: CallerFacts, rules: Rules) -> Verdict:
    return _apply_overrides(_base_verdict(now, facts, rules), facts, now, rules)


def _base_verdict(now: datetime, facts: CallerFacts, rules: Rules) -> Verdict:
    if facts.emergency_window:
        return Verdict.ring(Tone.DEFAULT)
    if facts.group_blocked:
        return Verdict.block()
    if facts.send_to_voicemail:
        return Verdict.silence(Reason.SEND_TO_VOICEMAIL)
    if facts.user_blocked:
        return Verdict.block()
    if _matches_block_pattern(facts, rules):
        return Verdict.block()
    if facts.stir_failed and rules.stir_action == StirAction.BLOCK:
        return Verdict.block()
    if facts.stir_failed and rules.stir_action == StirAction.SILENCE:
        return Verdict.silence(Reason.STIR_FAILED)
    if facts.starred:
        return Verdict.ring(Tone.DEFAULT)
    if facts.biz_tier and rules.business_window.contains(now):
        return Verdict.ring(Tone.DEFAULT)
    if facts.biz_tier:
        return Verdict.silence(Reason.BUSINESS_OUTSIDE_WINDOW)
    if facts.saved:
        return Verdict.ring(Tone.DEFAULT)
    if facts.recent_outgoing:
        return Verdict.ring(Tone.UNKNOWN)
    return _unknown_verdict(now, facts, rules)


def _unknown_verdict(now: datetime, facts: CallerFacts, rules: Rules) -> Verdict:
    if facts.withheld:
        if rules.hidden_caller_policy == HiddenCallerPolicy.BLOCK:
            return Verdict.block()
        if rules.hidden_caller_policy == HiddenCallerPolicy.SILENCE:
            return Verdict.silence(Reason.HIDDEN_POLICY)
        return _window_verdict(now, rules)
    number = facts.number
    if number is not None and any(p.matches(number) for p in rules.silence_patterns):
        return Verdict.silence(Reason.PATTERN_SILENCED)
    return _window_verdict(now, rules)


def _window_verdict(now: datetime, rules: Rules) -> Verdict:
    if rules.unknown_window.contains(now):
        return Verdict.ring(Tone.UNKNOWN)
    return Verdict.silence(Reason.UNKNOWN_OUTSIDE_WINDOW)


def _matches_block_pattern(facts: CallerFacts, rules: Rules) -> bool:
    return facts.number is not None and not facts.saved \
        and any(p.matches(facts.number) for p in rules.block_patterns)


def _apply_overrides(verdict: Verdict, facts: CallerFacts,
                     now: datetime, rules: Rules) -> Verdict:
    if verdict.kind == "silence" and verdict.reason == Reason.SEND_TO_VOICEMAIL:
        return verdict
    result = verdict
    if result.kind == "silence" and facts.repeat_caller and rules.repeat_caller_enabled:
        result = Verdict.ring(Tone.UNKNOWN)
    if result.kind == "silence" and _bypass_active(now, rules.bypass_until):
        result = Verdict.ring(Tone.UNKNOWN)
    return result


def _bypass_active(now: datetime, until: datetime | None) -> bool:
    if until is None or now > until:
        return False
    if until > _plus_hours(now, MAX_BYPASS_HOURS):
        return False
    return True


def _plus_hours(now: datetime, hours: int) -> datetime:
    from datetime import timedelta
    return now + timedelta(hours=hours)


def apply_mode(verdict: Verdict, enforcing: bool) -> tuple[Verdict, str]:
    """Enforcement gate: observe mode rings everything on the default tone.

    Returns (verdict_to_apply, mode) where mode is "enforced"|"observed".
    The computed verdict is what gets logged either way.
    """
    if enforcing:
        return verdict, "enforced"
    if verdict.kind == "ring":
        return verdict, "observed"
    return Verdict.ring(Tone.DEFAULT), "observed"


_NON_DIGIT = re.compile(r"[^\d]")


def normalize_number(raw: str | None) -> str | None:
    """Offline E.164-ish normalization: one caller, one identity.

    Keeps a leading `+`, lifts a leading `00` international prefix to `+`,
    strips formatting. Garbage or short codes normalize to None, which
    classifies the caller as unknown — noisy, never lossy. National
    numbers without a country code are kept as-is (consistent identity
    for block/pattern matching); the lane-2 contacts layer refines the
    default region from the modem country.
    """
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("00"):
        text = "+" + text[2:]
    plus = text.startswith("+")
    digits = _NON_DIGIT.sub("", text)
    if not digits or len(digits) > 15:
        return None
    if plus:
        return "+" + digits if len(digits) >= 7 else None
    if len(digits) < 3:
        return None
    return digits
