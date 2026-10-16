"""Port of UnitFormatterTest: stored kg/m/km render in display units."""

import pytest

from xx_vitals.core.format import format_distance_km, format_height, format_weight


def test_metric_weight_passthrough():
    assert format_weight(83.5, "metric") == "83.5 kg"
    assert format_weight(0.0, "metric") == "0.0 kg"


def test_imperial_weight_converts():
    assert format_weight(83.5, "imperial") == "184.1 lb"
    assert format_weight(83.55, "imperial") == "184.2 lb"


def test_height_formats():
    assert format_height(1.78, "metric") == "1.78 m"
    assert format_height(1.80, "imperial") == "5\u2032 11\u2033"
    assert format_height(1.83, "imperial") == "6\u2032 0\u2033"


def test_distance_formats():
    assert format_distance_km(9.0, "metric") == "9.0 km"
    assert format_distance_km(9.0, "imperial") == "5.6 mi"


def test_negative_rejected():
    with pytest.raises(ValueError):
        format_weight(-1.0, "metric")
    with pytest.raises(ValueError):
        format_height(-0.5, "imperial")
    with pytest.raises(ValueError):
        format_distance_km(-2.0, "metric")
