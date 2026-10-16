"""Profile field validation. Port of ui/profile/ProfileEditModel.kt.

Height is typed in display units but stored in metres: metric input is
metres, imperial accepts feet-and-inches or plain total inches. Goals are
positive whole numbers; birth year is a four-digit plausible year. Rejects
return None, never raise.
"""

from __future__ import annotations

import re

MAX_STEPS_GOAL = 100_000
MAX_EFFORT_GOAL = 10_000
MAX_WEEKLY_EFFORT_GOAL = 100_000

MIN_HEIGHT_METRES = 0.5
MAX_HEIGHT_METRES = 2.72
MIN_BIRTH_YEAR = 1900

_INCHES_TO_METRES = 0.0254

_FEET_INCHES = re.compile(r"(\d+)\s*(?:'|ft)\s*(\d*(?:\.\d+)?)?\s*(?:in|\"|″)?")


def parse_steps_goal(raw: str) -> int | None:
    return _parse_bounded_int(raw, MAX_STEPS_GOAL)


def parse_effort_goal(raw: str) -> int | None:
    return _parse_bounded_int(raw, MAX_EFFORT_GOAL)


def parse_weekly_effort_goal(raw: str) -> int | None:
    return _parse_bounded_int(raw, MAX_WEEKLY_EFFORT_GOAL)


def parse_height_metres(raw: str, units: str) -> float | None:
    text = raw.strip()
    if not text:
        return None
    if units == "metric":
        try:
            metres = float(text)
        except ValueError:
            return None
    else:
        metres = _parse_imperial_metres(text)
        if metres is None:
            return None
    if metres != metres:
        return None
    if MIN_HEIGHT_METRES <= metres <= MAX_HEIGHT_METRES:
        return metres
    return None


def height_input_text(metres: float, units: str) -> str:
    if metres == 0.0:
        return ""
    if units == "metric":
        return f"{metres:.2f}"
    total_inches = metres / _INCHES_TO_METRES
    feet = int(total_inches / 12.0)
    inches = int(round(total_inches - feet * 12.0))
    if inches == 12:
        return f"{feet + 1}'0"
    return f"{feet}'{inches}"


def parse_birth_year(raw: str, current_year: int) -> int | None:
    text = raw.strip()
    if len(text) != 4 or not text.isdigit():
        return None
    try:
        year = int(text)
    except ValueError:
        return None
    if MIN_BIRTH_YEAR <= year <= current_year:
        return year
    return None


def _parse_bounded_int(raw: str, ceiling: int) -> int | None:
    text = raw.strip()
    if not text or not text.isdigit():
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    if 1 <= value <= ceiling:
        return value
    return None


def _parse_imperial_metres(text: str) -> float | None:
    match = _FEET_INCHES.fullmatch(text)
    if match is not None:
        try:
            feet = int(match.group(1))
        except (ValueError, TypeError):
            return None
        inches_raw = match.group(2) or ""
        try:
            inches = float(inches_raw) if inches_raw else 0.0
        except ValueError:
            return None
        return (feet * 12.0 + inches) * _INCHES_TO_METRES
    try:
        return float(text) * _INCHES_TO_METRES
    except ValueError:
        return None
