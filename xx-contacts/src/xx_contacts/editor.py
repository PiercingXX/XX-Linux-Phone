"""L4 editor model without GTK: form state, validation, loss-free apply.

Pure module so pytest runs headless. The GTK page (``editor_ui``) binds
these forms to rows; saving goes through :func:`save_new` /
:func:`save_edit`, each of which performs exactly ONE store write (one
global revision bump). Properties the form does not show (``extra``)
are carried over verbatim so an edit round-trip preserves them.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import date

from xx_contacts import phones as phones_module
from xx_contacts.detail import human_type_label
from xx_contacts.store import ContactData

#: Phone/email/address type tokens offered by the editor's type pickers,
#: in display order. Labels come from ``detail.human_type_label`` so the
#: picker reads exactly like the detail page (CELL -> Mobile, ...).
TYPE_TOKENS = ("CELL", "HOME", "WORK", "MAIN", "VOICE", "FAX", "PAGER",
               "IPHONE", "OTHER")


def type_options() -> list[tuple[str, str]]:
    """``(token, human label)`` pairs for the type pickers."""
    return [(token, human_type_label(token, "", token.title()))
            for token in TYPE_TOKENS]


EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DATE_SHAPE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def is_valid_email(value: str) -> bool:
    """Minimal email shape check: one ``@``, a dot past it, no spaces."""
    return bool(EMAIL_SHAPE.match((value or "").strip()))


def is_valid_date(value: str) -> bool:
    """``YYYY-MM-DD`` that names a real calendar day (picker output)."""
    match = DATE_SHAPE.match((value or "").strip())
    if not match:
        return False
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return False
    return True


@dataclass
class PhoneForm:
    number: str = ""
    type: str = ""
    label: str = ""
    is_primary: bool = False


@dataclass
class EmailForm:
    address: str = ""
    type: str = ""
    label: str = ""
    is_primary: bool = False


@dataclass
class AddressForm:
    po_box: str = ""
    extended: str = ""
    street: str = ""
    locality: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""
    type: str = ""
    label: str = ""
    is_primary: bool = False


@dataclass
class UrlForm:
    value: str = ""
    type: str = ""


@dataclass
class EditorForm:
    given: str = ""
    family: str = ""
    prefix: str = ""
    suffix: str = ""
    nickname: str = ""
    org: str = ""
    title: str = ""
    birthday: str = ""
    anniversary: str = ""
    note: str = ""
    groups: list[str] = field(default_factory=list)
    starred: bool = False
    business: bool = False
    blocked: bool = False
    phones: list[PhoneForm] = field(default_factory=list)
    emails: list[EmailForm] = field(default_factory=list)
    addresses: list[AddressForm] = field(default_factory=list)
    urls: list[UrlForm] = field(default_factory=list)


def blank_form() -> EditorForm:
    """A new-contact form with one empty phone and one empty email row."""
    return EditorForm(phones=[PhoneForm()], emails=[EmailForm()])


def clone_form(form: EditorForm) -> EditorForm:
    """Deep copy for dirty tracking snapshots."""
    return copy.deepcopy(form)


def form_from_record(record: ContactData) -> EditorForm:
    """Project a store record onto the editable fields.

    ``extra`` and ``photo_path`` stay on the record: the apply step
    copies them back verbatim, so the form never sees (or drops) them.
    """
    return EditorForm(
        given=record.given,
        family=record.family,
        prefix=record.prefix,
        suffix=record.suffix,
        nickname=record.nickname,
        org=record.org,
        title=record.title,
        birthday=record.birthday,
        anniversary=record.anniversary,
        note=record.note,
        groups=list(record.groups),
        starred=bool(record.starred),
        business=bool(record.business),
        blocked=bool(record.blocked),
        phones=[PhoneForm(number=p.number_raw, type=p.type, label=p.label,
                          is_primary=bool(p.is_primary))
                for p in record.phones],
        emails=[EmailForm(address=e.address, type=e.type, label=e.label,
                          is_primary=bool(e.is_primary))
                for e in record.emails],
        addresses=[AddressForm(
            po_box=a.po_box, extended=a.extended, street=a.street,
            locality=a.locality, region=a.region, postal_code=a.postal_code,
            country=a.country, type=a.type, label=a.label,
            is_primary=bool(a.is_primary)) for a in record.addresses],
        urls=[UrlForm(value=u.value, type=u.type) for u in record.urls],
    )


def is_dirty(saved: EditorForm, current: EditorForm) -> bool:
    """True when the form differs from its load-time snapshot."""
    return saved != current


def _first_phone(form: EditorForm) -> str:
    for phone in form.phones:
        if phone.number.strip():
            return phone.number.strip()
    return ""


def _first_email(form: EditorForm) -> str:
    for email in form.emails:
        if email.address.strip():
            return email.address.strip()
    return ""


def display_preview(form: EditorForm) -> str:
    """Live display-name preview: names, else org/phone/email, else blank."""
    given = form.given.strip()
    family = form.family.strip()
    composed = " ".join(part for part in (given, family) if part)
    if composed:
        return composed
    for fallback in (form.org.strip(), _first_phone(form),
                     _first_email(form)):
        if fallback:
            return fallback
    return ""


def phone_preview(number: str, default_region: str = "US") -> str:
    """Normalized preview for a typed number (display form + E.164)."""
    raw = (number or "").strip()
    if not raw:
        return ""
    shown = phones_module.format_display_number(raw, default_region)
    e164 = phones_module.to_e164(raw, default_region)
    if e164 and e164 != shown:
        return f"{shown} ({e164})"
    return shown


def validate(form: EditorForm) -> str | None:
    """Error text blocking save, or None when the form may save.

    At least a name part, nickname, org, phone or email is required;
    every non-blank email must have a plausible shape; dates the
    picker writes are re-checked so a hand-built form cannot sneak a
    bad value past the UI.
    """
    named = any(part.strip() for part in
                (form.given, form.family, form.nickname, form.org))
    if not named and not _first_phone(form) and not _first_email(form):
        return "Add a name or a number before saving."
    for index, email in enumerate(form.emails):
        if email.address.strip() and not is_valid_email(email.address):
            return f"Email {index + 1} does not look like an email address."
    for value, kind in ((form.birthday, "Birthday"),
                        (form.anniversary, "Anniversary")):
        if value.strip() and not is_valid_date(value):
            return f"{kind} must be a real YYYY-MM-DD date."
    return None


def _single_primary(flags: list[bool]) -> list[bool]:
    """Keep the first primary flag only, so one row stays primary."""
    seen = False
    kept: list[bool] = []
    for flag in flags:
        kept.append(bool(flag) and not seen)
        seen = seen or bool(flag)
    return kept


def apply_form_to_data(original: ContactData, form: EditorForm,
                       default_region: str = "US") -> ContactData:
    """Build store data from a validated form, keeping unseen fields.

    ``extra`` (unknown vCard properties, X- props, extra params) is
    copied verbatim from ``original``; ``photo_path`` is kept and the
    caller overrides it only when the user picked or removed a photo.
    Blank rows are dropped; phones are normalized via ``phones``.
    """
    from xx_contacts.store import (
        AddressEntry,
        EmailEntry,
        PhoneEntry,
        UrlEntry,
    )

    region = (default_region or "US").upper()
    data = ContactData()
    data.given = form.given.strip()
    data.family = form.family.strip()
    data.prefix = form.prefix.strip()
    data.suffix = form.suffix.strip()
    data.nickname = form.nickname.strip()
    data.org = form.org.strip()
    data.title = form.title.strip()
    data.birthday = form.birthday.strip()
    data.anniversary = form.anniversary.strip()
    data.note = form.note.strip()
    data.display_name = display_preview(form)
    seen_groups: set[str] = set()
    for name in form.groups:
        cleaned = name.strip()
        if cleaned and cleaned not in seen_groups:
            seen_groups.add(cleaned)
            data.groups.append(cleaned)
    data.starred = bool(form.starred)
    data.business = bool(form.business)
    data.blocked = bool(form.blocked)

    phone_rows = [p for p in form.phones if p.number.strip()]
    primaries = _single_primary([p.is_primary for p in phone_rows])
    for phone, primary in zip(phone_rows, primaries):
        raw = phone.number.strip()
        digits, e164 = phones_module.normalize(raw, region)
        data.phones.append(PhoneEntry(
            number_raw=raw, number_e164=e164, number_digits=digits,
            type=phone.type.strip(), label=phone.label.strip(),
            is_primary=primary))
    email_rows = [e for e in form.emails if e.address.strip()]
    email_primaries = _single_primary([e.is_primary for e in email_rows])
    for email, primary in zip(email_rows, email_primaries):
        data.emails.append(EmailEntry(
            address=email.address.strip(), type=email.type.strip(),
            label=email.label.strip(), is_primary=primary))
    address_rows = [a for a in form.addresses if any(
        part.strip() for part in
        (a.po_box, a.extended, a.street, a.locality, a.region,
         a.postal_code, a.country))]
    address_primaries = _single_primary(
        [a.is_primary for a in address_rows])
    for entry, primary in zip(address_rows, address_primaries):
        data.addresses.append(AddressEntry(
            po_box=entry.po_box.strip(), extended=entry.extended.strip(),
            street=entry.street.strip(), locality=entry.locality.strip(),
            region=entry.region.strip(),
            postal_code=entry.postal_code.strip(),
            country=entry.country.strip(), type=entry.type.strip(),
            label=entry.label.strip(), is_primary=primary))
    for url in form.urls:
        if url.value.strip():
            data.urls.append(UrlEntry(value=url.value.strip(),
                                      type=url.type.strip()))

    data.photo_path = original.photo_path
    data.extra = [copy.deepcopy(line) for line in original.extra]
    return data


def save_new(store, form: EditorForm, default_region: str = "US",
             uid: str | None = None, photo_path: str = "") -> tuple[str, int]:
    """Validate, insert with ONE store write; return ``(uid, revision)``."""
    error = validate(form)
    if error is not None:
        raise ValueError(error)
    data = apply_form_to_data(ContactData(), form, default_region)
    data.photo_path = photo_path or ""
    fresh = store.create(data, uid)
    return fresh, store.get_revision()


def save_edit(store, uid: str, form: EditorForm,
              default_region: str = "US",
              photo_path: str | None = None) -> int:
    """Validate, replace ``uid`` with ONE store write; return revision.

    ``photo_path=None`` keeps the stored photo; pass ``""`` to remove it
    or a new path to replace it.
    """
    from xx_contacts.store import NotFoundError

    error = validate(form)
    if error is not None:
        raise ValueError(error)
    try:
        original = store.get(uid)
    except NotFoundError:
        raise
    data = apply_form_to_data(original, form, default_region)
    if photo_path is not None:
        data.photo_path = photo_path
    return store.update(uid, data)
