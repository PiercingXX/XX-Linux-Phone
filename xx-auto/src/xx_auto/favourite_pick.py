"""Primary-number pick for starred contacts (port of FavouritePick)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class NumberType(Enum):
    MOBILE = "mobile"
    HOME = "home"
    WORK = "work"
    OTHER = "other"


@dataclass(frozen=True)
class Number:
    value: str
    type: NumberType = NumberType.OTHER


def primary_number(numbers: list[Number]) -> Number | None:
    """Mobile first, else the first number, else None."""
    if not numbers:
        return None
    for number in numbers:
        if number.type is NumberType.MOBILE:
            return number
    return numbers[0]


def classify_label(label: str) -> NumberType:
    """Map a vCard TEL type token to a NumberType (CELL=MOBILE)."""
    token = (label or "").strip().upper()
    if token in ("CELL", "MOBILE", "CELLULAR"):
        return NumberType.MOBILE
    if token == "HOME":
        return NumberType.HOME
    if token == "WORK":
        return NumberType.WORK
    return NumberType.OTHER
