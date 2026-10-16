"""Sender-address normalisation and matching.

Complete port of ``core/PhoneNumbers.kt``. Matching is digit-normalised so
one sender written several ways (" +15551234567 ", "555-123-4567",
"(555) 123 4567", "+1 555 1234567") compares as one sender: a blocked
sender cannot evade the blocklist through formatting variance, and a
starred contact keeps their bypass whatever format the carrier delivers.

Email-gateway addresses (``foo@carrier.example.com``) are deliberately not
digit-stripped: their identity lives in punctuation and letters, so they
compare by exact trimmed-lowercase equality only.

A normalized MATCH key and the ADDRESS you dial are different things.
:func:`conversation_key` is digits-only for matching; :func:`canonical_dialable`
is the E.164 address ModemManager must dial (never send to a key).
:func:`format_display_number` mirrors xx-contacts ``phones.format_display_number``
for UI surfaces only.
"""

from __future__ import annotations

import re

#: Minimum digits the SHORTER side must carry for a suffix match to count.
#: Short codes (5-6 digits) and emergency numbers stay below it, so a stored
#: "911" never suffix-matches an unrelated longer number.
MIN_SUFFIX_DIGITS = 7

#: Country calling codes known for display/E.164 derivation (mirrors
#: xx-contacts ``phones.COUNTRY_CALLING_CODES``).
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

#: Regions whose national shapes this module can compose without a "+".
NANP_REGIONS = frozenset({"US", "CA"})


def normalize(address: str) -> str:
    """Normalise ``address`` for comparison.

    Trimmed and lowercased; an address containing ``@`` (email gateway) is
    returned verbatim, otherwise every non-digit character is stripped.
    Alphanumeric senders (``VERIFY``) strip to empty here; use
    :func:`conversation_key` for their thread identity.
    """
    trimmed = (address or "").strip().lower()
    if "@" in trimmed:
        return trimmed
    return "".join(ch for ch in trimmed if ch.isdigit())


def conversation_key(address: str) -> str:
    """Stable identity used as the 1:1 conversation key.

    Email: trimmed lowercase. Phone number: digits only. Alphanumeric
    sender (letters, no digits, no ``@``): the trimmed-lowercase token, so
    ``VERIFY`` and ``verify`` are one thread. Anything else: empty.
    """
    trimmed = (address or "").strip().lower()
    if not trimmed:
        return ""
    if "@" in trimmed:
        return trimmed
    digits = "".join(ch for ch in trimmed if ch.isdigit())
    if digits:
        return digits
    if any(ch.isalpha() for ch in trimmed):
        return trimmed
    return ""


