"""L3 detail model without GTK: sections over a store ``ContactRecord``.

Pure module so pytest runs headless. The GTK page (``detail_ui``) renders
these sections over an ``Adw.NavigationView`` push. Display strings use
the shared ``xx_phone.display`` rule (national form at home, otherwise
international); outbound URIs always use the raw compact number through
``list_model`` so dialing stays on E.164.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote_plus

import xx_phone

from xx_contacts.list_model import call_uri, email_uri, message_uri

#: Read-only call history stays hidden until xx-dialer exposes it over
#: D-Bus; the UI renders the history group only when this flips True
#: *and* :func:`get_call_history` returns rows.
CALL_HISTORY_AVAILABLE = False


def get_call_history(uid: str) -> list:
    """Seam for xx-dialer call history: no source yet, so always empty.

    When the dialer lane exposes per-contact history over D-Bus, fill this
    in (uid in, newest-first ``(at, direction, duration)`` rows out) and
    set :data:`CALL_HISTORY_AVAILABLE`; the detail page needs no change.
    """
    _ = uid
    return []


@dataclass(frozen=True)
class DetailAction:
    kind: str = ""
    uri: str = ""


@dataclass(frozen=True)
class DetailField:
    label: str = ""
    display: str = ""
    actions: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class DetailSection:
    key: str
    title: str
    fields: tuple = ()


def detail_title(record) -> str:
    """Header name: display name, else first number/email, else Unnamed."""
    if record.display_name:
        return record.display_name
    if record.phones:
        return record.phones[0].number_raw
    if record.emails:
        return record.emails[0].address
    return "Unnamed"


#: Human labels for vCard TYPE tokens. Raw tokens like ``CELL`` read as
#: jargon on a phone, so the detail page maps them; ``PREF``/``INTERNET``
#: are flags/defaults, not types, and fall through to the generic label.
TYPE_LABELS = {
    "CELL": "Mobile",
    "HOME": "Home",
    "WORK": "Work",
    "VOICE": "Phone",
    "FAX": "Fax",
    "PAGER": "Pager",
    "MAIN": "Main",
    "IPHONE": "iPhone",
    "OTHER": "Other",
}

#: Priority scan order: location/device tokens win over the generic
#: ``VOICE`` flag, so ``HOME,VOICE`` reads as Home, not Phone.
_TYPE_PRIORITY = ("CELL", "IPHONE", "MAIN", "HOME", "WORK", "FAX",
                  "PAGER", "VOICE", "OTHER")

_IGNORED_TOKENS = frozenset({"PREF", "INTERNET"})


def human_type_label(type_value: str = "", label: str = "",
                     fallback: str = "Other") -> str:
    """Map a stored ``type``/``label`` pair to a display label.

    A custom ``label`` (``LABEL`` param / ``X-ABLABEL``) or ``X-`` type
    token wins, title-cased; known TYPE tokens map through
    :data:`TYPE_LABELS`; other unknown tokens read as Other; flags like
    ``PREF`` or a blank type fall back to ``fallback``.
    """
    custom = (label or "").strip()
    if custom:
        if custom.upper().startswith("X-") and custom[2:].strip():
            custom = custom[2:]
        return custom.replace("_", " ").replace("-", " ").strip().title()
    tokens = [tok.strip().upper()
              for tok in (type_value or "").replace(";", ",").split(",")]
    tokens = [tok for tok in tokens if tok]
    for tok in tokens:
        if tok.startswith("X-") and tok[2:].strip():
            custom_name = tok[2:].replace("_", " ").replace("-", " ")
            return custom_name.strip().title() or fallback
    for key in _TYPE_PRIORITY:
        if key in tokens:
            return TYPE_LABELS[key]
    for tok in tokens:
        if tok not in _IGNORED_TOKENS:
            return "Other"
    return fallback


def _phone_label(entry, index: int) -> str:
    fallback = "Primary" if entry.is_primary else (
        f"Number {index + 1}" if index else "Phone")
    return human_type_label(entry.type, entry.label, fallback)


def _email_label(entry, index: int) -> str:
    fallback = "Primary" if entry.is_primary else (
        f"Email {index + 1}" if index else "Email")
    return human_type_label(entry.type, entry.label, fallback)


def format_address(entry) -> str:
    """Multiline display for a structured address entry."""
    lines: list[str] = []
    if entry.po_box:
        lines.append(f"PO Box {entry.po_box}")
    if entry.extended:
        lines.append(entry.extended)
    if entry.street:
        lines.append(entry.street)
    city = " ".join(part for part in
                   (entry.locality, entry.region, entry.postal_code) if part)
    if city:
        lines.append(city)
    if entry.country:
        lines.append(entry.country)
    return "\n".join(lines)


def maps_uri(display: str) -> str:
    """``geo:`` URI for an address display string, or ``""`` when blank."""
    query = ", ".join(line for line in display.splitlines() if line.strip())
    if not query:
        return ""
    return f"geo:0,0?q={quote_plus(query)}"


def build_detail_sections(record, default_region: str = "US"
                          ) -> list[DetailSection]:
    """Project a store record onto renderable detail sections.

    Sections with no content are omitted (same hide-when-empty rule as
    the Android detail screen); field order follows the stored order.
    """
    sections: list[DetailSection] = []

    phone_fields: list[DetailField] = []
    for index, entry in enumerate(record.phones):
        raw = entry.number_raw or ""
        if not raw.strip():
            continue
        display = xx_phone.display(
            raw, xx_phone.display_country(default_region)) or raw.strip()
        actions = [DetailAction("call", call_uri(raw)),
                   DetailAction("message", message_uri(raw))]
        phone_fields.append(DetailField(_phone_label(entry, index), display,
                                       tuple(a for a in actions if a.uri)))
    if phone_fields:
        sections.append(DetailSection("phones", "Numbers",
                                     tuple(phone_fields)))

    email_fields: list[DetailField] = []
    for index, entry in enumerate(record.emails):
        address = (entry.address or "").strip()
        if not address:
            continue
        uri = email_uri(address)
        email_fields.append(DetailField(
            _email_label(entry, index), address,
            (DetailAction("email", uri),) if uri else ()))
    if email_fields:
        sections.append(DetailSection("emails", "Email",
                                     tuple(email_fields)))

    address_fields: list[DetailField] = []
    for entry in record.addresses:
        display = format_address(entry)
        if not display:
            continue
        fallback = "Primary" if entry.is_primary else "Address"
        label = human_type_label(entry.type, entry.label, fallback)
        uri = maps_uri(display)
        address_fields.append(DetailField(
            label, display, (DetailAction("map", uri),) if uri else ()))
    if address_fields:
        sections.append(DetailSection("addresses", "Addresses",
                                     tuple(address_fields)))

    date_fields: list[DetailField] = []
    if (record.birthday or "").strip():
        date_fields.append(DetailField("Birthday",
                                       record.birthday.strip(), ()))
    if (record.anniversary or "").strip():
        date_fields.append(DetailField("Anniversary",
                                       record.anniversary.strip(), ()))
    for entry in record.dates:
        value = (entry.value or "").strip()
        if value:
            date_fields.append(DetailField(entry.kind or "Date", value, ()))
    if date_fields:
        sections.append(DetailSection("dates", "Dates", tuple(date_fields)))

    web_fields: list[DetailField] = []
    for entry in record.urls:
        value = (entry.value or "").strip()
        if value:
            web_fields.append(DetailField(
                entry.type or "Link", value,
                (DetailAction("open", value),)))
    for entry in record.ims:
        value = (entry.value or "").strip()
        if value:
            web_fields.append(DetailField(entry.protocol or "Chat", value,
                                         ()))
    for entry in record.relations:
        value = (entry.value or "").strip()
        if value:
            web_fields.append(DetailField(entry.type or "Related", value,
                                         ()))
    if web_fields:
        sections.append(DetailSection("web", "Web", tuple(web_fields)))

    if (record.note or "").strip():
        sections.append(DetailSection(
            "notes", "Notes",
            (DetailField("Note", record.note.strip(), ()),)))

    groups = [name for name in record.groups if name.strip()]
    if groups:
        sections.append(DetailSection(
            "groups", "Groups",
            tuple(DetailField("Group", name.strip(), ()) for name in groups)))

    return sections
