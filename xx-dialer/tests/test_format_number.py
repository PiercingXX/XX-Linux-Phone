"""Display-only number grouping: NANP parens, international spaces."""
from xx_dialer import recents


def test_nanp_formats_with_parens_in_nanp_region():
    assert recents.format_number("+16145550142", "US") == "(614) 555-0142"
    assert recents.format_number("6145550142", "US") == "(614) 555-0142"
    assert recents.format_number("16145550142", "US") == "(614) 555-0142"
    assert recents.format_number("+16145550142", "CA") == "(614) 555-0142"
    assert recents.format_number(
        "+1 (614) 555-0142", "US") == "(614) 555-0142"


def test_nanp_in_non_nanp_region_has_no_parens():
    shown = recents.format_number("+16145550142", "GB")
    assert shown.startswith("+1 ") and "(" not in shown
    assert "614" in shown and "555" in shown


def test_keypad_partial_groups_then_parens_when_complete():
    assert recents.format_number("61455501", "US") == "614 555 01"
    assert recents.format_number("6145550142", "US") == "(614) 555-0142"


def test_international_groups_with_country_code():
    assert recents.format_number(
        "+442079460958", "US") == "+44 207 946 0958"
    assert recents.format_number(
        "+442079460958", "GB") == "+44 207 946 0958"
    assert recents.format_number(
        "00442079460958", "US") == "+44 207 946 0958"


def test_short_codes_and_garbage_come_back_as_is():
    assert recents.format_number("911", "US") == "911"
    assert recents.format_number("611", "US") == "611"
    assert recents.format_number("abc", "US") == "abc"
    assert recents.format_number("not-a-number", "US") == "not-a-number"
    assert recents.format_number("", "US") == ""
    assert recents.format_number(None, "US") == ""
    assert recents.format_number("*67", "US") == "*67"
