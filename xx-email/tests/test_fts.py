"""Port of FtsEscaperTest: raw input never reaches MATCH."""

from xx_email.db import escape_fts_prefix_query as escape


def test_plain_terms_become_quoted_prefix_phrases():
    assert escape("hello world") == '"hello"* "world"*'


def test_boolean_operators_neutralized_as_literals():
    assert escape("AND") == '"AND"*'
    assert escape("OR NOT") == '"OR"* "NOT"*'


def test_fts_syntax_characters_defused():
    assert escape("from:alice@example.com") == '"from:alice@example.com"*'
    assert escape('a"b') == '"ab"*'
    assert escape("(paren)") == '"(paren)"*'
    assert escape("a-b c*d") == '"a-b"* "c*d"*'


def test_whitespace_runs_collapse_and_empties_vanish():
    assert escape("  a   b  ") == '"a"* "b"*'
    assert escape("   ") == ""
    assert escape("") == ""
    assert escape('" " ab') == '"ab"*'
