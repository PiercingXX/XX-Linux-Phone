"""Collector one-shot: opt-in gate, hardware verbatim, no-source honesty."""

from xx_vitals import prefs
from xx_vitals.collect.sampler import StepReading
from xx_vitals.collect.service import (
    OUTCOME_DISABLED,
    OUTCOME_NO_SOURCE,
    OUTCOME_OK,
    SOURCE_HARDWARE,
    run_once,
)
from xx_vitals.store.db import Database, WeightSample

TODAY = "2026-08-27"
ELAPSED = 1_000_000
WALL = 1_000_000_000


def _db():
    db = Database(":memory:")
    db.init()
    return db


def _enabled(height=1.70, weight_kg=None):
    cfg = prefs.AppConfig(collect_enabled=True, height_metres=height)
    db = _db()
    if weight_kg is not None:
        db.upsert_weight(WeightSample(source_id="manual", measured_at=1_000, weight_kg=weight_kg))
    return db, cfg


def _reading(counter, elapsed=ELAPSED, wall=WALL):
    return StepReading(counter_value=float(counter), elapsed_realtime_ms=elapsed, wall_clock_ms=wall)


def test_disabled_by_default_writes_nothing():
    db = _db()
    cfg = prefs.AppConfig()
    assert cfg.collect_enabled is False
    saved = []
    result = run_once(
        db, cfg, now_ms=WALL, today=TODAY, elapsed_ms=ELAPSED, wall_ms=WALL,
        hardware=_reading(8_000),
        on_save=saved.append,
    )
    assert result.outcome == OUTCOME_DISABLED
    assert db.count_dailies() == 0
    assert cfg.step_baseline is None
    assert saved == []


def test_hardware_cold_start_records_baseline_writes_nothing():
    db, cfg = _enabled()
    result = run_once(
        db, cfg, now_ms=WALL, today=TODAY, elapsed_ms=ELAPSED, wall_ms=WALL,
        hardware=_reading(8_000),
        on_save=lambda _c: None,
    )
    assert result.outcome == OUTCOME_OK and result.source == SOURCE_HARDWARE
    assert result.steps == 0 and not result.rebooted
    assert db.count_dailies() == 0
    assert cfg.step_baseline is not None and cfg.step_baseline.counter == 8_000


def test_hardware_increment_accumulates_derived_and_enqueues():
    db, cfg = _enabled(height=1.70, weight_kg=70.0)
    tick = {"elapsed": ELAPSED, "wall": WALL}
    run_once(
        db, cfg, now_ms=WALL, today=TODAY, elapsed_ms=tick["elapsed"], wall_ms=tick["wall"],
        hardware=_reading(8_000),
    )
    result = run_once(
        db, cfg, now_ms=WALL + 100_000, today=TODAY,
        elapsed_ms=tick["elapsed"] + 100_000, wall_ms=tick["wall"] + 100_000,
        hardware=_reading(8_900, tick["elapsed"] + 100_000, tick["wall"] + 100_000),
    )
    assert result.steps == 900
    row = db.get_daily("phone", TODAY)
    assert row is not None and row.steps == 900
    assert row.distance_m is not None and row.calories_kcal is not None
    assert row.active_min == 9 and row.effort_points == 9
    assert row.derived_mask == 0b1111
    assert db.pending_count() == 1


def test_hardware_reboot_counts_whole_new_value_verbatim():
    db, cfg = _enabled()
    run_once(
        db, cfg, now_ms=WALL, today=TODAY, elapsed_ms=5_000_000, wall_ms=5_000_000_000,
        hardware=_reading(8_000, 5_000_000, 5_000_000_000),
    )
    result = run_once(
        db, cfg, now_ms=6_000_000_000, today=TODAY, elapsed_ms=100_000, wall_ms=6_000_000_000,
        hardware=_reading(900, 100_000, 6_000_000_000),
    )
    assert result.steps == 900 and result.rebooted
    assert db.get_daily("phone", TODAY).steps == 900


