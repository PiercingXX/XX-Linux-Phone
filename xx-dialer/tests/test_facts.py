"""facts.py over the warm tables: no D-Bus, never a contacts round trip."""
from datetime import datetime

import pytest

from xx_dialer import config as app_config
from xx_dialer import facts
from xx_dialer import policy
from xx_dialer.store import (
    CallRow,
    MirrorRow,
    ScreenRow,
    load_rules,
)

MONDAY_10AM = datetime(2026, 10, 12, 10, 0)
MONDAY_NIGHT = datetime(2026, 10, 12, 23, 30)
WALL = 1_790_000_000_000


def _enforcing(home, store):
    settings = app_config.load(home)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, home)
    return app_config.load(home)


def _starred(store, uid="+uid-ada", number="+14155550100"):
    store.upsert_mirror([MirrorRow(uid, number, "Ada Lovelace", True,
                                   None, False, WALL)])
    store.add_star(uid)


def test_build_facts_starred_from_warm_mirror(store, tmp_path):
    _starred(store)
    got = facts.build_facts("+1 (415) 555-0100", MONDAY_10AM, store,
                            wall_ms=WALL, monotonic_ms=WALL)
    assert got.number == "+14155550100"
    assert (got.saved, got.starred) == (True, True)
    assert got.cnap_name == "Ada Lovelace"
    assert got.stir_failed is False


def test_build_facts_unknown_and_withheld(store, tmp_path):
    unknown = facts.build_facts("+14155559999", MONDAY_10AM, store,
                                wall_ms=WALL, monotonic_ms=WALL)
    assert unknown.number == "+14155559999"
    assert not (unknown.saved or unknown.starred or unknown.withheld)
    garbage = facts.build_facts("!!", MONDAY_10AM, store, wall_ms=WALL,
                                monotonic_ms=WALL)
    assert garbage.number is None and not garbage.withheld
    for raw in (None, "", "   "):
        hidden = facts.build_facts(raw, MONDAY_10AM, store, wall_ms=WALL,
                                   monotonic_ms=WALL)
        assert hidden.withheld is True and hidden.number is None


def test_build_facts_blocked_and_repeat_windows(store, tmp_path):
    store.add_blocked("+14155550200")
    blocked = facts.build_facts("+14155550200", MONDAY_10AM, store,
                                wall_ms=WALL, monotonic_ms=WALL)
    assert blocked.user_blocked is True
    store.log_screen(ScreenRow(at=WALL - 60_000, verdict="Silence",
                              reason="unknown caller",
                              e164="+14155559999"))
    repeat = facts.build_facts("+14155559999", MONDAY_10AM, store,
                               wall_ms=WALL, monotonic_ms=WALL)
    assert repeat.repeat_caller is True
    store.add_call(CallRow(date=WALL - 1_000, number="+14155559999",
                           call_type=2, duration=30))
    outgoing = facts.build_facts("+14155559999", MONDAY_10AM, store,
                                 wall_ms=WALL, monotonic_ms=WALL)
    assert outgoing.recent_outgoing is True


def test_build_facts_emergency_window(store, tmp_path):
    store.record_emergency(WALL - 1_000, WALL - 1_000)
    got = facts.build_facts("+14155559999", MONDAY_10AM, store,
                            wall_ms=WALL, monotonic_ms=WALL)
    assert got.emergency_window is True


def test_screen_block_and_silence_enforcing(store, tmp_path):
    config = _enforcing(tmp_path, store)
    store.add_blocked("+14155550200")
    verdict, reason, mode = facts.screen_number(
        "+14155550200", MONDAY_10AM, store, config, wall_ms=WALL,
        monotonic_ms=WALL)
    assert (verdict, reason, mode) == ("block", "blocked", "enforced")
    latest = store.latest_for("+14155550200")
    assert latest is not None and latest.verdict == "Block"
    assert latest.mode == "enforced"
    verdict, reason, mode = facts.screen_number(
        "+14155559999", MONDAY_NIGHT, store, config, wall_ms=WALL,
        monotonic_ms=WALL)
    assert verdict == "silence" and mode == "enforced"
    assert reason == policy.Reason.UNKNOWN_OUTSIDE_WINDOW.value


def test_screen_observe_rings_but_logs_computed(store, tmp_path):
    config = app_config.load(tmp_path)
    assert config["enforcement_mode"] == "observing"
    verdict, reason, mode = facts.screen_number(
        "+14155559999", MONDAY_NIGHT, store, config, wall_ms=WALL,
        monotonic_ms=WALL)
    assert (verdict, mode) == ("ring", "observed")
    latest = store.latest_for("+14155559999")
    assert latest is not None and latest.verdict == "Silence"
    assert latest.mode == "observed"


