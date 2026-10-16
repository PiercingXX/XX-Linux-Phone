"""Port of :core unit tests: Derive, Derived, MetricMath, GoalModel, UnitConvert."""

import pytest

from xx_vitals.core import metrics
from xx_vitals.core.metrics import Derived


def test_effort_zero_steps_is_zero():
    assert metrics.effort_from_cadence(0.0).value == 0.0


def test_effort_stationary_day_is_zero_not_nan():
    assert sum(metrics.effort_from_cadence(0.0).value for _ in range(1440)) == 0.0


def test_effort_thresholds():
    assert metrics.effort_from_cadence(99.0).value == 0.0
    assert metrics.effort_from_cadence(100.0).value == 1.0
    assert metrics.effort_from_cadence(101.0).value == 1.0
    assert metrics.effort_from_cadence(129.0).value == 1.0
    assert metrics.effort_from_cadence(130.0).value == 2.0
    assert metrics.effort_from_cadence(131.0).value == 2.0
    assert metrics.effort_from_cadence(200.0).value == 2.0


def test_distance_zero_is_zero():
    assert metrics.distance(0.0, 170.0).value == 0.0


def test_distance_scales_stride_from_height():
    assert metrics.distance(10_000.0, 170.0).value == pytest.approx(7055.0, abs=0.01)


def test_distance_proportional_to_steps():
    one = metrics.distance(1.0, 170.0).value
    assert metrics.distance(2.0, 170.0).value == pytest.approx(2.0 * one, abs=0.0001)


def test_taller_height_more_distance():
    assert metrics.distance(10_000.0, 190.0).value > metrics.distance(10_000.0, 160.0).value


def test_calories_zero_not_nan():
    assert metrics.calories(0.0, 70.0, 170.0).value == 0.0


def test_calories_hand_computed():
    assert metrics.calories(10_000.0, 70.0, 170.0).value == pytest.approx(342.95, abs=0.01)


def test_calories_scales_with_weight():
    light = metrics.calories(10_000.0, 60.0, 170.0).value
    heavy = metrics.calories(10_000.0, 90.0, 170.0).value
    assert heavy == pytest.approx(1.5 * light, abs=0.01)


def test_active_minutes_floor():
    assert metrics.active_minutes([0.0, 59.0, 60.0, 61.0, 100.0]).value == 3
    assert metrics.active_minutes([0.0, 10.0, 59.0]).value == 0
    assert metrics.active_minutes([]).value == 0
    assert metrics.active_minutes([60.0]).value == 1


def test_totals_fallback():
    assert metrics.active_minutes_from_total(0.0).value == 0
    assert metrics.active_minutes_from_total(99.0).value == 0
    assert metrics.active_minutes_from_total(100.0).value == 1
    assert metrics.active_minutes_from_total(10_000.0).value == 100
    assert metrics.effort_from_total(50.0).value == 0
    assert metrics.effort_from_total(6_900.0).value == 69


def test_derived_marker():
    assert Derived(7055.0).value == 7055.0
    assert Derived(3).value == 3
    assert str(Derived(7055.0)) == "~7055.0"
    assert str(Derived(3)) == "~3"
    assert metrics.effort_from_cadence(100.0).value == 1.0
    total = sum(metrics.effort_from_cadence(v).value for v in [0.0, 100.0, 131.0])
    assert total == 3.0


def test_ring_fraction_saturation():
    assert metrics.ring_fraction(0.0, 10_000.0) == 0.0
    assert metrics.ring_fraction(100.0, 10_000.0) == pytest.approx(0.01)
    assert metrics.ring_fraction(9_900.0, 10_000.0) == pytest.approx(0.99)
    assert metrics.ring_fraction(10_000.0, 10_000.0) == 1.0
    assert metrics.ring_fraction(34_000.0, 10_000.0) == 1.0
    assert metrics.ring_fraction(1_000_000.0, 1.0) == 1.0
    assert metrics.ring_fraction(-500.0, 10_000.0) == 0.0
    assert metrics.ring_fraction(1.0, 0.0) == 1.0
    assert metrics.ring_fraction(0.0, 0.0) == 0.0


