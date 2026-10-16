"""Full §6 truth-table port of Android RingPolicyTest + boundary cases.

One test per contract line of
``ref/xx-dialer/core/src/test/.../RingPolicyTest.kt`` (row order,
conflicts, window boundaries, repeat/recent-outgoing overrides,
bypass, hidden callers, patterns, CNAP invariance, observe-gate
inertness, shipped defaults), plus the DST wall-clock cases from
``WindowTest.kt`` and the 15-minute / 48-hour window edges
(14:59 in, 15:00/15:01 out; 47:59 in, 48:00/48:01 out).

Adaptations: ``LocalDateTime`` is a naive ``datetime`` here (same
wall-clock semantics — DST cases assert that directly), and the
repeat/recent-outgoing edges pin ``store.in_repeat_window`` /
``store.in_recent_outgoing_window``, the ms-level predicates behind
the ``repeatCaller``/``recentOutgoing`` facts.
"""
from datetime import datetime, timedelta

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
    decide,
)
from xx_dialer.store import (
    in_recent_outgoing_window,
    in_repeat_window,
)

NOON = datetime(2026, 8, 20, 12, 0)
EVENING = datetime(2026, 8, 20, 20, 0)

NEIGHBOR_SPOOF = PatternRule(prefix="1415555", wildcard_count=4)


def facts(number="+14155550100", saved=False, starred=False,
          biz_tier=False, send_to_voicemail=False, user_blocked=False,
          stir_failed=False, repeat_caller=False, recent_outgoing=False,
          cnap_name=None, emergency_window=False, withheld=False,
          group_blocked=False):
    return CallerFacts(
        number=number, saved=saved, starred=starred, biz_tier=biz_tier,
        send_to_voicemail=send_to_voicemail, user_blocked=user_blocked,
        stir_failed=stir_failed, repeat_caller=repeat_caller,
        recent_outgoing=recent_outgoing, cnap_name=cnap_name,
        emergency_window=emergency_window, withheld=withheld,
        group_blocked=group_blocked)


def make_rules(bypass_until=None,
               hidden_caller_policy=HiddenCallerPolicy.UNKNOWN,
               stir_action=StirAction.BLOCK, repeat_caller_enabled=True,
               block_patterns=None, silence_patterns=None):
    return Rules(
        hidden_caller_policy=hidden_caller_policy,
        stir_action=stir_action,
        repeat_caller_enabled=repeat_caller_enabled,
        bypass_until=bypass_until,
        block_patterns=list(block_patterns or []),
        silence_patterns=list(silence_patterns or []))


def test_verdict_tokens_are_stable_wire_names():
    assert Verdict.block().token() == "Block"
    assert Verdict.ring(Tone.DEFAULT).token() == "Ring"
    assert Verdict.silence(Reason.SEND_TO_VOICEMAIL).token() == "Silence"


def test_row1_emergency_beats_everything_including_block():
    verdict = decide(EVENING, facts(user_blocked=True, stir_failed=True,
                                    emergency_window=True), make_rules())
    assert verdict == Verdict.ring(Tone.DEFAULT)


def test_row2_send_to_voicemail_silenced_with_reason():
    assert decide(NOON, facts(send_to_voicemail=True), make_rules()) == \
        Verdict.silence(Reason.SEND_TO_VOICEMAIL)


def test_row2_voicemail_sits_above_block():
    assert decide(NOON, facts(send_to_voicemail=True, user_blocked=True),
                  make_rules()) == \
        Verdict.silence(Reason.SEND_TO_VOICEMAIL)


def test_row2_voicemail_not_pierced_by_repeat():
    assert decide(EVENING, facts(send_to_voicemail=True,
                                 repeat_caller=True),
                  make_rules()) == \
        Verdict.silence(Reason.SEND_TO_VOICEMAIL)


