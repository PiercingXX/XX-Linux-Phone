"""Rules-screen logic tests: precedence, labels, countdown, tallies."""
import time

from xx_dialer import config as app_config
from xx_dialer import policy, rules
from xx_dialer.store import ScreenRow, Store


def test_precedence_rows_follow_engine_order():
    rows = rules.precedence_rows(policy.Window(540, 1020, 127),
                                 policy.Window(540, 1140, 127))
    assert [row.n for row in rows] == list(range(1, len(rows) + 1))
    assert rows[0].condition == "Emergency callback"
    assert rows[0].kind == "ring"
    assert rows[3].condition == "Blocked numbers"
    assert rows[3].kind == "block"
    assert rows[6].condition == "Starred contact"
    assert rows[-2].condition.startswith("Repeat caller")
    assert rows[-1].condition == "Expecting a call"


def test_precedence_rows_carry_live_windows_and_policies():
    rows = rules.precedence_rows(
        policy.Window(540, 1020, 127), policy.Window(600, 900, 31),
        policy.StirAction.OFF, policy.HiddenCallerPolicy.BLOCK, False)
    by_condition = {row.condition: row for row in rows}
    assert "Business, inside 10:00-15:00 Mon-Fri" in by_condition
    stir = [row for row in rows if row.condition.startswith("STIR")]
    assert stir[0].verdict == "off" and stir[0].kind == "info"
    hidden = [row for row in rows if row.condition == "Hidden caller"]
    assert hidden[0].verdict == "Block"
    repeat = [row for row in rows
              if row.condition == "Repeat caller"]
    assert repeat[0].verdict == "off"


def test_window_labels():
    assert rules.clock_label(540) == "09:00"
    assert rules.clock_label(61) == "01:01"
    window = policy.Window(540, 1020, 127)
    assert rules.time_range(window) == "09:00-17:00"
    assert rules.days_label(127) == "every day"
    assert rules.days_label(0b00011111) == "Mon-Fri"
    assert rules.days_label(0b1010101) == "Mon, Wed, Fri, Sun"
    assert rules.window_line(window) == "09:00-17:00, every day"


def test_day_mask_round_trip():
    mask = rules.mask_from_chips([True, False, True, False, False,
                                  False, False])
    assert mask == 0b0000101
    assert rules.chips_from_mask(mask) == [True, False, True, False,
                                           False, False, False]
    assert rules.toggle_days(mask, 1) == 0b0000111


def test_countdown_idle_and_active_shapes():
    now = 1_000_000_000
    assert rules.countdown_label(None, now) == "Expecting a call"
    assert rules.countdown_label(now - 1000, now) == "Expecting a call"
    assert rules.countdown_label(now + rules.MAX_BYPASS_MS + 1, now) == \
        "Expecting a call"
    assert rules.countdown_label(now + 4 * 60_000, now) == \
        "Expecting, 4m left"
    assert rules.countdown_label(now + (60 + 59) * 60_000, now) == \
        "Expecting, 1h59m left"
    assert rules.countdown_duration(30_000) == "1m"
    assert rules.bypass_active_ms(now + 1000, now)
    assert not rules.bypass_active_ms(now - 1, now)
    assert not rules.bypass_active_ms("soon", now)


def test_bypass_config_ops_round_trip(tmp_path):
    config = app_config.load(tmp_path)
    now = 1_700_000_000_000
    rules.set_bypass(config, 30, now)
    assert rules.bypass_until_ms(config) == now + 30 * 60_000
    assert config["bypass_duration_minutes"] == 30
    rules.clear_bypass(config)
    assert rules.bypass_until_ms(config) is None
    assert rules.duration_label(30) == "30 min"
    assert rules.duration_label(120) == "2 h"


def test_window_config_op_rejects_garbage():
    config = {"unknown_window": {"start": 540, "end": 1020,
                                 "days": 127}}
    rules.set_window(config, "unknown_window", 600, 900, 31)
    assert config["unknown_window"] == {"start": 600, "end": 900,
                                        "days": 31}
    try:
        rules.set_window(config, "unknown_window", 0, 0, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero-day window must raise")


def test_observe_week_seed_is_put_if_absent(tmp_path):
    config = app_config.load(tmp_path)
    now = 1_700_000_000_000
    rules.seed_observe_week(config, now)
    first = config["observe_week_end"]
    assert first == float(now + 7 * 24 * 60 * 60 * 1000)
    rules.seed_observe_week(config, now + 999)
    assert config["observe_week_end"] == first
    assert not rules.offer_enforcement(config, now)
    assert rules.offer_enforcement(config, int(first))


def test_week_start_is_monday_midnight():
    thursday = int(time.mktime(time.strptime("2026-08-20",
                                             "%Y-%m-%d")) * 1000)
    start = rules.week_start_ms(thursday + 12 * 3600 * 1000)
    assert time.strftime("%Y-%m-%d %H:%M", time.localtime(start / 1000)) \
        == "2026-08-17 00:00"


def test_screen_tallies_group_by_verdict_and_reason(tmp_path):
    now = int(time.time() * 1000)
    with Store(tmp_path / "calls.db") as store:
        store.log_screen(ScreenRow(at=now - 1000, verdict="Silence",
                                   reason="unknown caller",
                                   e164="+14155550100"))
        store.log_screen(ScreenRow(at=now - 2000, verdict="Silence",
                                   reason="unknown caller",
                                   e164="+14155550200"))
        store.log_screen(ScreenRow(at=now - 3000, verdict="Block",
                                   reason="blocked",
                                   e164="+14155550300"))
        store.log_screen(ScreenRow(at=now - 8 * 24 * 3600 * 1000,
                                   verdict="Ring",
                                   reason="starred",
                                   e164="+14155550400"))
        week = rules.screen_tallies(store, rules.week_start_ms(now))
        assert week["total"] == 3
        assert week["by_verdict"] == {"Silence": 2, "Block": 1}
        assert week["by_reason"] == {"unknown caller": 2,
                                     "blocked": 1}
        all_time = rules.screen_tallies(store, 0)
        assert all_time["total"] == 4


def test_test_number_runs_the_service_engine(tmp_path):
    from xx_dialer.service import PolicyService

    settings = app_config.load(tmp_path)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, tmp_path)
    service = PolicyService(db_file=tmp_path / "calls.db", home=tmp_path)
    with Store(service._db_file) as store:
        store.add_blocked("+14155550200")
    verdict, reason = rules.test_number(service, "+14155550200")
    assert (verdict, reason) == ("block", "blocked")
    verdict, _reason = rules.test_number(service, "+14155559999")
    assert verdict in ("ring", "silence")
    with Store(service._db_file) as store:
        assert store.last_screened(10) == []
