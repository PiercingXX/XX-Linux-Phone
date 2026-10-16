"""Port of CallLogJsonTest (app/.../backup/CallLogJsonTest.kt).

Pure codec: the call-log JSON round-trips a few rows (BK-1).
"""
from xx_dialer.codecs import CallLogEntry, decode_calllog, encode_calllog


def sample_entries() -> list[CallLogEntry]:
    return [
        CallLogEntry(number="+14255551234", call_type=1,
                     date=1700000000000, duration=42, cached_name="Alice"),
        CallLogEntry(number="+14255555678", call_type=2,
                     date=1700000100000, duration=0, cached_name=None),
        CallLogEntry(number=None, call_type=3, date=1700000200000,
                     duration=5, cached_name="Withheld caller"),
    ]


def test_round_trips_a_few_call_log_rows():
    original = sample_entries()
    assert decode_calllog(encode_calllog(original)) == original


def test_encode_of_an_empty_list_decodes_back_to_empty():
    assert decode_calllog(encode_calllog([])) == []


def test_decode_of_garbage_is_an_empty_list_never_throws():
    assert decode_calllog("{not json") == []
    assert decode_calllog("") == []


def test_decode_of_a_newer_schema_is_an_empty_list():
    assert decode_calllog('{"schema":99,"entries":[{"date":1}]}') == []


def test_a_row_missing_its_date_is_dropped():
    text = '{"schema":1,"entries":[{"number":"123","type":1,"duration":1}]}'
    assert decode_calllog(text) == []
