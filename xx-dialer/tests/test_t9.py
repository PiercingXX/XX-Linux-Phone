"""T9 match-as-you-type tests — port of ui/T9Test.kt."""
from xx_dialer.t9 import (
    InName,
    InNumber,
    char_range,
    digits_of,
    encode,
    hit,
    initials,
    search,
)


def test_letters_map_onto_keypad_digits():
    assert encode("abcdefghijklmnopqrstuvwxyz") == "22233344455566677778889999"


def test_case_irrelevant_digits_pass_through():
    assert encode("GHI-jkl 425-555-0") == "4445554255550"


def test_punctuation_drops_out():
    assert encode("+1 (2)") == "12"


def test_digits_of_keeps_only_digits():
    assert digits_of("+1 (415) 555-0100") == "14155550100"


def test_query_prefix_hits_in_number():
    found = hit("425", "+14255550134", "Dr. Reyes")
    assert isinstance(found, InNumber)
    assert (found.digit_start, found.length) == (1, 3)


def test_query_inside_number_hits_anywhere():
    found = hit("0134", "+14255550134", "Dr. Reyes")
    assert isinstance(found, InNumber)
    assert (found.digit_start, found.length) == (7, 4)


def test_name_letters_match_as_prefix():
    found = hit("37", "2065550177", "Dr. Reyes")
    assert isinstance(found, InName)
    assert found.prefix_len == 2


def test_name_match_requires_prefix_not_infix():
    assert hit("555", "+14150000000", "Margaret Adams") is None
    assert hit("427", "+14150000000", "Margaret Adams") is None


def test_empty_or_digitless_queries_never_hit():
    assert hit("", "+14155550100", "Jal") is None
    assert hit("*#", "+14155550100", "Jal") is None


def test_number_wins_when_both_could_match():
    assert isinstance(hit("426", "+14265550100", "Han Solo"), InNumber)


def test_char_range_maps_back_onto_formatted_numbers():
    raw = "+1 (425) 555-0100"
    assert char_range(raw, InNumber(1, 3)) == (4, 6)
    assert raw[4:7] == "425"


def test_char_range_survives_leading_plus_shape():
    assert char_range("+14155550100", InNumber(0, 4)) == (1, 4)


def test_char_range_null_when_raw_shrunk():
    assert char_range("+1", InNumber(3, 2)) is None


def test_initials_take_two_words():
    assert initials("Margaret Adams") == "MA"


def test_single_word_keeps_first_two_letters():
    assert initials("Roscoe") == "RO"


def test_non_letter_words_skipped():
    assert initials("2nd Margaret Adams") == "MA"


def test_blank_names_degrade_to_hash():
    assert initials(None) == "#"
    assert initials("   ") == "#"


def test_search_lists_number_hits_before_name_hits():
    rows = [("u1", "2065550177", "Han Solo"),
            ("u2", "+14265550100", "Zed")]
    got = search(rows, "426", limit=10)
    assert [r[0] for r in got] == ["u2", "u1"]
    assert isinstance(got[0][3], InNumber)
    assert isinstance(got[1][3], InName)


def test_search_empty_query_and_limit():
    rows = [("u1", "+14155550100", "Ada")]
    assert search(rows, "", limit=10) == []
    assert search(rows, "*#", limit=10) == []
    assert search(rows, "415", limit=0) == []
