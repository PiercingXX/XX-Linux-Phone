"""Policy engine tests: the §6 precedence table in miniature.

A fuller truth-table port (boundaries, DST, 14:59/15:01, 47:59/48:01)
lands in lane 5; these pin the precedence skeleton the later port must
preserve.
"""
from datetime import datetime

from xx_dialer.policy import (
    ALL_DAYS,
    CallerFacts,
    HiddenCallerPolicy,
    PatternRule,
    Reason,
    Rules,
    StirAction,
    Tone,
    Verdict,
    Window,
    apply_mode,
    canonical_number,
    decide,
    normalize_number,
)


def noon() -> datetime:
    return datetime(2026, 10, 15, 12, 0)


def night() -> datetime:
    return datetime(2026, 10, 15, 22, 0)


def test_starred_beats_everything_below():
    facts = CallerFacts(number="+14155550100", starred=True, biz_tier=True,
                        saved=True)
    assert decide(night(), facts, Rules()) == Verdict.ring(Tone.DEFAULT)


def test_blocked_beats_starred():
    facts = CallerFacts(number="+14155550100", starred=True, user_blocked=True)
    assert decide(noon(), facts, Rules()).kind == "block"


def test_business_in_window_rings_outside_silences():
    facts = CallerFacts(number="+14155550100", biz_tier=True)
    assert decide(noon(), facts, Rules()).kind == "ring"
    verdict = decide(night(), facts, Rules())
    assert verdict == Verdict.silence(Reason.BUSINESS_OUTSIDE_WINDOW)


def test_unknown_window_boundary():
    facts = CallerFacts(number="+14155550100")
    assert decide(datetime(2026, 10, 15, 8, 59), facts, Rules()).kind == "silence"
    assert decide(datetime(2026, 10, 15, 9, 0), facts, Rules()) == \
        Verdict.ring(Tone.UNKNOWN)
    assert decide(datetime(2026, 10, 15, 16, 59), facts, Rules()).kind == "ring"
    assert decide(datetime(2026, 10, 15, 17, 0), facts, Rules()).kind == "silence"


def test_recent_outgoing_rings_any_time():
    facts = CallerFacts(number="+14155550100", recent_outgoing=True)
    assert decide(night(), facts, Rules()) == Verdict.ring(Tone.UNKNOWN)


def test_pattern_block_with_contacts_exemption():
    rules = Rules(block_patterns=[PatternRule("1425555", 4)])
    blocked = CallerFacts(number="+14255550100")
    assert decide(noon(), blocked, rules).kind == "block"
    saved = CallerFacts(number="+14255550100", saved=True)
    assert decide(night(), saved, rules).kind == "ring"


def test_silence_pattern_never_beats_recent_outgoing():
    rules = Rules(silence_patterns=[PatternRule("1425555", 4)])
    facts = CallerFacts(number="+14255550100", recent_outgoing=True)
    assert decide(night(), facts, rules).kind == "ring"


def test_repeat_pierces_silence_not_block():
    silenced = CallerFacts(number="+14155550100", repeat_caller=True)
    assert decide(night(), silenced, Rules()) == Verdict.ring(Tone.UNKNOWN)
    blocked = CallerFacts(number="+14155550100", repeat_caller=True,
                          user_blocked=True)
    assert decide(night(), blocked, Rules()).kind == "block"


def test_hidden_policy():
    facts = CallerFacts(number=None, withheld=True)
    assert decide(night(), facts, Rules()).kind == "silence"
    rules = Rules(hidden_caller_policy=HiddenCallerPolicy.BLOCK)
    assert decide(noon(), facts, rules).kind == "block"


def test_emergency_window_rings():
    facts = CallerFacts(number="+14155550100", emergency_window=True)
    assert decide(night(), facts, Rules()) == Verdict.ring(Tone.DEFAULT)


def test_observe_gate_rings_default():
    verdict = decide(night(), CallerFacts(number="+14155550100"), Rules())
    applied, mode = apply_mode(verdict, enforcing=False)
    assert mode == "observed"
    assert applied == Verdict.ring(Tone.DEFAULT)
    applied, mode = apply_mode(verdict, enforcing=True)
    assert mode == "enforced" and applied == verdict


def test_verdict_tokens_stable():
    assert Verdict.block().token() == "Block"
    assert Verdict.ring(Tone.DEFAULT).token() == "Ring"
    assert Verdict.silence(Reason.HIDDEN_POLICY).token() == "Silence"


def test_window_wrap_and_days():
    late = Window(22 * 60, 2 * 60, ALL_DAYS)
    assert late.contains(datetime(2026, 10, 15, 23, 0))
    assert late.contains(datetime(2026, 10, 16, 1, 0))
    assert not late.contains(datetime(2026, 10, 16, 3, 0))
    monday_only = Window(0, 24 * 60 - 1, 0b0000001)
    assert monday_only.contains(datetime(2026, 10, 12, 12, 0))
    assert not monday_only.contains(datetime(2026, 10, 13, 12, 0))


def test_normalize_groups_caller_forms():
    assert normalize_number("+1 (415) 555-0100") == "+14155550100"
    assert normalize_number("0041415550100") == "+41415550100"
    assert normalize_number("+14155550100") == "+14155550100"
    assert normalize_number("") is None
    assert normalize_number("12") is None
    assert normalize_number(None) is None


def test_canonical_number_is_region_aware():
    assert canonical_number("(555) 123-4567") == "+15551234567"
    assert canonical_number("5551234567") == "+15551234567"
    assert canonical_number("1-555-123-4567") == "+15551234567"
    assert canonical_number("+15551234567") == "+15551234567"
    assert canonical_number("004415550100") == "+4415550100"
    assert canonical_number("(555) 123-4567", "DE") == "5551234567"
    assert canonical_number("911") == "911"
    assert canonical_number("") is None
    assert canonical_number("12") is None
    assert canonical_number(None) is None


def test_stir_action_off_never_blocks():
    rules = Rules(stir_action=StirAction.OFF)
    facts = CallerFacts(number="+14155550100", stir_failed=True)
    assert decide(noon(), facts, rules).kind == "ring"