def test_row2_voicemail_not_pierced_by_expecting():
    assert decide(EVENING, facts(send_to_voicemail=True),
                  make_rules(bypass_until=EVENING + timedelta(hours=2))) == \
        Verdict.silence(Reason.SEND_TO_VOICEMAIL)


def test_row3_user_blocked_blocks():
    assert decide(NOON, facts(user_blocked=True),
                  make_rules()) == Verdict.block()


def test_blocked_group_hides_saved_and_starred():
    assert decide(NOON, facts(saved=True, starred=True,
                              group_blocked=True),
                  make_rules()) == Verdict.block()


def test_row4_stir_failed_default_block_action_blocks():
    assert decide(NOON, facts(stir_failed=True),
                  make_rules(stir_action=StirAction.BLOCK)) == \
        Verdict.block()


def test_row4_stir_failed_silence_action_silences():
    assert decide(NOON, facts(stir_failed=True),
                  make_rules(stir_action=StirAction.SILENCE)) == \
        Verdict.silence(Reason.STIR_FAILED)


def test_row4_stir_off_falls_through_to_unknown_window():
    assert decide(NOON, facts(stir_failed=True),
                  make_rules(stir_action=StirAction.OFF)) == \
        Verdict.ring(Tone.UNKNOWN)


def test_row5_starred_rings_default_any_time():
    night = datetime(2026, 8, 20, 3, 0)
    assert decide(night, facts(starred=True),
                  make_rules()) == Verdict.ring(Tone.DEFAULT)
    assert decide(NOON, facts(starred=True),
                  make_rules()) == Verdict.ring(Tone.DEFAULT)


def test_row6_business_inside_window_rings_default():
    moment = datetime(2026, 8, 20, 18, 59)
    assert decide(moment, facts(biz_tier=True, saved=True),
                  make_rules()) == Verdict.ring(Tone.DEFAULT)


def test_row7_business_outside_window_silenced():
    assert decide(EVENING, facts(biz_tier=True, saved=True),
                  make_rules()) == \
        Verdict.silence(Reason.BUSINESS_OUTSIDE_WINDOW)


def test_row8_saved_rings_default_any_time():
    night = datetime(2026, 8, 20, 3, 0)
    assert decide(night, facts(saved=True),
                  make_rules()) == Verdict.ring(Tone.DEFAULT)


def test_row9_recent_outgoing_rings_unknown_any_time():
    assert decide(EVENING, facts(recent_outgoing=True),
                  make_rules()) == Verdict.ring(Tone.UNKNOWN)


def test_row9_recent_outgoing_false_falls_through_to_silence():
    assert decide(EVENING, facts(), make_rules()) == \
        Verdict.silence(Reason.UNKNOWN_OUTSIDE_WINDOW)


def test_row10_silence_pattern_beats_unknown_inside_window():
    assert decide(NOON, facts(),
                  make_rules(
                      silence_patterns=[NEIGHBOR_SPOOF])) == \
        Verdict.silence(Reason.PATTERN_SILENCED)


def test_row11_unknown_inside_window_rings_unknown_tone():
    assert decide(NOON, facts(), make_rules()) == \
        Verdict.ring(Tone.UNKNOWN)


def test_row12_unknown_outside_window_silenced():
    assert decide(EVENING, facts(), make_rules()) == \
        Verdict.silence(Reason.UNKNOWN_OUTSIDE_WINDOW)


def test_conflict_block_beats_starred():
    assert decide(NOON, facts(starred=True, user_blocked=True),
                  make_rules()) == Verdict.block()


def test_conflict_starred_beats_business_outside_window():
    assert decide(EVENING, facts(starred=True, biz_tier=True,
                                saved=True),
                  make_rules()) == Verdict.ring(Tone.DEFAULT)


def test_boundary_unknown_085959_is_outside():
    moment = datetime(2026, 8, 20, 8, 59, 59, 999999)
    assert decide(moment, facts(), make_rules()) == \
        Verdict.silence(Reason.UNKNOWN_OUTSIDE_WINDOW)


