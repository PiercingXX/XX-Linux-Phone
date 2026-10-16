"""Keypad T9 match-as-you-type: pure port of ui/T9.kt, stdlib only.

A query of keypad digits matches a contact either by digit run inside
its number or through the letters of its display name mapped onto the
same digits. Name matches are prefix-only; a number hit wins when both
could match. Monogram initials ride along because they are the only
avatar the People surfaces ever draw.
"""
from __future__ import annotations

from dataclasses import dataclass

_LETTER_TO_DIGIT = {
    **dict.fromkeys("abc", "2"),
    **dict.fromkeys("def", "3"),
    **dict.fromkeys("ghi", "4"),
    **dict.fromkeys("jkl", "5"),
    **dict.fromkeys("mno", "6"),
    **dict.fromkeys("pqrs", "7"),
    **dict.fromkeys("tuv", "8"),
    **dict.fromkeys("wxyz", "9"),
}


def encode(text: str) -> str:
    """Map letters onto keypad digits; digits pass through, rest drops."""
    out = []
    for ch in text.lower():
        if "0" <= ch <= "9":
            out.append(ch)
        else:
            digit = _LETTER_TO_DIGIT.get(ch)
            if digit is not None:
                out.append(digit)
    return "".join(out)


def digits_of(number: str) -> str:
    """Digit skeleton of a number; formatting drops out."""
    return "".join(ch for ch in number if "0" <= ch <= "9")


@dataclass(frozen=True)
class InNumber:
    digit_start: int
    length: int


@dataclass(frozen=True)
class InName:
    prefix_len: int


def hit(query: str, number: str, display_name: str) -> InNumber | InName | None:
    """Match one contact against the query; number hits beat name hits."""
    encoded = encode(query)
    if not encoded:
        return None
    digits = digits_of(number)
    index = digits.find(encoded)
    if index >= 0:
        return InNumber(index, len(encoded))
    if encode(display_name).startswith(encoded):
        return InName(len(encoded))
    return None


def char_range(raw_number: str, found: InNumber) -> tuple[int, int] | None:
    """Map an InNumber hit back onto the raw formatted string.

    Returns the inclusive char range covering the matched digits, or
    None when the raw text no longer holds that many digits there.
    """
    seen = 0
    start = -1
    end = -1
    for i, ch in enumerate(raw_number):
        if not "0" <= ch <= "9":
            continue
        if seen == found.digit_start:
            start = i
        if found.digit_start <= seen < found.digit_start + found.length:
            end = i
        seen += 1
    if start >= 0 and end >= start \
            and seen - found.digit_start >= found.length:
        return (start, end)
    return None


def initials(display_name: str | None) -> str:
    """Deterministic monogram: two leading initials, blank degrades to #."""
    words = (display_name or "").split()
    letters = [w for w in words if w[0].isalpha()]
    if not letters:
        return "#"
    if len(letters) == 1:
        return letters[0][:2].upper()
    return (letters[0][0] + letters[1][0]).upper()


def search(entries: list[tuple[str, str, str]], query: str,
           limit: int = 10) -> list[tuple[str, str, str, InNumber | InName]]:
    """Match-as-you-type over (uid, number, display_name) rows.

    Number hits list before name hits, input order kept inside each
    group; empty or digitless queries never hit. Truncated to limit.
    """
    if limit <= 0:
        return []
    if not encode(query):
        return []
    number_hits: list[tuple[str, str, str, InNumber | InName]] = []
    name_hits: list[tuple[str, str, str, InNumber | InName]] = []
    for uid, number, display_name in entries:
        found = hit(query, number, display_name)
        if isinstance(found, InNumber):
            number_hits.append((uid, number, display_name, found))
        elif isinstance(found, InName):
            name_hits.append((uid, number, display_name, found))
    return (number_hits + name_hits)[:limit]
