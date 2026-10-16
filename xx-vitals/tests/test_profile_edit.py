"""Port of ProfileEditModelTest: goals, height, birth year validation."""

from xx_vitals.core import profile_edit


def test_goals_accept_positive_whole_numbers():
    assert profile_edit.parse_steps_goal("12000") == 12000
    assert profile_edit.parse_effort_goal("1") == 1
    assert profile_edit.parse_weekly_effort_goal("150") == 150


def test_goals_reject_garbage_and_outrageous_values():
    for raw in ("", "  ", "abc", "12.5", "-100", "0", "99999999999999"):
        assert profile_edit.parse_steps_goal(raw) is None, raw
    assert profile_edit.parse_steps_goal(str(profile_edit.MAX_STEPS_GOAL + 1)) is None
    assert profile_edit.parse_effort_goal("0") is None
    assert profile_edit.parse_weekly_effort_goal("-3") is None


def test_goals_tolerate_surrounding_whitespace():
    assert profile_edit.parse_steps_goal(" 9000 ") == 9000


def test_height_metric_parses_metres():
    assert profile_edit.parse_height_metres("1.78", "metric") == 1.78
    assert profile_edit.parse_height_metres("1.50", "metric") == 1.5


def test_height_imperial_feet_inches_parses_to_metres():
    assert abs(profile_edit.parse_height_metres("5'11", "imperial") - 1.8034) < 1e-3
    assert abs(profile_edit.parse_height_metres("5'11\"", "imperial") - 1.8034) < 1e-3
    assert abs(profile_edit.parse_height_metres("5 ft 6 in", "imperial") - 1.6764) < 1e-3
    assert abs(profile_edit.parse_height_metres("6'0", "imperial") - 1.8288) < 1e-3


def test_height_imperial_plain_number_reads_as_total_inches():
    assert abs(profile_edit.parse_height_metres("70", "imperial") - 1.778) < 1e-3


def test_height_outside_plausible_range_rejected():
    assert profile_edit.parse_height_metres("0.3", "metric") is None
    assert profile_edit.parse_height_metres("3.0", "metric") is None
    assert profile_edit.parse_height_metres("-1.78", "metric") is None
    assert profile_edit.parse_height_metres("5", "imperial") is None
    assert profile_edit.parse_height_metres("", "imperial") is None


def test_height_input_text_prefills_display_unit():
    assert profile_edit.height_input_text(1.78, "metric") == "1.78"
    assert profile_edit.height_input_text(1.8034, "imperial") == "5'11"
    assert profile_edit.height_input_text(0.0, "metric") == ""
    assert profile_edit.height_input_text(0.0, "imperial") == ""


def test_height_round_trips_through_input_text():
    metres = 1.75
    text = profile_edit.height_input_text(metres, "imperial")
    assert abs(profile_edit.parse_height_metres(text, "imperial") - metres) < 0.01


def test_birth_year_accepts_four_digit_plausible_year():
    assert profile_edit.parse_birth_year("1990", 2026) == 1990
    assert profile_edit.parse_birth_year("2026", 2026) == 2026


def test_birth_year_rejects_wrong_length_and_implausible():
    for raw in ("", "19x0", "189", "18990", "1899", "2050"):
        assert profile_edit.parse_birth_year(raw, 2026) is None, raw