def matches(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` refer to the same sender.

    Emails compare by exact normalised equality (and never match a bare
    number). Phone numbers compare by digit equality with suffix tolerance
    for a country code on one side, gated by :data:`MIN_SUFFIX_DIGITS`.
    Alphanumeric senders compare by exact key equality. An address with no
    identity never matches anything.
    """
    ka = conversation_key(a)
    kb = conversation_key(b)
    if not ka or not kb:
        return False

    a_is_email = "@" in ka
    b_is_email = "@" in kb
    if a_is_email or b_is_email:
        return a_is_email and b_is_email and ka == kb

    da = "".join(ch for ch in ka if ch.isdigit())
    db = "".join(ch for ch in kb if ch.isdigit())
    if not da and not db:
        return ka == kb
    if not da or not db:
        return False
    if da == db:
        return True
    shorter, longer = (da, db) if len(da) <= len(db) else (db, da)
    if len(shorter) < MIN_SUFFIX_DIGITS:
        return False
    return longer.endswith(shorter)


def digits_of(raw: str | None) -> str:
    """Only the decimal digits of a raw number string."""
    if not raw:
        return ""
    return re.sub(r"[^\d]", "", raw)


def to_e164(raw: str | None, default_region: str = "US") -> str | None:
    """Best-effort E.164 for a raw number, or None when it cannot be told.

    Mirrors xx-contacts ``phones.to_e164``: an explicit ``+`` or ``00``
    international prefix maps directly, otherwise the default region's
    calling code is applied to national NANP shapes (US/CA); national
    numbers from other regions that need a trunk-prefix rule this module
    does not know return None.
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


def canonical_dialable(address: str, default_region: str = "US") -> str:
    """Canonical address ModemManager must dial for ``address``.

    E.164 with "+" when the raw address had it or the region rules make it
    unambiguous (via :func:`to_e164`); otherwise the raw address trimmed.
    Email gateways and alphanumeric senders have no E.164 form and return
    trimmed input. Empty input returns "".
    """
    trimmed = (address or "").strip()
    if not trimmed:
        return ""
    if "@" in trimmed:
        return trimmed
    e164 = to_e164(trimmed, default_region)
    if e164 is not None:
        return e164
    return trimmed


def should_replace_dialable(stored: str, candidate: str) -> bool:
    """Whether ``candidate`` is a better dialable than ``stored``.

    Empty stored always loses; an E.164 candidate beats a non-E.164 stored
    value; an E.164 stored value is never downgraded. Two distinct E.164
    values keep the stored one so a thread never churns mid-conversation.
    """
    current = (stored or "").strip()
    incoming = (candidate or "").strip()
    if not incoming:
        return False
    if not current:
        return True
    if current == incoming:
        return False
    if incoming.startswith("+") and not current.startswith("+"):
        return True
    return False


def _group_national(national: str) -> str:
    """Space-group a national digit string for reading (3-3-rest)."""
    chunks: list[str] = []
    rest = national
    while len(rest) > 4:
        chunks.append(rest[:3])
        rest = rest[3:]
    chunks.append(rest)
    return " ".join(chunks)


def configured_region(settings=None) -> str:
    """Configured default region (upper-cased), ``US`` when unknown.

    Mirrors xx-contacts ``AppConfig.default_region``: the region lives in
    the txxt JSON config (``default_region``) and every normalization
    entry point accepts it explicitly. This helper resolves it from a
    loaded :class:`Settings`, a plain region string, or the config file
    when nothing was passed, so call sites that hold no settings object
    still dial like the region expects instead of assuming NANP.
    """
    if isinstance(settings, str):
        text = settings.strip().upper()
        return text or "US"
    region = getattr(settings, "default_region", None)
    if isinstance(region, str) and region.strip():
        return region.strip().upper()
    try:
        from txxt.settings import load as _load
    except Exception:
        return "US"
    try:
        loaded = _load()
    except Exception:
        return "US"
    raw = getattr(loaded, "default_region", "US")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().upper()
    return "US"


def canonical_dialable_configured(address: str, settings=None) -> str:
    """Region-aware :func:`canonical_dialable` using the configured region."""
    return canonical_dialable(address, configured_region(settings))


def format_display_configured(raw: str | None, settings=None) -> str:
    """Region-aware :func:`format_display_number` for UI surfaces."""
    return format_display_number(raw, configured_region(settings))


def format_display_number(raw: str | None, default_region: str = "US") -> str:
    """Readable spacing for a stored number; dialing keeps raw E.164.

    Mirrors xx-contacts ``phones.format_display_number``: NANP numbers
    render as ``+1 555 010 0200``; other regions render the country code
    plus space-grouped national digits (``+44 770 090 0123``). Anything
    without a derivable E.164 comes back stripped but unchanged, so short
    codes and unparseable input never vanish from the UI.
    """
    if not raw or not raw.strip():
        return ""
    e164 = to_e164(raw, default_region)
    if e164 is None:
        return raw.strip()
    digits = e164[1:]
    region = (default_region or "US").upper()
    if region in NANP_REGIONS and digits.startswith("1") and len(digits) == 11:
        national = digits[1:]
        return f"+1 {national[:3]} {national[3:6]} {national[6:]}"
    for code in sorted({*COUNTRY_CALLING_CODES.values(), "1"},
                       key=len, reverse=True):
        if digits.startswith(code) and len(digits) > len(code):
            return f"+{code} {_group_national(digits[len(code):])}"
    return f"+{_group_national(digits)}"