def test_boundary_unknown_0900_is_inside():
    assert decide(datetime(2026, 8, 20, 9, 0), facts(),
                  make_rules()) == Verdict.ring(Tone.UNKNOWN)


def test_boundary_unknown_165959_is_inside():
    moment = datetime(2026, 8, 20, 16, 59, 59, 999999)
    assert decide(moment, facts(), make_rules()) == \
        Verdict.ring(Tone.UNKNOWN)


def test_boundary_unknown_1700_exact_is_outside():
    assert decide(datetime(2026, 8, 20, 17, 0), facts(),
                  make_rules()) == \
        Verdict.silence(Reason.UNKNOWN_OUTSIDE_WINDOW)


def test_boundary_business_185959_is_inside():
    moment = datetime(2026, 8, 20, 18, 59, 59, 999999)
    assert decide(moment, facts(biz_tier=True, saved=True),
                  make_rules()) == Verdict.ring(Tone.DEFAULT)


def test_boundary_business_1900_exact_is_outside():
    moment = datetime(2026, 8, 20, 19, 0)
    assert decide(moment, facts(biz_tier=True, saved=True),
                  make_rules()) == \
        Verdict.silence(Reason.BUSINESS_OUTSIDE_WINDOW)


def test_repeat_pierces_unknown_outside_silence():
    assert decide(EVENING, facts(repeat_caller=True),
                  make_rules()) == Verdict.ring(Tone.UNKNOWN)


def test_repeat_disabled_stays_silenced():
    assert decide(EVENING, facts(repeat_caller=True),
                  make_rules(repeat_caller_enabled=False)) == \
        Verdict.silence(Reason.UNKNOWN_OUTSIDE_WINDOW)


def test_repeat_while_blocked_block_still_wins():
    assert decide(EVENING, facts(repeat_caller=True, user_blocked=True),
                  make_rules()) == Verdict.block()


def test_repeat_pierces_business_outside_silence():
    assert decide(EVENING, facts(biz_tier=True, saved=True,
                                repeat_caller=True),
                  make_rules()) == Verdict.ring(Tone.UNKNOWN)


def test_repeat_pierces_pattern_silence():
    assert decide(NOON, facts(repeat_caller=True),
                  make_rules(
                      silence_patterns=[NEIGHBOR_SPOOF])) == \
        Verdict.ring(Tone.UNKNOWN)


def test_repeat_pierces_hidden_policy_silence():
    assert decide(EVENING, facts(number=None, withheld=True,
                                 repeat_caller=True),
                  make_rules(
                      hidden_caller_policy=HiddenCallerPolicy.SILENCE)
                  ) == Verdict.ring(Tone.UNKNOWN)


def test_recent_outgoing_rings_over_matching_silence_pattern():
    assert decide(NOON, facts(recent_outgoing=True),
                  make_rules(
                      silence_patterns=[NEIGHBOR_SPOOF])) == \
        Verdict.ring(Tone.UNKNOWN)


def test_bypass_now_equals_until_is_inclusive():
    assert decide(EVENING, facts(),
                  make_rules(bypass_until=EVENING)) == \
        Verdict.ring(Tone.UNKNOWN)


def test_bypass_active_converts_silence_to_unknown_ring():
    until = EVENING + timedelta(minutes=90)
    assert decide(EVENING, facts(),
                  make_rules(bypass_until=until)) == \
        Verdict.ring(Tone.UNKNOWN)


def test_bypass_expired_stays_silenced():
    after = EVENING + timedelta(microseconds=1)
    assert decide(after, facts(),
                  make_rules(bypass_until=EVENING)) == \
        Verdict.silence(Reason.UNKNOWN_OUTSIDE_WINDOW)


def test_bypass_block_still_blocks_while_active():
    assert decide(NOON, facts(user_blocked=True),
                  make_rules(bypass_until=NOON + timedelta(hours=1))
                  ) == Verdict.block()