def test_screen_first_run_rings_everything(store, tmp_path):
    config = app_config.load(tmp_path)
    verdict, _reason, _mode = facts.screen_number(
        "+14155559999", MONDAY_NIGHT, store, config, wall_ms=WALL,
        monotonic_ms=WALL)
    assert verdict == "ring"


def test_reason_labels_walk_precedence(store, tmp_path):
    config = _enforcing(tmp_path, store)
    _starred(store)
    rules = load_rules(store, config)
    verdict, reason, _mode = facts.screen_number(
        "+14155550100", MONDAY_NIGHT, store, config, rules=rules,
        wall_ms=WALL, monotonic_ms=WALL)
    assert (verdict, reason) == ("ring", "starred")
    from xx_dialer.store import PatternRow

    store.add_pattern(PatternRow(e164_prefix="1415555", wildcards=4,
                                action="block"))
    rules = load_rules(store, config)
    verdict, reason, _mode = facts.screen_number(
        "+14155550999", MONDAY_10AM, store, config, rules=rules,
        wall_ms=WALL, monotonic_ms=WALL)
    assert (verdict, reason) == ("block", "blocked by pattern")


def test_reason_repeat_pierce(store, tmp_path):
    config = _enforcing(tmp_path, store)
    store.log_screen(ScreenRow(at=WALL - 60_000, verdict="Silence",
                              reason="unknown caller",
                              e164="+14155559999"))
    verdict, reason, _mode = facts.screen_number(
        "+14155559999", MONDAY_NIGHT, store, config, wall_ms=WALL,
        monotonic_ms=WALL)
    assert (verdict, reason) == ("ring", "repeat caller")


def test_note_call_incoming_marks_screen_answered(store):
    store.log_screen(ScreenRow(at=WALL, verdict="Silence",
                              reason="unknown caller",
                              e164="+14155550100"))
    summary = facts.note_call(store, "+14155550100", "incoming", WALL,
                              45_000, "answered", wall_ms=WALL + 60_000,
                              monotonic_ms=WALL + 60_000)
    assert summary["answered"] is True and summary["duration"] == 45
    assert store.call_count() == 1
    assert store.latest_for("+14155550100").answered is True


def test_note_call_outgoing_feeds_recent_and_emergency(store):
    facts.note_call(store, "+14155550100", "outgoing", WALL, 10_000,
                    "answered", wall_ms=WALL, monotonic_ms=WALL)
    assert store.has_outgoing_since("+14155550100", WALL - 1_000) is True
    assert store.emergency_active(WALL + 1_000, WALL + 1_000) is False
    facts.note_call(store, "911", "outgoing", WALL, 5_000, "answered",
                    wall_ms=WALL, monotonic_ms=WALL)
    assert store.emergency_active(WALL + 1_000, WALL + 1_000) is True


def test_note_call_tolerates_garbage_disposition(store):
    summary = facts.note_call(store, "+14155550100", "incoming", WALL,
                              0, "weird", wall_ms=WALL,
                              monotonic_ms=WALL)
    assert summary["answered"] is False
    assert store.call_count() == 1
    with pytest.raises(ValueError):
        facts.note_call(store, "+14155550100", "martian", WALL, 0,
                        "missed", wall_ms=WALL, monotonic_ms=WALL)


def test_facts_module_has_no_bus_imports():
    from pathlib import Path

    text = (Path(__file__).parent.parent
            / "src" / "xx_dialer" / "facts.py").read_text()
    assert "gi.repository" not in text
    assert "from gi" not in text
    assert "bus_get_sync" not in text
    assert "signal_subscribe" not in text
    assert "Gio." not in text and "GLib." not in text


def test_lookup_tier_prefers_star(store):
    _starred(store, uid="uid-ada", number="+14155550100")
    store.upsert_mirror([MirrorRow("uid-bo", "+14155550200", "Bo Dahl",
                                   False, None, False, WALL)])
    from xx_dialer.store import BIZ_TIER

    store.upsert_tier("uid-bo", BIZ_TIER)
    assert facts.lookup_tier(store, "+14155550100")[1] == "starred"
    assert facts.lookup_tier(store, "+14155550200")[1] == "business"
    assert facts.lookup_tier(store, "+14155559999") == ("", "unknown")
    assert facts.lookup_tier(store, None) == ("", "unknown")
