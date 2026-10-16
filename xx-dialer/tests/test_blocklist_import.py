"""Port of BlocklistImportTest (app/.../util/BlocklistImportTest.kt).

Offline blocklist import: messy real-world files parsed with no network.
One number per line; `#` comments and blanks are structure; garbage is
counted, never fatal.

Adaptation: Android normalizes through libphonenumber with a US default
region. This port uses the offline region-aware normalizer in
`importers` (international `+`/`00` forms anywhere; bare national numbers
via the NANP +1 trunk rule), which agrees with libphonenumber on every
case below.
"""
from xx_dialer.importers import parse_blocklist


def test_mixed_file_yields_survivors_and_counts_garbage():
    text = ("# exported blocklist\n+1 415 555 0100\n\n4155550101\n"
            "not-a-phone\n12345\n# mid-file comment\n0044 20 7946 0958\n")
    parsed = parse_blocklist(text)
    assert parsed.numbers == ["+14155550100", "+14155550101",
                              "+442079460958"]
    assert parsed.skipped == 2


def test_blank_lines_and_comments_are_structure_not_garbage():
    parsed = parse_blocklist("\n\n# header\n   \n# footer\n")
    assert parsed.numbers == []
    assert parsed.skipped == 0


def test_empty_file_imports_nothing_skips_nothing():
    parsed = parse_blocklist("")
    assert len(parsed.numbers) + parsed.skipped == 0


def test_whitespace_around_a_line_does_not_disqualify_it():
    parsed = parse_blocklist("  +14155550100  ")
    assert parsed.numbers == ["+14155550100"]
    assert parsed.skipped == 0


def test_duplicates_are_kept_for_the_insert_layer_to_reconcile():
    parsed = parse_blocklist("+14155550100\n+1 (415) 555-0100")
    assert parsed.numbers == ["+14155550100", "+14155550100"]
