"""Pattern mask builder: pure port of ui/MaskBuilder.kt, stdlib only.

Typed digits in, (prefix, wildcards) out — the exact contract
core PatternRule enforces (prefix excludes '+', digits only,
length-exact matching). The neighbor-spoof preset derives from the
full normalized E.164 digit string with the country code kept, because
length-exact matching runs on exactly those digits.
"""
from __future__ import annotations

from dataclasses import dataclass

WILDCARD = "X"


@dataclass(frozen=True)
class Draft:
    prefix: str
    wildcards: int

    @property
    def total_length(self) -> int:
        return len(self.prefix) + self.wildcards


def digits_of(typed: str) -> str:
    """Digit skeleton of whatever the user typed."""
    return "".join(ch for ch in typed if ch.isdigit())


def _is_ascii_digits(text: str) -> bool:
    return bool(text) and all("0" <= ch <= "9" for ch in text)


def build(digits: str, masked_tail: int) -> Draft | None:
    """Mask masked_tail trailing digits into X's; needs a prefix digit."""
    if masked_tail < 0 or masked_tail >= len(digits):
        return None
    prefix = digits[:len(digits) - masked_tail] if masked_tail else digits
    if not _is_ascii_digits(prefix):
        return None
    return Draft(prefix, masked_tail)


def preview(draft: Draft) -> str:
    """Call Control-style preview: NANP shapes group, rest prints +digits."""
    full = draft.prefix + WILDCARD * draft.wildcards
    if len(full) == 11 and full.startswith("1"):
        return "+1 %s-%s-%s" % (full[1:4], full[4:7], full[7:])
    if len(full) == 10:
        return "%s-%s-%s" % (full[0:3], full[3:6], full[6:])
    return "+" + full


def neighbor_draft(e164_digits: str) -> Draft | None:
    """Own six-digit prefix over the full E.164 digits; silence, never block."""
    if not _is_ascii_digits(e164_digits):
        return None
    if len(e164_digits) < 7:
        return None
    return build(e164_digits, len(e164_digits) - 6)
