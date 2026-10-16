"""Port of RingPolicyJsonTest (app/.../backup/RingPolicyJsonTest.kt).

Pure codec: the ring-policy JSON round-trips (BK-1). Star tiers never
survive a decode — stars are owned by the contacts backup, never this
app's export.
"""
from xx_dialer.codecs import (
    PatternSnapshot,
    RingPolicySnapshot,
    TierSnapshot,
    WindowSnapshot,
    decode_policy,
    encode_policy,
)


def sample_policy() -> RingPolicySnapshot:
    return RingPolicySnapshot(
        enforcement_mode="enforcing",
        unknown_window=WindowSnapshot(540, 1020, 0b1111111),
        business_window=WindowSnapshot(540, 1140, 0b0011111),
        hidden_caller_policy="SILENCE",
        stir_action="BLOCK",
        repeat_caller_enabled=True,
        ring_repeat="twice",
        silenced_notif_policy="daily",
        answer_interaction="swipe",
        group_recents_enabled=False,
        tiers=[TierSnapshot("lookup-1", "biz", 1000),
               TierSnapshot("lookup-2", "family", 2000)],
        patterns=[PatternSnapshot("1425555", 4, "block", None, 3000),
                  PatternSnapshot("1800", 7, "silence", "telemarketer",
                                  4000)],
    )


def test_round_trips_a_full_policy():
    original = sample_policy()
    decoded = decode_policy(encode_policy(original))
    assert decoded == original


def test_encoded_json_carries_the_schema():
    assert '"schema":1' in encode_policy(sample_policy())


def test_decode_of_garbage_is_none_never_throws():
    assert decode_policy("not json at all") is None
    assert decode_policy("") is None


def test_decode_of_a_newer_schema_is_none():
    assert decode_policy('{"schema":99}') is None


def test_decode_of_a_minimal_envelope_fills_design_defaults():
    assert decode_policy('{"schema":1}') == RingPolicySnapshot(
        enforcement_mode="observing",
        unknown_window=WindowSnapshot(540, 1020, 0b1111111),
        business_window=WindowSnapshot(540, 1140, 0b1111111),
        hidden_caller_policy="UNKNOWN",
        stir_action="BLOCK",
        repeat_caller_enabled=True,
        ring_repeat="once",
        silenced_notif_policy="immediate",
        answer_interaction="tap",
        group_recents_enabled=True,
        tiers=[],
        patterns=[],
    )


def test_a_star_tier_is_dropped_on_decode():
    decoded = decode_policy(
        '{"schema":1,"tiers":[{"lookupKey":"k","tier":"star",'
        '"addedAt":1}]}')
    assert decoded is not None
    assert decoded.tiers == []


def test_a_pattern_with_negative_wildcards_is_dropped_on_decode():
    decoded = decode_policy(
        '{"schema":1,"patterns":[{"e164Prefix":"1425","wildcards":-1,'
        '"action":"block","createdAt":1}]}')
    assert decoded is not None
    assert decoded.patterns == []
