"""Port of BackupJsonRoundTripTest + BackupImportGuardTest
(app/.../data/BackupJsonRoundTripTest.kt, BackupImportGuardTest.kt).

Adaptation: Android's family-backup envelope
`{schema,tiers,patterns,settings}` with its import whitelist maps onto
this lane's `ring_policy.json` snapshot plus `validate_snapshot` (reject
before any write, violations naming themselves) and `sanitize_for_import`
/ `apply_policy` (imported enforcement always lands observing — offered,
never flipped). The unknown-settings-key whitelist is satisfied
structurally: the codec ignores unknown JSON keys (like Gson) and the
apply path only ever writes known config keys.
"""
import pytest

from xx_dialer.codecs import (
    PatternSnapshot,
    RingPolicySnapshot,
    TierSnapshot,
    WindowSnapshot,
    decode_policy,
    encode_policy,
)
from xx_dialer.importers import (
    apply_policy,
    default_config,
    export_policy,
    import_policy_text,
    sanitize_for_import,
    validate_snapshot,
)


def sample_snapshot() -> RingPolicySnapshot:
    return RingPolicySnapshot(
        enforcement_mode="enforcing",
        unknown_window=WindowSnapshot(540, 1020, 127),
        business_window=WindowSnapshot(540, 1140, 31),
        tiers=[TierSnapshot("abc123/", "biz", 1700000000000),
               TierSnapshot("def456/", "biz", 1700000001000)],
        patterns=[PatternSnapshot("1425555", 4, "block", None, 1),
                  PatternSnapshot("1800555", 4, "silence",
                                  "neighbor_spoof", 2)],
    )


def test_round_trip_preserves_every_user_owned_fact():
    original = sample_snapshot()
    parsed = decode_policy(encode_policy(original))
    assert parsed is not None
    assert parsed.tiers == original.tiers
    assert [(p.e164_prefix, p.wildcards, p.action)
            for p in parsed.patterns] == \
        [(p.e164_prefix, p.wildcards, p.action)
         for p in original.patterns]
    assert parsed.patterns[1].preset == "neighbor_spoof"


def test_envelope_carries_schema_version_1():
    assert '"schema":1' in encode_policy(sample_snapshot())


def test_wrong_schema_version_is_rejected(store):
    bad = encode_policy(sample_snapshot()).replace('"schema":1',
                                                   '"schema":99')
    assert decode_policy(bad) is None
    assert import_policy_text(bad, default_config(), store) is None


def test_garbage_fails_to_parse_instead_of_throwing(store):
    assert decode_policy("{ not json") is None
    assert import_policy_text("", default_config(), store) is None


def test_null_envelope_parses_to_none():
    assert decode_policy("null") is None


def test_unknown_action_is_rejected_before_any_write(store):
    snapshot = sample_snapshot()
    snapshot.patterns = [PatternSnapshot("1425555", 4, "obliterate",
                                         None, 1)]
    errors = validate_snapshot(snapshot)
    assert errors and any("unknown action" in e for e in errors)
    with pytest.raises(ValueError):
        apply_policy(snapshot, default_config(), store)
    assert store.all_patterns() == []


def test_negative_wildcards_and_blank_prefix_are_rejected():
    bad_wildcards = sample_snapshot()
    bad_wildcards.patterns = [PatternSnapshot("1425555", -1, "block",
                                               None, 1)]
    assert validate_snapshot(bad_wildcards)

    blank_prefix = sample_snapshot()
    blank_prefix.patterns = [PatternSnapshot("", 4, "block", None, 1)]
    assert validate_snapshot(blank_prefix)


def test_star_and_blank_tiers_are_rejected_custom_groups_kept():
    star = sample_snapshot()
    star.tiers = [TierSnapshot("abc123/", "star", 1)]
    assert validate_snapshot(star)

    blank_key = sample_snapshot()
    blank_key.tiers = [TierSnapshot("", "biz", 1)]
    assert validate_snapshot(blank_key)

    family = sample_snapshot()
    family.tiers = [TierSnapshot("abc123/", "family", 1)]
    assert validate_snapshot(family) == []


def test_valid_snapshot_passes_validation():
    assert validate_snapshot(sample_snapshot()) == []


def test_crafted_enforcing_mode_lands_observing(store):
    snapshot = sample_snapshot()
    snapshot.enforcement_mode = "enforcing"
    sanitized = sanitize_for_import(snapshot)
    assert sanitized.enforcement_mode == "observing"
    app_config = default_config()
    apply_policy(snapshot, app_config, store)
    assert app_config["enforcement_mode"] == "observing"


def test_enforcing_survives_export_but_never_import(store):
    app_config = default_config()
    app_config["enforcement_mode"] = "enforcing"
    exported = export_policy(app_config, store)
    assert exported.enforcement_mode == "enforcing"
    report = apply_policy(exported, default_config(), store)
    assert report.config_keys_written > 0


def test_payload_missing_enforcement_key_gains_observing():
    decoded = decode_policy('{"schema":1}')
    assert decoded is not None
    assert decoded.enforcement_mode == "observing"


def test_unknown_json_keys_are_ignored_like_gson():
    decoded = decode_policy('{"schema":1,"sneaky_override":1,'
                            '"tiers":[],"patterns":[]}')
    assert decoded is not None
    assert decoded.tiers == [] and decoded.patterns == []


def test_export_covers_every_applied_policy_key(store):
    app_config = default_config()
    apply_policy(sample_snapshot(), app_config, store)
    exported = export_policy(app_config, store)
    assert exported.enforcement_mode == "observing"
    assert (exported.unknown_window.start_minute,
            exported.unknown_window.end_minute) == (540, 1020)
    assert (exported.business_window.start_minute,
            exported.business_window.end_minute) == (540, 1140)
    assert len(exported.tiers) == 2
    assert len(exported.patterns) == 2
