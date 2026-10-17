"""Store tests: schema, DAO helpers, log semantics, rules loading."""
from xx_dialer.store import (
    CallRow,
    MirrorRow,
    PatternRow,
    ScreenRow,
    load_rules,
)


def test_patterns_round_trip_in_created_order(store):
    store.add_pattern(PatternRow("1800555", 4, "silence", created_at=2))
    store.add_pattern(PatternRow("1425555", 4, "block", created_at=1))
    rows = store.all_patterns()
    assert [(r.e164_prefix, r.action) for r in rows] == [
        ("1425555", "block"), ("1800555", "silence")]
    store.delete_pattern(rows[0].row_id)
    assert [r.e164_prefix for r in store.all_patterns()] == ["1800555"]
    store.clear_patterns()
    assert store.all_patterns() == []


def test_tier_membership_is_keyed_by_lookup_key_and_tier(store):
    store.upsert_tier("abc/", "biz", 10)
    store.upsert_tier("abc/", "family", 11)
    store.upsert_tier("abc/", "biz", 12)
    assert sorted(store.keys_for_tier("biz")) == ["abc/"]
    assert len(store.all_tiers()) == 2
    store.remove_tier("abc/", "biz")
    assert store.keys_for_tier("biz") == []
    store.remove_tiers_for_key("abc/")
    assert store.all_tiers() == []


def test_blocked_numbers_dedupe_and_remove(store):
    store.add_blocked("+14155550100")
    store.add_blocked("+14155550100")
    assert store.is_blocked("+14155550100")
    assert store.all_blocked() == ["+14155550100"]
    store.remove_blocked("+14155550100")
    assert not store.is_blocked("+14155550100")


def test_star_overlay(store):
    store.add_star("uid-1")
    store.add_star("uid-1")
    assert store.is_starred("uid-1")
    assert store.all_starred() == ["uid-1"]
    store.remove_star("uid-1")
    assert not store.is_starred("uid-1")


def test_mirror_find_resolves_biz_tier_with_join(store):
    store.upsert_mirror([MirrorRow("abc/", "+14155550100", "Ada", True,
                                   None, False, 7)])
    store.upsert_tier("abc/", "biz")
    row = store.find_mirror("+14155550100")
    assert row is not None
    assert row.display_name == "Ada" and row.starred and row.biz_tier
    assert store.find_mirror("+19999999999") is None
    assert store.mirror_count() == 1
    assert store.mirror_numbers_for_key("abc/") == ["+14155550100"]
    store.delete_stale_mirror(8)
    assert store.mirror_count() == 0


def test_screen_log_labels_order_answers_and_tallies(store):
    first = store.log_screen(ScreenRow(at=100, verdict="Silence",
                                       reason="Unknown, outside 09-17",
                                       e164="+14155550100",
                                       mode="observed"))
    store.log_screen(ScreenRow(at=200, verdict="Block", reason="blocked",
                               e164="+14155550100", mode="enforced"))
    rows = store.last_screened(10)
    assert [r.at for r in rows] == [200, 100]
    latest = store.latest_for("+14155550100")
    assert latest is not None and latest.verdict == "Block"
    store.set_answered(first, True)
    assert store.latest_for("+14155550100").verdict == "Block"
    assert store.silence_count_since("+14155550100", 50) == 1
    assert store.silence_count_since("+14155550100", 150) == 0
    assert store.tallies_since(50) == {"Silence": 1, "Block": 1}
    store.clear_screen_log()
    assert store.last_screened(10) == []


def test_screen_log_prunes_oldest_first_at_1000(store):
    for at in range(1, 1006):
        store.log_screen(ScreenRow(at=at, verdict="Ring", reason="saved"))
    rows = store.last_screened(2000)
    assert len(rows) == 1000
    assert min(r.at for r in rows) == 6


def test_call_log_identity_matching(store):
    store.add_call(CallRow(date=1000, number="+14155550100", call_type=2,
                           duration=30, cached_name="Ada"))
    assert store.has_call("+14155550100", 1000)
    assert not store.has_call("+14155550100", 1001)
    assert not store.has_call("+19999999999", 1000)
    store.add_call(CallRow(date=1000, number=None, call_type=3))
    assert store.has_call(None, 1000)
    assert store.has_outgoing_since("+14155550100", 999)
    assert not store.has_outgoing_since("+14155550100", 1001)
    assert not store.has_outgoing_since("+19999999999", 0)
    store.add_call(CallRow(date=2000, number="+14155550100", call_type=1))
    assert [r.date for r in store.calls_for("+14155550100")] == [2000, 1000]


def test_load_rules_combines_config_and_stored_patterns(store):
    store.add_pattern(PatternRow("1425555", 4, "block", created_at=1))
    app_config = {"unknown_window": {"start": 600, "end": 900,
                                      "days": 31},
                  "business_window": {"start": 540, "end": 1140,
                                       "days": 127},
                  "hidden_caller_policy": "silence",
                  "stir_action": "off",
                  "repeat_caller_enabled": False,
                  "bypass_until": "1700000000000"}
    rules = load_rules(store, app_config)
    assert (rules.unknown_window.start_minute,
            rules.unknown_window.end_minute) == (600, 900)
    assert rules.hidden_caller_policy.value == "silence"
    assert rules.stir_action.value == "off"
    assert not rules.repeat_caller_enabled
    assert rules.bypass_until is not None
    assert len(rules.block_patterns) == 1


def test_pending_edits_merge_to_net_delta(store):
    store.record_pending_edit("uid-a", ["starred"], 1, add=["starred"],
                              remove=[])
    store.record_pending_edit("uid-a", ["business", "starred"], 2,
                              add=["business"], remove=[])
    assert store.pending_deltas() == [
        ("uid-a", ["business", "starred"], [], 2)]
    store.record_pending_edit("uid-a", ["business"], 3, add=[],
                              remove=["starred"])
    assert store.pending_deltas() == [
        ("uid-a", ["business"], ["starred"], 3)]
    assert [uid for uid, _tags, _ts in store.pending_edits()] == ["uid-a"]
    store.clear_pending_edit("uid-a")
    assert store.pending_deltas() == []


def test_pending_delta_columns_migrate(tmp_path):
    import sqlite3

    from xx_dialer.store import Store

    path = tmp_path / "calls.db"
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE pending_tag_edit (uid TEXT PRIMARY KEY,"
               " tags TEXT NOT NULL DEFAULT '[]',"
               " updatedAt INTEGER NOT NULL DEFAULT 0)")
    db.execute("INSERT INTO pending_tag_edit (uid, tags, updatedAt)"
               " VALUES ('uid-a', '[\"blocked\"]', 7)")
    db.commit()
    db.close()
    handle = Store(path)
    try:
        assert handle.pending_deltas() == [
            ("uid-a", ["blocked"], [], 7)]
    finally:
        handle.close()
