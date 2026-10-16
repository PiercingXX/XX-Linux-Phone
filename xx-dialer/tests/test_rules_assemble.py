"""Port of RulesAssembleTest (app/.../data/RulesAssembleTest.kt).

Stored settings + pattern rows assemble into engine Rules (design §6,
§7, §15).

Adaptation: Android's LocalDateTime system-zone mapping is naive
`datetime.fromtimestamp` here; pattern rows are `store.PatternRow`.
"""
from datetime import datetime

from xx_dialer.policy import (
    HiddenCallerPolicy,
    PatternRule,
    StirAction,
    Window,
)
from xx_dialer.store import PatternRow, assemble_rules, window_to_json


def test_empty_settings_yield_the_shipped_design_defaults():
    rules = assemble_rules({}, [])
    assert rules.hidden_caller_policy == HiddenCallerPolicy.UNKNOWN
    assert rules.stir_action == StirAction.BLOCK
    assert rules.repeat_caller_enabled
    assert rules.bypass_until is None
    assert rules.unknown_window == Window(540, 1020, 127)
    assert rules.business_window == Window(540, 1140, 127)
    assert rules.block_patterns == [] and rules.silence_patterns == []


def test_garbage_values_degrade_to_defaults_instead_of_throwing():
    rules = assemble_rules(
        {"hidden_caller_policy": "lobotomize",
         "stir_action": "42",
         "unknown_window": "{oops",
         "business_window": "",
         "repeat_caller_enabled": "certainly",
         "bypass_until": "soon"},
        [],
    )
    assert rules.hidden_caller_policy == HiddenCallerPolicy.UNKNOWN
    assert rules.stir_action == StirAction.BLOCK
    assert rules.repeat_caller_enabled
    assert rules.unknown_window == Window(540, 1020, 127)
    assert rules.business_window == Window(540, 1140, 127)
    assert rules.bypass_until is None


def test_stored_windows_and_enums_are_honored():
    rules = assemble_rules(
        {"unknown_window": '{"startMinute":600,"endMinute":900,'
                            '"daysMask":31}',
         "hidden_caller_policy": "block",
         "stir_action": "off",
         "repeat_caller_enabled": "0"},
        [],
    )
    assert rules.unknown_window == Window(600, 900, 31)
    assert rules.hidden_caller_policy == HiddenCallerPolicy.BLOCK
    assert rules.stir_action == StirAction.OFF
    assert not rules.repeat_caller_enabled


def test_bypass_epoch_maps_to_a_local_datetime():
    epoch = 1700000000000
    rules = assemble_rules({"bypass_until": str(epoch)}, [])
    assert rules.bypass_until == datetime.fromtimestamp(epoch / 1000)


def test_blank_bypass_stays_inactive():
    rules = assemble_rules({"bypass_until": ""}, [])
    assert rules.bypass_until is None


def test_pattern_rows_split_by_action_into_block_and_silence_lists():
    rules = assemble_rules(
        {},
        [PatternRow("1425555", 4, "block", created_at=1),
         PatternRow("1800555", 4, "silence", preset="neighbor_spoof",
                    created_at=2)],
    )
    assert rules.block_patterns == [PatternRule("1425555", 4)]
    assert rules.silence_patterns == [PatternRule("1800555", 4)]


def test_malformed_and_unknown_action_rows_are_skipped_never_thrown():
    rules = assemble_rules(
        {},
        [PatternRow("", 4, "block", created_at=1),
         PatternRow("+1-800-FLOWERS", -1, "silence", created_at=2),
         PatternRow("1425", 0, "weird", created_at=3)],
    )
    assert rules.block_patterns == []
    assert rules.silence_patterns == []


def test_malformed_prefixes_are_skipped_not_rewritten():
    rules = assemble_rules(
        {},
        [PatternRow("+1 425-555", 4, "block", created_at=1),
         PatternRow("+1-800-FLOWERS", 4, "silence", created_at=2),
         PatternRow("  ", 0, "block", created_at=3)],
    )
    assert rules.block_patterns == []
    assert rules.silence_patterns == []


def test_config_windows_round_trip_through_assembly():
    from xx_dialer.store import config_to_settings

    settings = config_to_settings(
        {"unknown_window": {"start": 600, "end": 900, "days": 31},
         "business_window": {"start": 540, "end": 1140, "days": 127}})
    rules = assemble_rules(settings, [])
    assert rules.unknown_window == Window(600, 900, 31)
    assert rules.business_window == Window(540, 1140, 127)
    assert window_to_json(Window(600, 900, 31)) == \
        '{"startMinute":600,"endMinute":900,"daysMask":31}'
