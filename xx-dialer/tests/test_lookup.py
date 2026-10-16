"""Policy1 LookupNumber dataset: cache-backed (display_name, tier)."""
from xx_dialer.contacts_client import keypad_search, lookup_number
from xx_dialer.store import MirrorRow


def _ada(store):
    store.upsert_mirror([MirrorRow("uid-ada", "+14155550100", "Ada",
                                   False, None, False, 1)])


def test_unknown_number(store):
    assert lookup_number(store, "+19999999999") == ("", "unknown")
    assert lookup_number(store, "") == ("", "unknown")
    assert lookup_number(store, None) == ("", "unknown")


def test_saved_contact_reports_name(store):
    _ada(store)
    assert lookup_number(store, "+14155550100") == ("Ada", "saved")


def test_overlay_star_beats_business(store):
    _ada(store)
    store.upsert_tier("uid-ada", "biz")
    store.add_star("uid-ada")
    assert lookup_number(store, "+14155550100") == ("Ada", "starred")


def test_mirror_star_flag_counts(store):
    store.upsert_mirror([MirrorRow("uid-ada", "+14155550100", "Ada", True,
                                   None, False, 1)])
    assert lookup_number(store, "+14155550100") == ("Ada", "starred")


def test_business_without_star(store):
    _ada(store)
    store.upsert_tier("uid-ada", "biz")
    assert lookup_number(store, "+14155550100") == ("Ada", "business")


def test_blocked_only_still_reports_saved(store):
    _ada(store)
    store.upsert_tier("uid-ada", "Blocked")
    store.add_blocked("+14155550100")
    assert lookup_number(store, "+14155550100") == ("Ada", "saved")


def test_keypad_search_reads_mirror(store):
    store.upsert_mirror([MirrorRow("u1", "2065550177", "Han Solo", False,
                                   None, False, 1),
                         MirrorRow("u2", "+14265550100", "Zed", False,
                                   None, False, 1)])
    got = keypad_search(store, "426")
    assert [(uid, number) for uid, number, _name, _hit in got] == [
        ("u2", "+14265550100"), ("u1", "2065550177")]
    assert keypad_search(store, "") == []