def test_goal_met():
    assert metrics.goal_met(10_000.0, 10_000.0)
    assert metrics.goal_met(12_000.0, 10_000.0)
    assert not metrics.goal_met(9_999.0, 10_000.0)
    assert not metrics.goal_met(0.0, 10_000.0)


def test_streak_count():
    assert metrics.streak_count([]) == 0
    assert metrics.streak_count([True] * 7) == 7
    assert metrics.streak_count([True, False, True, True, True]) == 3
    assert metrics.streak_count([True, True, False]) == 0
    assert metrics.streak_count([True]) == 1
    assert metrics.streak_count([False]) == 0
    assert metrics.streak_count([True, True, False, True, True]) == 2


def test_weekly_progress():
    assert metrics.weekly_target_progress(90.0, 150.0) == pytest.approx(0.6)
    assert metrics.weekly_target_progress(0.0, 150.0) == 0.0
    assert metrics.weekly_target_progress(180.0, 150.0) == pytest.approx(1.2)
    assert metrics.weekly_target_progress(5.0, 0.0) == 1.0


def test_weight_trend():
    assert metrics.weight_trend_30d([72.0, 71.5, 70.5]) == pytest.approx(-1.5)
    assert metrics.weight_trend_30d([70.0, 71.0, 72.0]) == pytest.approx(2.0)
    assert metrics.weight_trend_30d([70.0, 70.0, 70.0]) == 0.0
    assert metrics.weight_trend_30d([70.0]) == 0.0
    assert metrics.weight_trend_30d([]) == 0.0


def test_goal_defaults():
    assert metrics.DEFAULT_STEPS == 10_000
    assert metrics.DEFAULT_EFFORT == 90
    assert metrics.DEFAULT_WEEKLY_EFFORT == 150
    assert metrics.DEFAULT_GOALS == metrics.Goals()
    assert metrics.override_goals() == metrics.DEFAULT_GOALS


def test_goal_override_isolation():
    assert metrics.override_goals(steps=12_000).steps == 12_000
    assert metrics.override_goals(steps=12_000).effort == metrics.DEFAULT_EFFORT
    assert metrics.override_goals(effort=120).effort == 120
    assert metrics.override_goals(weekly_effort=200).weekly_effort == 200
    full = metrics.override_goals(steps=8_000, effort=60, weekly_effort=180)
    assert (full.steps, full.effort, full.weekly_effort) == (8_000, 60, 180)


def test_goal_override_rejects_non_positive():
    with pytest.raises(ValueError):
        metrics.override_goals(steps=0)
    with pytest.raises(ValueError):
        metrics.override_goals(steps=-5)
    with pytest.raises(ValueError):
        metrics.override_goals(effort=0)
    with pytest.raises(ValueError):
        metrics.override_goals(weekly_effort=-10)


def test_goals_feed_ring():
    fraction = metrics.ring_fraction(
        metrics.DEFAULT_GOALS.steps * 0.75, float(metrics.DEFAULT_GOALS.steps)
    )
    assert fraction == pytest.approx(0.75)
    assert metrics.goal_met(float(metrics.DEFAULT_GOALS.steps), float(metrics.DEFAULT_GOALS.steps))


def test_unit_convert():
    assert metrics.kg_to_lb(0.0) == 0.0
    assert metrics.kg_to_lb(1.0) == pytest.approx(2.2046226218487757, rel=1e-4)
    assert metrics.kg_to_lb(70.0) == pytest.approx(154.3236, abs=0.001)
    assert metrics.lb_to_kg(154.3236) == pytest.approx(70.0, abs=0.001)
    assert metrics.lb_to_kg(metrics.kg_to_lb(72.5)) == pytest.approx(72.5, abs=0.0001)
    assert metrics.metres_to_miles(1609.344) == pytest.approx(1.0)
    assert metrics.metres_to_km(1000.0) == 1.0
    assert metrics.km_to_metres(1.0) == 1000.0
    assert metrics.miles_to_metres(metrics.metres_to_miles(12_345.0)) == pytest.approx(12_345.0)
    assert metrics.km_to_metres(metrics.metres_to_km(8_888.0)) == pytest.approx(8_888.0)