def test_bypass_exactly_at_max_duration_still_active():
    until = EVENING + timedelta(hours=8)
    assert decide(EVENING, facts(),
                  make_rules(bypass_until=until)) == \
        Verdict.ring(Tone.UNKNOWN)


def test_bypass_beyond_max_duration_treated_as_expired():
    until = EVENING + timedelta(hours=8, microseconds=1)
    assert decide(EVENING, facts(),
                  make_rules(bypass_until=until)) == \
        Verdict.silence(Reason.UNKNOWN_OUTSIDE_WINDOW)


def test_hidden_policy_unknown_flows_into_rows_11_and_12():
    assert decide(NOON, facts(number=None, withheld=True),
                  make_rules()) == Verdict.ring(Tone.UNKNOWN)
    assert decide(EVENING, facts(number=None, withheld=True),
                  make_rules()) == \
        Verdict.silence(Reason.UNKNOWN_OUTSIDE_WINDOW)


def test_hidden_policy_silence_withheld_silenced():
    assert decide(NOON, facts(number=None, withheld=True),
                  make_rules(
                      hidden_caller_policy=HiddenCallerPolicy.SILENCE)
                  ) == Verdict.silence(Reason.HIDDEN_POLICY)


def test_hidden_policy_block_withheld_blocks():
    assert decide(NOON, facts(number=None, withheld=True),
                  make_rules(
                      hidden_caller_policy=HiddenCallerPolicy.BLOCK)
                  ) == Verdict.block()


def test_unparseable_allowed_does_not_take_hidden_block():
    assert decide(NOON, facts(number=None, withheld=False),
                  make_rules(
                      hidden_caller_policy=HiddenCallerPolicy.BLOCK)
                  ) == Verdict.ring(Tone.UNKNOWN)


def test_unparseable_allowed_outside_window_is_unknown_silence():
    assert decide(EVENING, facts(number=None, withheld=False),
                  make_rules(
                      hidden_caller_policy=HiddenCallerPolicy.BLOCK)
                  ) == Verdict.silence(Reason.UNKNOWN_OUTSIDE_WINDOW)


def test_pattern_block_rule_unsaved_number_blocks():
    assert decide(NOON, facts(), make_rules(
        block_patterns=[PatternRule("1415555", 4)])) == Verdict.block()


def test_pattern_block_rule_saved_number_exempt():
    assert decide(NOON, facts(saved=True),
                  make_rules(
                      block_patterns=[NEIGHBOR_SPOOF])
                  ) == Verdict.ring(Tone.DEFAULT)


def test_pattern_silence_rule_saved_number_exempt():
    assert decide(NOON, facts(saved=True),
                  make_rules(
                      silence_patterns=[NEIGHBOR_SPOOF])
                  ) == Verdict.ring(Tone.DEFAULT)


def test_pattern_withheld_number_cannot_match_any_pattern():
    assert decide(NOON, facts(number=None, withheld=True),
                  make_rules(silence_patterns=[NEIGHBOR_SPOOF],
                             block_patterns=[NEIGHBOR_SPOOF])
                  ) == Verdict.ring(Tone.UNKNOWN)


def test_cnap_name_never_changes_any_verdict():
    cases = [
        (NOON, facts(), make_rules()),
        (EVENING, facts(), make_rules()),
        (NOON, facts(user_blocked=True), make_rules()),
        (EVENING, facts(starred=True), make_rules()),
        (EVENING, facts(biz_tier=True, saved=True), make_rules()),
        (NOON, facts(send_to_voicemail=True), make_rules()),
        (NOON, facts(emergency_window=True), make_rules()),
        (EVENING, facts(number=None, withheld=True),
         make_rules(hidden_caller_policy=HiddenCallerPolicy.SILENCE)),
        (EVENING, facts(repeat_caller=True), make_rules()),
    ]
    for now, case_facts, case_rules in cases:
        bare = decide(now, case_facts, case_rules)
        named = decide(now, facts(
            number=case_facts.number, saved=case_facts.saved,
            starred=case_facts.starred, biz_tier=case_facts.biz_tier,
            send_to_voicemail=case_facts.send_to_voicemail,
            user_blocked=case_facts.user_blocked,
            stir_failed=case_facts.stir_failed,
            repeat_caller=case_facts.repeat_caller,
            recent_outgoing=case_facts.recent_outgoing,
            cnap_name="EVERGREEN DENTAL",
            emergency_window=case_facts.emergency_window,
            withheld=case_facts.withheld,
            group_blocked=case_facts.group_blocked), case_rules)
        assert bare == named