def test_hardware_sensor_hub_restart_rebases_attributes_nothing():
    db, cfg = _enabled()
    run_once(
        db, cfg, now_ms=WALL, today=TODAY, elapsed_ms=ELAPSED, wall_ms=WALL,
        hardware=_reading(8_000),
    )
    result = run_once(
        db, cfg, now_ms=WALL + 100_000, today=TODAY,
        elapsed_ms=ELAPSED + 100_000, wall_ms=WALL + 100_000,
        hardware=_reading(900, ELAPSED + 100_000, WALL + 100_000),
    )
    assert result.steps == 0 and result.rebooted
    assert db.count_dailies() == 0
    assert cfg.step_baseline.counter == 900


def test_null_read_writes_nothing_leaves_baseline():
    db, cfg = _enabled()
    run_once(
        db, cfg, now_ms=WALL, today=TODAY, elapsed_ms=ELAPSED, wall_ms=WALL,
        hardware=_reading(8_000),
    )
    before = cfg.step_baseline
    result = run_once(
        db, cfg, now_ms=WALL + 100_000, today=TODAY,
        elapsed_ms=ELAPSED + 100_000, wall_ms=WALL + 100_000,
        hardware=None,
    )
    assert result.outcome == OUTCOME_NO_SOURCE
    assert cfg.step_baseline == before
    assert db.count_dailies() == 0


def test_missing_height_weight_stay_null_never_zero():
    db, cfg = _enabled(height=0.0, weight_kg=None)
    run_once(
        db, cfg, now_ms=WALL, today=TODAY, elapsed_ms=ELAPSED, wall_ms=WALL,
        hardware=_reading(8_000),
    )
    run_once(
        db, cfg, now_ms=WALL + 100_000, today=TODAY,
        elapsed_ms=ELAPSED + 100_000, wall_ms=WALL + 100_000,
        hardware=_reading(8_500, ELAPSED + 100_000, WALL + 100_000),
    )
    row = db.get_daily("phone", TODAY)
    assert row.steps == 500
    assert row.distance_m is None and row.calories_kcal is None
    assert row.derived_mask == 0b0110


def test_no_hardware_counter_is_no_source_not_an_estimate():
    db, cfg = _enabled(height=1.70, weight_kg=70.0)
    result = run_once(
        db, cfg, now_ms=WALL, today=TODAY, elapsed_ms=ELAPSED, wall_ms=WALL,
        hardware=None,
    )
    assert result.outcome == OUTCOME_NO_SOURCE
    assert result.steps == 0
    assert db.count_dailies() == 0
    assert cfg.step_baseline is None


def test_legacy_burst_baseline_cold_starts_on_hardware():
    from xx_vitals.prefs import StepBaseline

    db, cfg = _enabled()
    cfg.step_baseline = StepBaseline(counter=12.0, elapsed_ms=ELAPSED, wall_ms=WALL, source="burst")
    result = run_once(
        db, cfg, now_ms=WALL + 100_000, today=TODAY,
        elapsed_ms=ELAPSED + 100_000, wall_ms=WALL + 100_000,
        hardware=_reading(8_000, ELAPSED + 100_000, WALL + 100_000),
    )
    assert result.outcome == OUTCOME_OK and result.source == SOURCE_HARDWARE
    assert result.steps == 0 and result.rebooted
    assert db.count_dailies() == 0
    assert cfg.step_baseline.source == SOURCE_HARDWARE


def test_prefs_round_trip_keeps_collect_keys(tmp_path):
    path = tmp_path / "config.json"
    cfg = prefs.AppConfig(collect_enabled=True, step_noise_floor=0.33)
    prefs.save(cfg, path)
    reloaded = prefs.load(path)
    assert reloaded.collect_enabled is True
    assert reloaded.step_noise_floor == 0.33
    assert reloaded.step_baseline is None
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"collect_enabled": "yes", "step_baseline": {"counter": "many"}}',
        encoding="utf-8",
    )
    assert prefs.load(bad).collect_enabled is False
