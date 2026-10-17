"""Phone normalization and the in-memory caller-ID lookup index.

Pure module: no GTK, no D-Bus, safe headless and in tests.

Normalization: ``digits_of`` strips everything but digits. ``to_e164`` is a
best-effort E.164 composer: leading ``+`` (or ``00`` IDD prefix) is taken as
an explicit country code, otherwise the configured default region supplies
its country calling code for national numbers written in the region's usual
shapes (currently the NANP 10-digit / 11-digit-leading-1 forms for region
US/CA; other regions accept an explicit ``+``/``00`` form only).

Lookup index: digits-suffix match. A query must carry at least 7 digits to
match anything. Ambiguity resolves exact E.164 first, then the longest
matching digit suffix; remaining ties break deterministically by
(uid, digits) so repeated lookups agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MIN_MATCH_DIGITS = 7

COUNTRY_CALLING_CODES: dict[str, str] = {
    "US": "1",
    "CA": "1",
    "GB": "44",
    "DE": "49",
    "FR": "33",
    "ES": "34",
    "IT": "39",
    "IN": "91",
    "AU": "61",
    "BR": "55",
    "MX": "52",
}

NANP_REGIONS = frozenset({"US", "CA"})


def digits_of(raw: str | None) -> str:
    """Return only the decimal digits of a raw number string."""
    if not raw:
        return ""
    return re.sub(r"[^\d]", "", raw)


def to_e164(raw: str | None, default_region: str = "US") -> str | None:
    """Best-effort E.164 for a raw number, or None when it cannot be told.

    Explicit international forms (``+`` prefix or ``00`` IDD prefix) map
    directly. Otherwise the default region's calling code is applied to
    national NANP shapes (US/CA); national numbers from other regions that
    need a trunk-prefix rule this module does not know return None.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    region = (default_region or "US").upper()
    if text.startswith("+"):
        digits = digits_of(text)
        if 7 <= len(digits) <= 15:
            return "+" + digits
        return None
    digits = digits_of(text)
    if text.startswith("00"):
        rest = digits[2:] if digits.startswith("00") else digits
        if 7 <= len(rest) <= 15:
            return "+" + rest
        return None
    if region in NANP_REGIONS:
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
        if digits.startswith("1") and 7 <= len(digits) <= 15:
            return "+" + digits
    cc = COUNTRY_CALLING_CODES.get(region)
    if cc and digits.startswith(cc) and 7 <= len(digits) <= 15:
        return "+" + digits
    return None


def normalize(raw: str | None, default_region: str = "US") -> tuple[str, str | None]:
    """Return ``(digits, e164_or_None)`` for a raw number string."""
    digits = digits_of(raw)
    return digits, to_e164(raw, default_region)


@dataclass
class IndexedNumber:
    uid: str
    display_name: str
    tags: tuple[str, ...] = ()
    digits: str = ""
    e164: str | None = None


@dataclass
class LookupResult:
    uid: str
    display_name: str
    tags: tuple[str, ...] = ()


NumberTriple = tuple[str, str | None, str]

IndexRow = tuple[str, str, list[NumberTriple], tuple[str, ...] | list[str]]

IndexRows = list[IndexRow]


class LookupIndex:
    """In-memory phone lookup rebuilt incrementally on writes."""

    def __init__(self, default_region: str = "US") -> None:
        self._default_region = (default_region or "US").upper()
        self._by_uid: dict[str, list[IndexedNumber]] = {}
        self._by_e164: dict[str, list[IndexedNumber]] = {}
        self._by_digits: dict[str, list[IndexedNumber]] = {}
        self._suffixes: dict[str, list[IndexedNumber]] = {}

    @property
    def default_region(self) -> str:
        return self._default_region

    def _track(self, entry: IndexedNumber) -> None:
        if entry.e164:
            self._by_e164.setdefault(entry.e164, []).append(entry)
        if entry.digits:
            self._by_digits.setdefault(entry.digits, []).append(entry)
            for width in range(MIN_MATCH_DIGITS, len(entry.digits) + 1):
                self._suffixes.setdefault(entry.digits[-width:], []).append(entry)

    def _untrack(self, entry: IndexedNumber) -> None:
        for table, key in ((self._by_e164, entry.e164),
                           (self._by_digits, entry.digits)):
            if not key:
                continue
            kept = [e for e in table.get(key, []) if e.uid != entry.uid]
            if kept:
                table[key] = kept
            else:
                table.pop(key, None)
        if entry.digits:
            for width in range(MIN_MATCH_DIGITS, len(entry.digits) + 1):
                suffix = entry.digits[-width:]
                kept = [e for e in self._suffixes.get(suffix, [])
                        if e.uid != entry.uid]
                if kept:
                    self._suffixes[suffix] = kept
                else:
                    self._suffixes.pop(suffix, None)

    def upsert(self, uid: str, display_name: str,
               numbers: list[tuple[str, str | None, str]],
               tags: tuple[str, ...] | list[str] = ()) -> None:
        """Insert or replace every indexed number for one contact.

        ``numbers`` holds ``(raw, e164, digits)`` triples as stored; empty
        digit strings are skipped. Replaces the contact's previous entries.
        """
        self.remove(uid)
        entries: list[IndexedNumber] = []
        for _raw, e164, digits in numbers:
            if not digits:
                continue
            entry = IndexedNumber(uid=uid, display_name=display_name,
                                  tags=tuple(tags), digits=digits, e164=e164)
            entries.append(entry)
            self._track(entry)
        self._by_uid[uid] = entries

    def remove(self, uid: str) -> None:
        for entry in self._by_uid.pop(uid, []):
            self._untrack(entry)

    def rebuild(self, rows: IndexRows) -> None:
        """Replace the whole index from ``(uid, name, numbers, tags)`` rows."""
        self._by_uid.clear()
        self._by_e164.clear()
        self._by_digits.clear()
        self._suffixes.clear()
        for uid, display_name, numbers, tags in rows:
            entries: list[IndexedNumber] = []
            for _raw, e164, digits in numbers:
                if not digits:
                    continue
                entry = IndexedNumber(uid=uid, display_name=display_name,
                                      tags=tuple(tags), digits=digits,
                                      e164=e164)
                entries.append(entry)
                self._track(entry)
            self._by_uid[uid] = entries

    def lookup(self, number: str | None) -> LookupResult | None:
        query_digits = digits_of(number)
        if len(query_digits) < MIN_MATCH_DIGITS:
            return None
        query_e164 = to_e164(number, self._default_region)
        if query_e164:
            exact = self._by_e164.get(query_e164)
            if exact:
                winner = sorted(exact, key=lambda e: (e.uid, e.digits))[0]
                return LookupResult(winner.uid, winner.display_name,
                                    winner.tags)
        limit = min(len(query_digits), 15)
        for width in range(limit, MIN_MATCH_DIGITS - 1, -1):
            candidates = self._suffixes.get(query_digits[-width:])
            if candidates:
                winner = sorted(candidates,
                                key=lambda e: (e.uid, e.digits))[0]
                return LookupResult(winner.uid, winner.display_name,
                                    winner.tags)
        return None

    @property
    def contact_count(self) -> int:
        return len(self._by_uid)
