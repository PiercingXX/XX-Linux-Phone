"""Display-unit conversions. Pure port of core/UnitConvert.kt.

Storage is always kilograms and metres; conversion happens at the view
layer only.
"""

from __future__ import annotations

KG_PER_LB = 0.45359237
METRES_PER_MILE = 1609.344
METRES_PER_KM = 1000.0


def kg_to_lb(kg: float) -> float:
    return kg / KG_PER_LB


def lb_to_kg(lb: float) -> float:
    return lb * KG_PER_LB


def metres_to_miles(metres: float) -> float:
    return metres / METRES_PER_MILE


def miles_to_metres(miles: float) -> float:
    return miles * METRES_PER_MILE


def metres_to_km(metres: float) -> float:
    return metres / METRES_PER_KM


def km_to_metres(km: float) -> float:
    return km * METRES_PER_KM


def format_weight(weight_kg: float, units: str) -> str:
    """Render a stored kg value in the user's display units."""
    if units == "imperial":
        return f"{kg_to_lb(weight_kg):.1f} lb"
    return f"{weight_kg:.1f} kg"


def format_distance(metres: float, units: str) -> str:
    """Render a stored metres value in the user's display units."""
    if units == "imperial":
        return f"{metres_to_miles(metres):.2f} mi"
    return f"{metres_to_km(metres):.2f} km"
