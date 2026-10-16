"""Mask-builder tests — port of ui/MaskBuilderTest.kt."""
from xx_dialer.masks import Draft, build, digits_of, neighbor_draft, preview
from xx_dialer.policy import PatternRule


def test_digits_strip_formatting():
    assert digits_of("+1 (425) 555-0134") == "14255550134"
    assert digits_of("425.555.0134") == "4255550134"
    assert digits_of("no digits here") == ""


def test_build_masks_exactly_the_tail():
    draft = build("14255550134", 5)
    assert draft is not None
    assert (draft.prefix, draft.wildcards) == ("142555", 5)


def test_build_keeps_a_prefix_digit():
    assert build("425", 3) is None
    assert build("425", 4) is None
    assert build("", 1) is None


def test_build_rejects_nondigit_prefixes():
    assert build("+1425555", 2) is None


def test_zero_mask_is_a_legal_exact_rule():
    draft = build("14255550134", 0)
    assert draft is not None
    assert (draft.prefix, draft.wildcards) == ("14255550134", 0)


def test_drafts_satisfy_length_exact_matching():
    rule = PatternRule("142555", 5)
    assert rule.matches("+14255550134")
    assert rule.matches("14255550134")
    assert not rule.matches("+1425555013")
    assert not rule.matches("+142555501349")
    assert not rule.matches("+14255650134")


def test_preview_shapes_nanp_with_country_code():
    assert preview(build("14255550134", 4)) == "+1 425-555-XXXX"


def test_preview_shapes_nanp_without_country_code():
    assert preview(build("4255550134", 4)) == "425-555-XXXX"


def test_preview_falls_back_to_plus_digits():
    assert preview(Draft("44", 7)) == "+44XXXXXXX"
    assert preview(Draft("1", 1)) == "+1X"


def test_neighbor_draft_takes_six_digits():
    draft = neighbor_draft("14255550134")
    assert draft is not None
    assert (draft.prefix, draft.wildcards) == ("142555", 5)
    assert PatternRule("142555", 5).matches("+14255550134")
    assert not PatternRule("142555", 5).matches("+14255650134")


def test_neighbor_draft_handles_short_lines():
    draft = neighbor_draft("442055123456")
    assert draft is not None
    assert (draft.prefix, draft.wildcards) == ("442055", 6)
    assert neighbor_draft("456789") is None
    assert neighbor_draft("") is None
    assert neighbor_draft("+142555") is None
