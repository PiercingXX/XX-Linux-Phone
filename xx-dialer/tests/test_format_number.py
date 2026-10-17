"""Display-only number text: the shared xx_phone rule through recents.

Home-country numbers read nationally, foreign ones keep their country
code, and anything that is not a complete valid number is shown as it
came in; keypad entry groups as it is typed.
"""
import xx_phone
from xx_dialer import recents


def show(raw, region="US"):
    return xx_phone.display(raw, xx_phone.display_country(region))


def test_nanp_formats_nationally_in_nanp_region():
    assert show("+16145550142", "US") == "(614) 555-0142"
    assert show("6145550142", "US") == "(614) 555-0142"
    assert show("16145550142", "US") == "(614) 555-0142"
    assert show("+16145550142", "CA") == "(614) 555-0142"
    assert show("+1 (614) 555-0142", "US") == "(614) 555-0142"


def test_nanp_in_non_nanp_region_keeps_country_code():
    shown = show("+16145550142", "GB")
    assert shown.startswith("+1 ") and "(" not in shown
    assert "614" in shown and "555" in shown


def test_keypad_groups_as_you_type_then_national_when_complete():
    assert xx_phone.as_you_type(
        "61455501", xx_phone.display_country("US")) == "(614) 555-01"
    assert xx_phone.as_you_type(
        "6145550142", xx_phone.display_country("US")) == "(614) 555-0142"
    assert xx_phone.as_you_type(
        "*#06#", xx_phone.display_country("US")) == "*#06#"


def test_international_keeps_country_code():
    assert show("+442079460958", "US") == "+44 20 7946 0958"
    assert show("+442079460958", "GB") == "020 7946 0958"
    assert show("00442079460958", "US") == "00442079460958"


def test_short_codes_and_garbage_come_back_as_is():
    assert show("911", "US") == "911"
    assert show("611", "US") == "611"
    assert show("abc", "US") == "abc"
    assert show("not-a-number", "US") == "not-a-number"
    assert show("", "US") == ""
    assert show(None, "US") == ""
    assert show("*67", "US") == "*67"


def test_recents_helpers_use_the_shared_rule():
    assert recents.display_name_of(
        recents.MergedCall(key=1, e164="+16145550142", raw=None,
                           cached_name=None, display_name=None,
                           time_ms=0, missed=False, outgoing=False,
                           duration_s=0), "US") == "(614) 555-0142"
