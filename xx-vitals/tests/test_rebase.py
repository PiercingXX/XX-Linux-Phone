"""Port of StepReaderTest + StepMetricMapperTest (pure rebase + mapping)."""

from xx_vitals.collect import sampler
from xx_vitals.collect.sampler import StepReading


def _reading(counter, elapsed_ms=1_000_000, wall_ms=1_000_000_000):
    return StepReading(counter_value=float(counter), elapsed_realtime_ms=elapsed_ms, wall_clock_ms=wall_ms)


def test_cold_start_records_baseline_attributes_nothing():
    now = _reading(8_000)
    delta = sampler.rebase(None, now)
    assert delta.steps == 0
    assert delta.baseline == now
    assert not delta.rebooted


def test_normal_increment_is_difference():
    last = _reading(8_000)
    now = _reading(8_900, elapsed_ms=2_000_000, wall_ms=1_001_000_000)
    delta = sampler.rebase(last, now)
    assert delta.steps == 900
    assert delta.baseline == now
    assert not delta.rebooted


def test_zero_elapsed_steps_is_zero_delta():
    last = _reading(8_000)
    now = _reading(8_000, elapsed_ms=2_000_000, wall_ms=1_001_000_000)
    delta = sampler.rebase(last, now)
    assert delta.steps == 0
    assert not delta.rebooted
    assert delta.baseline == now


def test_reboot_by_elapsed_going_backwards():
    last = _reading(8_000, elapsed_ms=5_000_000, wall_ms=5_000_000_000)
    now = _reading(900, elapsed_ms=100_000, wall_ms=6_000_000_000)
    delta = sampler.rebase(last, now)
    assert delta.steps == 900
    assert delta.rebooted
    assert delta.baseline == now


def test_reboot_by_boot_clock_jump_and_counter_reset():
    last = _reading(8_000)
    now = _reading(900, elapsed_ms=2_000_000, wall_ms=3_000_000_000)
    delta = sampler.rebase(last, now)
    assert delta.steps == 900
    assert delta.rebooted


def test_ntp_catch_up_not_a_reboot():
    last = _reading(8_000)
    now = _reading(8_900, elapsed_ms=1_010_000, wall_ms=1_000_055_000)
    delta = sampler.rebase(last, now)
    assert delta.steps == 900
    assert not delta.rebooted


def test_large_ntp_jump_with_climbing_counter_not_a_reboot():
    last = _reading(8_000)
    now = _reading(8_400, elapsed_ms=1_060_000, wall_ms=1_000_420_000)
    delta = sampler.rebase(last, now)
    assert delta.steps == 400
    assert not delta.rebooted


def test_boot_jump_with_near_zero_counter_is_reboot():
    last = _reading(8_000)
    now = _reading(20, elapsed_ms=2_000_000, wall_ms=1_000_360_000)
    delta = sampler.rebase(last, now)
    assert delta.steps == 20
    assert delta.rebooted


def test_counter_looks_reset():
    assert sampler.counter_looks_reset(8_000.0, 900.0)
    assert sampler.counter_looks_reset(8_000.0, 20.0)
    assert not sampler.counter_looks_reset(8_000.0, 8_400.0)
    assert not sampler.counter_looks_reset(8_000.0, 8_000.0)
    assert not sampler.counter_looks_reset(30.0, 40.0)


def test_counter_overflow_wraps_rebases_attributes_nothing():
    last = _reading(4_294_967_290, elapsed_ms=2_000_000, wall_ms=1_001_000_000)
    now = _reading(12, elapsed_ms=3_000_000, wall_ms=1_002_000_000)
    delta = sampler.rebase(last, now)
    assert delta.steps == 0
    assert delta.rebooted
    assert delta.baseline == now


def test_sensor_hub_restart_rebases_attributes_nothing():
    last = _reading(8_000)
    now = _reading(900, elapsed_ms=2_000_000, wall_ms=1_001_000_000)
    delta = sampler.rebase(last, now)
    assert delta.steps == 0
    assert delta.rebooted
    assert delta.baseline == now


def test_float_precision_past_2pow24():
    last = _reading(16_777_216, wall_ms=1_000_000_000)
    now = _reading(16_777_300, elapsed_ms=2_000_000, wall_ms=1_001_000_000)
    delta = sampler.rebase(last, now)
    assert delta.steps == 84
    assert not delta.rebooted


def test_looks_blocked():
    assert not sampler.looks_blocked(0.0, sampler.STUCK_ZERO_ELAPSED_MS)
    assert sampler.looks_blocked(0.0, sampler.STUCK_ZERO_ELAPSED_MS + 1)
    assert not sampler.looks_blocked(1.0, sampler.STUCK_ZERO_ELAPSED_MS + 1)


def test_map_total_to_derived():
    m = sampler.map_steps(10_000, [], 170.0, 70.0)
    assert abs(m.distance_m - 7055.0) < 0.01
    assert abs(m.calories_kcal - 342.95) < 0.01
    assert m.active_min == 100
    assert m.effort_points == 100


def test_map_per_minute_floor_and_cadence():
    m = sampler.map_steps(300, [0.0, 59.0, 60.0, 61.0, 100.0], 170.0, 70.0)
    assert m.active_min == 3
    m2 = sampler.map_steps(400, [90.0, 100.0, 120.0, 130.0, 200.0], 170.0, 70.0)
    assert m2.effort_points == 0 + 1 + 1 + 2 + 2


def test_map_zero_and_negative_clamped():
    m = sampler.map_steps(0, [], 170.0, 70.0)
    assert m.distance_m == 0.0
    assert m.calories_kcal == 0.0
    m2 = sampler.map_steps(-5, [], 170.0, 70.0)
    assert m2.distance_m == 0.0
    assert m2.calories_kcal == 0.0


def test_map_helpers_match_canonical():
    assert abs(sampler.distance_m(10_000, 170.0) - 7055.0) < 0.01
    assert abs(sampler.calories_kcal(10_000, 70.0, 170.0) - 342.95) < 0.01
