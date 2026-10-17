"""L6 contact picker model without GTK: kinds, rows, value extraction.

Pure module so pytest runs headless. The GTK page (``picker_ui``)
renders these rows over the shared store snapshot; the D-Bus service
(``service``) validates the requested kind and holds the ``Pick``
invocation until the page reports a pick or a cancel.

Kinds mirror the Android picker contract (``PickContract``: contact,
phone, email): ``contact`` picks a person, ``phone``/``email`` pick one
concrete number/address, so a contact with three numbers contributes
three rows in phone mode and none in email mode when it has no address.
"""

from __future__ import annotations

from dataclasses import dataclass

import xx_phone

from xx_contacts.detail import detail_title

#: D-Bus ``Pick`` kinds, in introspection order.
PICK_KINDS = ("phone", "email", "contact")


@dataclass(frozen=True)
class PickRow:
    """One tappable picker row: contact identity plus the picked value."""

    uid: str = ""
    title: str = ""
    detail: str = ""
    value: str = ""


def normalize_kind(kind: str | None) -> str:
    """Lowercased valid kind, or raise ``ValueError`` for anything else."""
    cleaned = (kind or "").strip().lower()
    if cleaned in PICK_KINDS:
        return cleaned
    raise ValueError(
        f"unknown pick kind: {kind!r} (expected one of "
        f"{', '.join(PICK_KINDS)})")


def primary_phone(record) -> str:
    """Primary-first phone number raw text, or ``""`` when there is none."""
    phones = list(getattr(record, "phones", None) or ())
    for entry in phones:
        if getattr(entry, "is_primary", False) and entry.number_raw:
            return entry.number_raw
    for entry in phones:
        if entry.number_raw:
            return entry.number_raw
    return ""


def primary_email(record) -> str:
    """Primary-first email address, or ``""`` when there is none."""
    emails = list(getattr(record, "emails", None) or ())
    for entry in emails:
        if getattr(entry, "is_primary", False) and (entry.address or ""):
            return entry.address
    for entry in emails:
        if (entry.address or "").strip():
            return entry.address
    return ""


def pick_value(kind: str, record) -> tuple[str, str]:
    """``(uid, value)`` a confirmed pick reports for ``record``.

    Contact picks carry the display name so callers can show who was
    picked without a follow-up ``Get``; phone/email picks carry the raw
    number/address. Missing data yields ``""``, never an exception.
    """
    normalized = normalize_kind(kind)
    uid = getattr(record, "uid", "") or ""
    if normalized == "phone":
        return uid, primary_phone(record)
    if normalized == "email":
        return uid, primary_email(record)
    return uid, detail_title(record)


def build_pick_rows(kind: str, records, country: str | None = None
                   ) -> list[PickRow]:
    """Flatten ``records`` into tappable rows for ``kind``.

    Contact mode keeps one row per contact; phone/email modes keep one
    row per number/address, so choosing is a single tap and contacts
    without that datum simply do not appear. Titles and details read
    through the shared display rule; ``value`` keeps the raw picked
    number/address so dialing and storage never see formatting.
    """
    normalized = normalize_kind(kind)
    shown_in = (country if country is not None
                else xx_phone.display_country())
    rows: list[PickRow] = []
    for record in records or ():
        uid = getattr(record, "uid", "") or ""
        if not uid:
            continue
        name = detail_title(record)
        title = xx_phone.display(name, shown_in)
        if normalized == "contact":
            detail = primary_phone(record) or primary_email(record)
            rows.append(PickRow(uid, title,
                                xx_phone.display(detail, shown_in), name))
            continue
        if normalized == "phone":
            for entry in getattr(record, "phones", None) or ():
                raw = entry.number_raw or ""
                if raw.strip():
                    rows.append(PickRow(
                        uid, title,
                        xx_phone.display(raw.strip(), shown_in),
                        raw.strip()))
            continue
        for entry in getattr(record, "emails", None) or ():
            address = (entry.address or "").strip()
            if address:
                rows.append(PickRow(uid, title, address, address))
    rows.sort(key=lambda row: (row.title.casefold(), row.detail, row.uid))
    return rows


def filter_pick_rows(rows: list[PickRow], query: str) -> list[PickRow]:
    """Substring filter over row titles and details (case-insensitive)."""
    folded = (query or "").casefold().strip()
    if not folded:
        return list(rows or ())
    return [row for row in rows or ()
            if folded in row.title.casefold()
            or folded in row.detail.casefold()]