def test_observe_mode_is_inert_identical_verdicts():
    cases = [
        (NOON, facts(), make_rules()),
        (EVENING, facts(), make_rules()),
        (NOON, facts(user_blocked=True), make_rules()),
        (NOON, facts(starred=True), make_rules()),
        (EVENING, facts(biz_tier=True, saved=True), make_rules()),
        (NOON, facts(send_to_voicemail=True), make_rules()),
        (NOON, facts(emergency_window=True), make_rules()),
        (EVENING, facts(number=None, withheld=True),
         make_rules(hidden_caller_policy=HiddenCallerPolicy.SILENCE)),
        (NOON, facts(),
         make_rules(silence_patterns=[NEIGHBOR_SPOOF])),
        (EVENING, facts(repeat_caller=True), make_rules()),
        (NOON, facts(),
         make_rules(bypass_until=NOON + timedelta(hours=2))),
    ]
    first = [decide(now, case_facts, case_rules)
             for now, case_facts, case_rules in cases]
    for enforcing in (False, True):
        for (now, case_facts, case_rules), verdict in zip(cases, first):
            applied, _mode = apply_mode(
                decide(now, case_facts, case_rules), enforcing)
            if enforcing:
                assert applied == verdict
            else:
                assert applied.kind == "ring"


def test_rules_defaults_pin_design_values():
    defaults = Rules()
    assert defaults.unknown_window == Window(540, 1020, ALL_DAYS)
    assert defaults.business_window == Window(540, 1140, ALL_DAYS)
    assert defaults.hidden_caller_policy == HiddenCallerPolicy.UNKNOWN
    assert defaults.stir_action == StirAction.BLOCK
    assert defaults.repeat_caller_enabled
    assert defaults.bypass_until is None
    assert defaults.block_patterns == []
    assert defaults.silence_patterns == []


def test_dst_spring_forward_wall_clock_has_no_gap():
    early = Window(60, 240, ALL_DAYS)
    assert early.contains(datetime(2026, 3, 8, 1, 59))
    assert early.contains(datetime(2026, 3, 8, 3, 0))
    assert not early.contains(datetime(2026, 3, 8, 4, 0))


def test_dst_fall_back_repeated_hour_is_just_wall_time():
    early = Window(60, 240, ALL_DAYS)
    assert early.contains(datetime(2026, 11, 1, 1, 30))
    assert not early.contains(datetime(2026, 11, 1, 0, 30))
    assert not early.contains(datetime(2026, 11, 1, 4, 0))


def test_repeat_window_1459_in_1500_and_1501_out():
    now = 1_000_000_000
    assert in_repeat_window(now, now - (14 * 60 + 59) * 1000)
    assert not in_repeat_window(now, now - 15 * 60 * 1000)
    assert not in_repeat_window(now, now - (15 * 60 + 1) * 1000)


def test_recent_outgoing_window_4759_in_4800_and_4801_out():
    now = 1_000_000_000
    assert in_recent_outgoing_window(now, now - (47 * 3600 + 59 * 60)
                                     * 1000)
    assert not in_recent_outgoing_window(now, now - 48 * 3600 * 1000)
    assert not in_recent_outgoing_window(now, now - (48 * 3600 + 60)
                                         * 1000)
