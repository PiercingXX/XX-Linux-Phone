"""View-layer unit formatting. Port of profile/UnitFormatter.kt.

Storage is always kg/metres/km; this module renders display strings only.
"""

from __future__ import annotations

from xx_vitals.core import metrics

METRE_TO_INCHES = 39.37007874
KM_TO_MILES = 0.6213711922


def _one_decimal(value: float) -> str:
    return f"{value:.1f}"


def format_weight(kilograms: float, units: str) -> str:
    if kilograms < 0.0:
        raise ValueError(f"weight cannot be negative, was {kilograms}")
    if units == "metric":
        return f"{_one_decimal(kilograms)} kg"
    return f"{_one_decimal(metrics.kg_to_lb(kilograms))} lb"


def format_height(metres: float, units: str) -> str:
    if metres < 0.0:
        raise ValueError(f"height cannot be negative, was {metres}")
    if units == "metric":
        return f"{metres:.2f} m"
    total_inches = metres * METRE_TO_INCHES
    feet = int(total_inches / 12.0)
    inches = int(round(total_inches - feet * 12.0))
    if inches == 12:
        return f"{feet + 1}\u2032 0\u2033"
    return f"{feet}\u2032 {inches}\u2033"


def format_distance_km(kilometres: float, units: str) -> str:
    if kilometres < 0.0:
        raise ValueError(f"distance cannot be negative, was {kilometres}")
    if units == "metric":
        return f"{_one_decimal(kilometres)} km"
    return f"{_one_decimal(kilometres * KM_TO_MILES)} mi"
