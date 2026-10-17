"""Collector-owned step state: the toggle is re-checked in the loop and
processes never overwrite each other's config fields.

Regression tests for the "turning step counting off turns itself back
on" and "Sample now double-counts from an app-open baseline" bugs.
Headless: state and config paths are injected, HOME is never touched.
"""

import threading

from xx_vitals import prefs
from xx_vitals.collect.sampler import StepReading
from xx_vitals.collect import service as collect_service
from xx_vitals.store.db import Database

TODAY = "2026-08-27"
ELAPSED = 1_000_000
WALL = 1_000_000_000


def _db():
    db = Database(":memory:")
    db.init()
    return db


def _reading(counter, elapsed=ELAPSED, wall=WALL):
    return StepReading(counter_value=float(counter),
                       elapsed_realtime_ms=elapsed,
                       wall_clock_ms=wall)


def _write_state(state_path, counter, elapsed=ELAPSED, wall=WALL,
                 source="hardware"):
    collect_service.store_state_baseline(
        _reading(counter, elapsed, wall), source, state_path)


def test_prefs_update_writes_only_given_fields(tmp_path):
    path = tmp_path / "config.json"
    prefs.save(prefs.AppConfig(collect_enabled=False, steps_goal=8000,
                               step_noise_floor=0.5), path)
    prefs.update(path, step_noise_floor=0.9)
    reloaded = prefs.load(path)
    assert reloaded.collect_enabled is False
    assert reloaded.steps_goal == 8000
    assert reloaded.step_noise_floor == 0.9


def test_prefs_update_rejects_unknown_fields(tmp_path):
    path = tmp_path / "config.json"
    prefs.save(prefs.AppConfig(), path)
    try:
        prefs.update(path, collect_enabledd=True)
    except TypeError:
        pass
    else:
        raise AssertionError("typo field must not be written")
    assert "collect_enabledd" not in path.read_text(encoding="utf-8")


def test_collector_persist_never_reenables_collection(tmp_path):
    config_path = tmp_path / "config.json"
    state_path = tmp_path / "collector-state.json"
    prefs.save(prefs.AppConfig(collect_enabled=False, steps_goal=8000),
               config_path)
    stale = prefs.AppConfig(collect_enabled=True, steps_goal=8000)
    stale.step_baseline = prefs.StepBaseline(
        counter=9000.0, elapsed_ms=ELAPSED, wall_ms=WALL, source="hardware")
    stale.step_noise_floor = 0.9
    collect_service.persist_collector_state(
        stale, config_path=config_path, state_path=state_path)
    reloaded = prefs.load(config_path)
    assert reloaded.collect_enabled is False
    assert reloaded.steps_goal == 8000
    assert reloaded.step_noise_floor == 0.9
    assert collect_service.load_state_baseline(state_path).counter == 9000.0


def test_state_baseline_round_trip_and_bad_data(tmp_path):
    state_path = tmp_path / "collector-state.json"
    assert collect_service.load_state_baseline(state_path) is None
    _write_state(state_path, 8900.0)
    baseline = collect_service.load_state_baseline(state_path)
    assert (baseline.counter, baseline.elapsed_ms, baseline.wall_ms,
            baseline.source) == (8900.0, ELAPSED, WALL, "hardware")
    state_path.write_text('{"step_baseline": {"counter": "many"}}',
                          encoding="utf-8")
    assert collect_service.load_state_baseline(state_path) is None


def test_runtime_config_prefers_state_baseline(tmp_path):
    from xx_vitals.prefs import StepBaseline

    config_path = tmp_path / "config.json"
    state_path = tmp_path / "collector-state.json"
    legacy = prefs.AppConfig(collect_enabled=True)
    legacy.step_baseline = StepBaseline(
        counter=8000.0, elapsed_ms=ELAPSED, wall_ms=WALL, source="hardware")
    prefs.save(legacy, config_path)
    assert collect_service.load_collector_config(
        config_path, state_path).step_baseline.counter == 8000.0
    _write_state(state_path, 8900.0)
    assert collect_service.load_collector_config(
        config_path, state_path).step_baseline.counter == 8900.0


def test_resident_loop_stops_when_toggle_turned_off(tmp_path):
    db = _db()
    cfg = prefs.AppConfig(collect_enabled=True, height_metres=1.70)
    saved = []
    reads = []

    def fake_read():
        reads.append(1)
        return (0.0, 0.0, 9.81)

    calls = []

    def gate():
        calls.append(1)
        return len(calls) < 2

    stop = threading.Event()
    sampler = collect_service.build_resident_sampler(db, cfg)
    collect_service.run_resident(
        db, cfg, read_sample=fake_read, stop=stop, sampler=sampler,
        save_config=saved.append, still_poll_s=0.001, clock=lambda: 0.0,
        is_enabled=gate)
    assert len(reads) == 1
    assert saved == []
    assert db.count_dailies() == 0


def test_sample_now_uses_fresh_baseline(tmp_path):
    from xx_vitals.ui import profile

    state_path = tmp_path / "collector-state.json"
    _write_state(state_path, 8900.0)
    db = _db()
    cfg = prefs.AppConfig(collect_enabled=True, height_metres=1.70)
    cfg.step_baseline = prefs.StepBaseline(
        counter=8000.0, elapsed_ms=ELAPSED, wall_ms=WALL, source="hardware")
    result = profile.tick_collect_now(
        db, cfg, hardware=_reading(9000.0), now_ms=WALL + 100_000,
        today=TODAY, elapsed_ms=ELAPSED + 100_000,
        wall_ms=WALL + 100_000, _state_path=state_path)
    assert result.steps == 100
