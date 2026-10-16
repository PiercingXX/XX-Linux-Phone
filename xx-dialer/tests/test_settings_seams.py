"""Port of SettingsParsingTest (app/.../data/SettingsParsingTest.kt).

Pure checks for the settings parsing seams (design §7, §12, §15).

Adaptation: Android's `Mode`/`HiddenCallerPolicy`/`StirAction` enums are
plain lowercase strings here (`safe_enum` returns the matched value), and
`designDefaults`/`observeWeekEndSeed` live on the config defaults plus
`observe_week_end_seed` in `store`.
"""
from xx_dialer import config as config_mod
from xx_dialer.policy import Window
from xx_dialer.store import (
    observe_week_end_seed,
    parse_window,
    safe_enum,
    should_offer_enforcement,
    window_to_json,
)


def test_default_unknown_window_round_trips():
    window = Window(9 * 60, 17 * 60, 127)
    assert parse_window(window_to_json(window)) == window


def test_wrapping_window_round_trips():
    window = Window(21 * 60, 5 * 60, 0b0000011)
    assert parse_window(window_to_json(window)) == window


def test_stored_json_uses_the_canonical_field_names():
    assert window_to_json(Window(540, 1020, 127)) == \
        '{"startMinute":540,"endMinute":1020,"daysMask":127}'


def test_garbage_json_parses_to_none_not_throw():
    assert parse_window("not json") is None
    assert parse_window('{"startMinute":"x"}') is None
    assert parse_window(None) is None
    assert parse_window("") is None
    assert parse_window('{"startMinute":99999,"endMinute":1020,'
                        '"daysMask":127}') is None
    assert parse_window('{"startMinute":540,"endMinute":1020,'
                        '"daysMask":0}') is None


def test_shipped_windows_deserialize():
    assert parse_window(window_to_json(Window(540, 1020, 127))) == \
        Window(540, 1020, 127)
    assert parse_window(window_to_json(Window(540, 1140, 127))) == \
        Window(540, 1140, 127)


def test_mode_parses_case_insensitively_with_whitespace():
    assert safe_enum("enforcing", {"enforcing", "observing"},
                     "observing") == "enforcing"
    assert safe_enum("OBSERVING", {"enforcing", "observing"},
                     "observing") == "observing"
    assert safe_enum(None, {"enforcing", "observing"},
                     "observing") == "observing"
    assert safe_enum("junk", {"enforcing", "observing"},
                     "observing") == "observing"
    assert safe_enum(" enforcing ", {"enforcing", "observing"},
                     "observing") == "enforcing"


def test_hidden_caller_policy_falls_back_to_unknown_on_garbage():
    allowed = {"unknown", "silence", "block"}
    assert safe_enum("silence", allowed, "unknown") == "silence"
    assert safe_enum("BLOCK", allowed, "unknown") == "block"
    assert safe_enum("whisper", allowed, "unknown") == "unknown"
    assert safe_enum(None, allowed, "unknown") == "unknown"


def test_stir_action_falls_back_to_block_on_garbage():
    assert safe_enum("off", {"block", "silence", "off"}, "block") == "off"
    assert safe_enum("nonsense", {"block", "silence", "off"},
                     "block") == "block"


def test_enforcement_offered_exactly_at_the_week_end():
    week_end = 1000000
    assert should_offer_enforcement("observing", week_end, week_end)


def test_enforcement_not_offered_before_the_week_end():
    week_end = 1000000
    assert not should_offer_enforcement("observing", week_end - 1, week_end)


def test_enforcement_offered_after_the_week_end_forever():
    week_end = 1000000
    assert should_offer_enforcement("observing", week_end + 1, week_end)
    assert should_offer_enforcement("observing",
                                    week_end + 30 * 24 * 60 * 60 * 1000,
                                    week_end)


def test_never_offered_while_enforcing_or_when_unseeded():
    week_end = 1000000
    assert not should_offer_enforcement("enforcing", week_end + 1, week_end)
    assert not should_offer_enforcement("observing", week_end, None)
    assert not should_offer_enforcement("observing", week_end, 0)
    assert not should_offer_enforcement("observing", week_end, 0.0)


def test_defaults_seed_observing_without_starting_the_observe_week(tmp_path):
    defaults = config_mod.load(home=tmp_path)
    assert defaults["enforcement_mode"] == "observing"
    assert defaults["observe_week_end"] == 0.0
    assert defaults["bypass_duration_minutes"] == 120
    assert defaults["hidden_caller_policy"] == "unknown"
    assert defaults["stir_action"] == "block"
    assert defaults["silenced_notif_policy"] == "immediate"
    assert defaults["ring_repeat"] == "once"
    assert defaults["bypass_until"] is None


def test_setup_completion_seed_value_is_now_plus_seven_days():
    now = 777777777777
    assert observe_week_end_seed(now) == str(now + 7 * 24 * 60 * 60 * 1000)
