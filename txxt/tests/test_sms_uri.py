"""RFC 5724 ``sms:``-family URI parsing (headless, pure presenter)."""

from pathlib import Path

from txxt.list_presenter import (
    SMS_URI_SCHEMES,
    parse_sms_target_argv,
    parse_sms_uri,
)


def test_single_recipient_without_body():
    target = parse_sms_uri("sms:+16145550100")
    assert target is not None
    assert target.recipients == ("+16145550100",)
    assert target.body == ""


def test_body_decodes_plus_and_percent_escapes():
    target = parse_sms_uri("sms:+16145550100?body=hello+there%21")
    assert target is not None
    assert target.body == "hello there!"


def test_multiple_recipients_share_one_body():
    target = parse_sms_uri("sms:+16145550100,+16145550200?body=hi")
    assert target is not None
    assert target.recipients == ("+16145550100", "+16145550200")
    assert target.body == "hi"


def test_all_four_schemes_parse_identically():
    for scheme in ("sms", "smsto", "mms", "mmsto"):
        target = parse_sms_uri(f"{scheme}:+16145550100?body=x")
        assert target is not None, scheme
        assert target.recipients == ("+16145550100",)
        assert target.body == "x"
    assert SMS_URI_SCHEMES == {"sms", "smsto", "mms", "mmsto"}


def test_scheme_is_case_insensitive():
    target = parse_sms_uri("SMS:+16145550100")
    assert target is not None
    assert target.recipients == ("+16145550100",)


def test_plus_stays_literal_in_recipients():
    target = parse_sms_uri("sms:%2B16145550100")
    assert target is not None
    assert target.recipients == ("+16145550100",)


def test_first_body_wins_and_others_ignored():
    target = parse_sms_uri("sms:+16145550100?body=one&foo=bar&body=two")
    assert target is not None
    assert target.body == "one"


def test_missing_recipient_is_not_a_target():
    assert parse_sms_uri("sms:") is None
    assert parse_sms_uri("sms:?body=hi") is None
    assert parse_sms_uri("sms:, ,") is None
    assert parse_sms_uri("") is None
    assert parse_sms_uri("tel:+16145550100") is None
    assert parse_sms_uri("mailto:a@example.com") is None


def test_argv_picks_first_sms_uri():
    target = parse_sms_target_argv(
        ["txxt", "--verbose", "sms:+16145550100?body=hi"])
    assert target is not None
    assert target.recipients == ("+16145550100",)
    assert target.body == "hi"
    assert parse_sms_target_argv(["txxt", "--open-thread", "3"]) is None
    assert parse_sms_target_argv([]) is None


def test_desktop_file_registers_sms_schemes():
    desktop = (Path(__file__).parent.parent / "data"
               / "io.piercingxx.TxxT.desktop")
    lines = desktop.read_text(encoding="utf-8").splitlines()
    mime = [line for line in lines if line.startswith("MimeType=")]
    assert len(mime) == 1
    for scheme in ("sms", "smsto", "mms", "mmsto"):
        assert f"x-scheme-handler/{scheme}" in mime[0], scheme
