"""vCard 4.0 + 3.0 codec. Port of data/VCard.kt and backup/VCardCodec.kt.

Pure module: line unfolding (folding + 3.0 quoted-printable soft breaks),
escaping, property parameters (TYPE/PREF/VALUE/ENCODING, bare 3.0 type
tokens, quoted values), base64 and quoted-printable PHOTO values, repeated
properties, ``item1.`` grouping prefixes with ``X-ABLABEL`` custom labels,
and lossless round-trip of unmapped properties through ``extra_vcard``.

The legacy ``VCardContact`` / ``parse`` / ``serialize`` helpers below keep
their 3.0 minimal shape for older callers; new code should use
``parse_cards`` / ``contact_to_vcard`` / ``contact_from_vcard``.
"""

from __future__ import annotations

import base64
import binascii
import quopri
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xx_contacts.store import ContactData, ContactRecord

MIME_TYPE = "text/vcard"
VERSION_3_0 = "3.0"
VERSION_4_0 = "4.0"

FOLD_OCTETS = 75


@dataclass
class VCardContact:
    display_name: str = ""
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    company: str = ""
    job_title: str = ""
    address: str = ""
    birthday: str = ""
    nickname: str = ""
    note: str = ""


def _escape(value: str) -> str:
    return (value.replace("\\", "\\\\").replace("\n", "\\n")
                 .replace(";", "\\;").replace(",", "\\,"))


def _unescape(value: str) -> str:
    out: list[str] = []
    raw = value
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw) and raw[i + 1] in "nN;,\\":
            nxt = raw[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def unfold(text: str) -> list[str]:
    logical: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and logical:
            logical[-1] += line[1:]
        else:
            logical.append(line)
    return [line for line in logical if line]


def _prop_name(line: str) -> tuple[str, str]:
    head, _, value = line.partition(":")
    return head.split(";")[0].strip().upper(), value


def parse(text: str) -> VCardContact:
    contact = VCardContact()
    for line in unfold(text):
        name, value = _prop_name(line)
        text_value = _unescape(value.strip())
        if name == "FN" and not contact.display_name:
            contact.display_name = text_value
        elif name == "N" and not contact.display_name:
            parts = [p for p in text_value.split(";") if p]
            contact.display_name = " ".join(reversed(parts)) if parts else ""
        elif name == "TEL" and text_value:
            contact.phones.append(text_value)
        elif name == "EMAIL" and text_value:
            contact.emails.append(text_value)
        elif name == "ORG" and not contact.company:
            contact.company = text_value.split(";")[0]
        elif name == "TITLE" and not contact.job_title:
            contact.job_title = text_value
        elif name == "ADR" and not contact.address:
            contact.address = "; ".join(
                p for p in text_value.split(";") if p)
        elif name == "BDAY" and not contact.birthday:
            contact.birthday = text_value
        elif name == "NICKNAME" and not contact.nickname:
            contact.nickname = text_value.split(",")[0]
        elif name == "NOTE":
            contact.note = (contact.note + "\n" + text_value
                            if contact.note else text_value)
    return contact


def serialize(contact: VCardContact) -> str:
    lines = ["BEGIN:VCARD", "VERSION:3.0",
             "FN:" + _escape(contact.display_name),
             "N:" + _escape(contact.display_name) + ";;;;"]
    lines += ["TEL;TYPE=CELL:" + _escape(p) for p in contact.phones]
    lines += ["EMAIL;TYPE=OTHER:" + _escape(e) for e in contact.emails]
    if contact.company:
        lines.append("ORG:" + _escape(contact.company))
    if contact.job_title:
        lines.append("TITLE:" + _escape(contact.job_title))
    if contact.address:
        lines.append("ADR:;;" + _escape(contact.address) + ";;;;")
    if contact.birthday:
        lines.append("BDAY:" + _escape(contact.birthday))
    if contact.nickname:
        lines.append("NICKNAME:" + _escape(contact.nickname))
    if contact.note:
        lines.append("NOTE:" + _escape(contact.note))
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def escape_value(value: str) -> str:
    return (value.replace("\\", "\\\\").replace("\n", "\\n")
            .replace(";", "\\;").replace(",", "\\,"))


def unescape_value(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def split_unescaped(value: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            current.append(ch)
            current.append(value[i + 1])
            i += 2
        elif ch == delimiter:
            parts.append("".join(current))
            current = []
            i += 1
        else:
            current.append(ch)
            i += 1
    parts.append("".join(current))
    return parts


def _header_is_qp(header: str) -> bool:
    return "QUOTED-PRINTABLE" in header.upper()


def unfold_lines(text: str) -> list[str]:
    """Unfold physical lines into logical lines.

    A line starting with a space or tab continues the previous line (the
    one folding character is dropped). A 3.0 quoted-printable soft break
    (``=`` at end of line on an ``ENCODING=QUOTED-PRINTABLE`` property)
    instead continues with the next physical line, dropping the ``=`` and
    one optional folding space, so QP bytes survive unfolding.
    """
    logical: list[str] = []
    pending_qp = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if pending_qp and logical:
            line = raw[1:] if raw[:1] in (" ", "\t") else raw
            logical[-1] = logical[-1][:-1] + line
            head = logical[-1].split(":", 1)[0]
            pending_qp = logical[-1].endswith("=") and _header_is_qp(head)
            continue
        if raw[:1] in (" ", "\t") and logical:
            logical[-1] += raw[1:]
        elif raw == "":
            continue
        else:
            logical.append(raw)
        if logical:
            head = logical[-1].split(":", 1)[0]
            pending_qp = logical[-1].endswith("=") and _header_is_qp(head)
    return logical


def fold_line(line: str, limit: int = FOLD_OCTETS) -> str:
    """Fold one logical line to ``CRLF + space`` continuations (75 octets)."""
    encoded = line.encode("utf-8")
    if len(encoded) <= limit:
        return line
    chunks: list[str] = []
    current = bytearray()
    for byte in encoded:
        if byte < 0x80 or byte >= 0xC0:
            if len(current) + (1 if byte < 0x80 else 4) > (
                    limit if not chunks else limit - 1) and current:
                chunks.append(bytes(current).decode("utf-8"))
                current = bytearray()
        current.append(byte)
    if current:
        chunks.append(bytes(current).decode("utf-8"))
    out = [chunks[0]]
    out += [" " + chunk for chunk in chunks[1:]]
    return "\r\n".join(out)


def _find_value_colon(line: str) -> int:
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ":" and not in_quote:
            return i
    return -1


def _split_params(segment: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_quote = False
    for ch in segment:
        if ch == '"':
            in_quote = not in_quote
            current.append(ch)
        elif ch == ";" and not in_quote:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _split_param_values(raw: str) -> list[str]:
    values: list[str] = []
    current: list[str] = []
    in_quote = False
    for ch in raw:
        if ch == '"':
            in_quote = not in_quote
        elif ch == "," and not in_quote:
            values.append("".join(current))
            current = []
        else:
            current.append(ch)
    values.append("".join(current))
    return [v for v in values if v != ""]


@dataclass
class VCardProperty:
    group: str = ""
    name: str = ""
    params: list[tuple[str, list[str]]] = field(default_factory=list)
    value: str = ""
    raw_line: str = ""

    def param(self, key: str) -> list[str]:
        wanted = key.upper()
        found: list[str] = []
        for name, values in self.params:
            if name.upper() == wanted:
                found.extend(values)
        return found

    def types(self) -> list[str]:
        return [t.upper() for t in self.param("TYPE")]

    def is_primary(self) -> bool:
        if "PREF" in self.types():
            return True
        return any(v == "1" for v in self.param("PREF"))

    def encoding(self) -> str:
        values = self.param("ENCODING")
        return values[0].upper() if values else ""


@dataclass
class Card:
    version: str = ""
    properties: list[VCardProperty] = field(default_factory=list)

    def all(self, name: str) -> list[VCardProperty]:
        wanted = name.upper()
        return [p for p in self.properties if p.name.upper() == wanted]

    def first(self, name: str) -> VCardProperty | None:
        found = self.all(name)
        return found[0] if found else None


def parse_property(line: str) -> VCardProperty | None:
    colon = _find_value_colon(line)
    if colon <= 0:
        return None
    head = line[:colon]
    value = line[colon + 1:]
    segments = _split_params(head)
    fullname = segments[0].strip()
    group = ""
    name = fullname
    if "." in fullname:
        group, _, rest = fullname.partition(".")
        if group and rest and " " not in group:
            name = rest
        else:
            group = ""
    params: list[tuple[str, list[str]]] = []
    for token in segments[1:]:
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            key, _, raw = token.partition("=")
            params.append((key.strip().upper(),
                           _split_param_values(raw.strip())))
        else:
            params.append(("TYPE", [token.upper()]))
    return VCardProperty(group=group, name=name.strip().upper(),
                         params=params, value=value, raw_line=line)


def decode_binary(prop: VCardProperty) -> bytes | None:
    """Decode a base64 (``ENCODING=b``) or quoted-printable property value."""
    encoding = prop.encoding()
    if encoding in ("B", "BASE64"):
        try:
            return base64.b64decode("".join(prop.value.split()))
        except (binascii.Error, ValueError):
            return None
    if encoding == "QUOTED-PRINTABLE":
        try:
            return quopri.decodestring(prop.value.encode("utf-8", "replace"))
        except ValueError:
            return None
    return None


def parse_cards(text: str) -> list[Card]:
    """Parse zero or more ``BEGIN:VCARD`` … ``END:VCARD`` blocks."""
    cards: list[Card] = []
    current: list[str] | None = None
    for line in unfold_lines(text):
        stripped = line.strip()
        if stripped.upper() == "BEGIN:VCARD":
            current = []
        elif stripped.upper() == "END:VCARD":
            if current is not None:
                props: list[VCardProperty] = []
                for item in current:
                    prop = parse_property(item)
                    if prop is not None:
                        props.append(prop)
                version = ""
                for prop in props:
                    if prop.name == "VERSION":
                        version = prop.value.strip()
                        break
                cards.append(Card(version=version, properties=props))
            current = None
        elif current is not None:
            current.append(line)
    return cards


def serialize_property(prop: VCardProperty) -> str:
    head = prop.name.upper()
    if prop.group:
        head = f"{prop.group}.{head}"
    for key, values in prop.params:
        head += ";" + key.upper() + "=" + ",".join(values)
    return fold_line(f"{head}:{prop.value}")


def serialize_card(card: Card) -> str:
    lines = ["BEGIN:VCARD"]
    lines.append(fold_line(f"VERSION:{card.version or VERSION_4_0}"))
    for prop in card.properties:
        if prop.name == "VERSION":
            continue
        lines.append(serialize_property(prop))
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


MAPPED_PROPERTIES = frozenset({
    "FN", "N", "NICKNAME", "BDAY", "ANNIVERSARY", "ORG", "TITLE",
    "TEL", "EMAIL", "ADR", "URL", "IMPP", "RELATED", "NOTE",
    "CATEGORIES", "UID", "PHOTO", "X-STARRED", "X-BUSINESS", "X-BLOCKED",
})

STRUCTURAL_PROPERTIES = frozenset({"BEGIN", "END", "VERSION", "PRODID"})


def _decode_qp_text(text: str) -> str:
    try:
        raw = quopri.decodestring(text.encode("utf-8", "replace"))
    except ValueError:
        return text
    return raw.decode("utf-8", "replace")


def decoded_text(prop: VCardProperty) -> str:
    """Mapped text of a property, QP-decoding 3.0 encoded values."""
    if prop.encoding() == "QUOTED-PRINTABLE":
        return _decode_qp_text(prop.value)
    return prop.value


def _group_labels(card: Card) -> dict[str, str]:
    labels: dict[str, str] = {}
    for prop in card.properties:
        if prop.group and prop.name in ("X-ABLABEL", "X-ABADR"):
            labels[prop.group] = unescape_value(decoded_text(prop))
    return labels


def _is_uri_photo(prop: VCardProperty) -> bool:
    values = [v.upper() for v in prop.param("VALUE")]
    if "URI" in values:
        return True
    lowered = prop.value.strip().lower()
    if lowered.startswith(("file:", "http:", "https:", "content:")):
        return True
    return bool(prop.value.strip()) and " " not in prop.value.strip() \
        and prop.encoding() == "" and "," not in prop.value \
        and ";" not in prop.value and len(prop.value.strip()) < 512 \
        and prop.value.strip().startswith("/")


def contact_from_card(card: Card, default_region: str = "US"
                      ) -> tuple[ContactData, str | None]:
    """Map one parsed card onto store models.

    Returns ``(ContactData, uid_hint_or_None)``. Properties the store has
    no column for are kept verbatim as ``ExtraLine`` entries so export
    round-trips them; ``item1.`` group prefixes survive on those extras
    and feed ``X-ABLABEL`` custom labels for grouped phones and emails.
    """
    from xx_contacts import store as store_module
    from xx_contacts.phones import normalize as normalize_phone

    data = store_module.ContactData()
    uid_hint: str | None = None
    group_labels = _group_labels(card)
    notes: list[str] = []

    def custom_label(prop: VCardProperty, standard: str) -> str:
        label_param = prop.param("LABEL")
        if label_param:
            return unescape_value(label_param[0])
        if prop.group and prop.group in group_labels:
            return group_labels[prop.group]
        return standard

    for prop in card.properties:
        name = prop.name.upper()
        if name in STRUCTURAL_PROPERTIES or name == "VERSION":
            continue
        text = decoded_text(prop)
        if name == "FN":
            if not data.display_name:
                data.display_name = unescape_value(text.strip())
        elif name == "N":
            parts = split_unescaped(text, ";")
            got = [unescape_value(p.strip()) for p in parts]
            while len(got) < 5:
                got.append("")
            if not data.family:
                data.family = got[0]
            if not data.given:
                data.given = got[1]
            if not data.middle:
                data.middle = got[2]
            if not data.prefix:
                data.prefix = got[3]
            if not data.suffix:
                data.suffix = got[4]
            if not data.display_name:
                if got[0] and got[1]:
                    data.display_name = f"{got[1]} {got[0]}"
                else:
                    data.display_name = got[0] or got[1]
        elif name == "NICKNAME":
            if not data.nickname:
                names = [unescape_value(v)
                         for v in split_unescaped(text, ",")]
                data.nickname = ",".join(v for v in names if v)
        elif name == "BDAY":
            if not data.birthday:
                data.birthday = unescape_value(text.strip())
        elif name == "ANNIVERSARY":
            if not data.anniversary:
                data.anniversary = unescape_value(text.strip())
        elif name == "ORG":
            if not data.org:
                data.org = unescape_value(text.strip())
        elif name == "TITLE":
            if not data.title:
                data.title = unescape_value(text.strip())
        elif name == "TEL":
            raw = unescape_value(text.strip())
            if raw:
                digits, e164 = normalize_phone(raw, default_region)
                types = prop.types()
                label = custom_label(prop, "")
                data.phones.append(store_module.PhoneEntry(
                    number_raw=raw, number_e164=e164, number_digits=digits,
                    type=",".join(types), label=label,
                    is_primary=prop.is_primary()))
        elif name == "EMAIL":
            address = unescape_value(text.strip())
            if address:
                types = prop.types()
                data.emails.append(store_module.EmailEntry(
                    address=address, type=",".join(types),
                    label=custom_label(prop, ""),
                    is_primary=prop.is_primary()))
        elif name == "ADR":
            parts = split_unescaped(text, ";")
            got = [unescape_value(p.strip()) for p in parts]
            while len(got) < 7:
                got.append("")
            types = prop.types()
            data.addresses.append(store_module.AddressEntry(
                po_box=got[0], extended=got[1], street=got[2],
                locality=got[3], region=got[4], postal_code=got[5],
                country=got[6], type=",".join(types),
                label=custom_label(prop, ""),
                is_primary=prop.is_primary()))
        elif name == "URL":
            value = unescape_value(text.strip())
            if value:
                data.urls.append(store_module.UrlEntry(
                    value=value, type=",".join(prop.types())))
        elif name == "IMPP":
            value = unescape_value(text.strip())
            if value:
                protocol = value.split(":", 1)[0].lower() \
                    if ":" in value else ""
                data.ims.append(store_module.ImEntry(
                    value=value, protocol=protocol,
                    type=",".join(prop.types())))
        elif name == "RELATED":
            value = unescape_value(text.strip())
            if value:
                data.relations.append(store_module.RelationEntry(
                    value=value, type=",".join(prop.types())))
        elif name == "NOTE":
            notes.append(unescape_value(text))
        elif name == "CATEGORIES":
            for entry in split_unescaped(text, ","):
                cleaned = unescape_value(entry.strip())
                if cleaned and cleaned not in data.groups:
                    data.groups.append(cleaned)
        elif name == "UID":
            candidate = unescape_value(prop.value.strip())
            if uid_hint is None and store_module.is_valid_uid(candidate):
                uid_hint = candidate.lower()
            elif candidate and not any(
                    e.prop == "UID" and e.value == prop.value
                    for e in data.extra):
                data.extra.append(store_module.ExtraLine(
                    prop="UID", params=prop_params_raw(prop),
                    value=prop.value, group=prop.group))
        elif name == "PHOTO":
            if _is_uri_photo(prop):
                if not data.photo_path:
                    data.photo_path = unescape_value(prop.value.strip())
            else:
                data.extra.append(store_module.ExtraLine(
                    prop="PHOTO", params=prop_params_raw(prop),
                    value=prop.value, group=prop.group))
        elif name == "X-STARRED":
            if prop.value.strip() in ("1", "true", "TRUE", "True"):
                data.starred = True
            elif not data.starred:
                data.extra.append(store_module.ExtraLine(
                    prop="X-STARRED", params=prop_params_raw(prop),
                    value=prop.value, group=prop.group))
        elif name == "X-BUSINESS":
            if prop.value.strip() in ("1", "true", "TRUE", "True"):
                data.business = True
            elif not data.business:
                data.extra.append(store_module.ExtraLine(
                    prop="X-BUSINESS", params=prop_params_raw(prop),
                    value=prop.value, group=prop.group))
        elif name == "X-BLOCKED":
            if prop.value.strip() in ("1", "true", "TRUE", "True"):
                data.blocked = True
            elif not data.blocked:
                data.extra.append(store_module.ExtraLine(
                    prop="X-BLOCKED", params=prop_params_raw(prop),
                    value=prop.value, group=prop.group))
        elif name in ("X-ABLABEL", "X-ABADR"):
            if not prop.group or prop.group not in {
                    p.group for p in card.properties
                    if p.group == prop.group and p.name in
                    ("TEL", "EMAIL", "ADR", "URL")}:
                data.extra.append(store_module.ExtraLine(
                    prop=name, params=prop_params_raw(prop),
                    value=prop.value, group=prop.group))
        else:
            data.extra.append(store_module.ExtraLine(
                prop=name, params=prop_params_raw(prop),
                value=prop.value, group=prop.group))
    if notes:
        data.note = "\n".join(notes)
    if not data.display_name:
        data.display_name = data.org or (
            data.phones[0].number_raw if data.phones else "")
    return data, uid_hint


def prop_params_raw(prop: VCardProperty) -> str:
    """Return the raw parameter segment of a parsed line (lossless keep)."""
    colon = _find_value_colon(prop.raw_line)
    if colon <= 0:
        return ""
    semi = prop.raw_line[:colon].find(";")
    if semi < 0:
        return ""
    return prop.raw_line[semi + 1:colon]


def _tel_params(entry_type: str, is_primary: bool) -> str:
    out = ""
    if entry_type:
        out += ";TYPE=" + entry_type
    if is_primary:
        out += ";PREF=1"
    return out


def contact_to_vcard(record: ContactRecord,
                       photo_bytes: bytes | None = None) -> str:
    """Serialize a store ContactRecord to one vCard 4.0 block.

    Unmapped properties stored in ``extra`` are re-emitted verbatim (folded
    to the line limit), so import → export round-trips them. Normalized
    photo bytes are exported as a 4.0 ``PHOTO:data:image/jpeg;base64,``
    line; without them a stored ``photo_path`` keeps the legacy
    ``PHOTO;VALUE=URI:`` line.
    """
    lines = ["BEGIN:VCARD", "VERSION:4.0",
             f"UID:{record.uid}",
             "FN:" + escape_value(record.display_name)]
    lines.append("N:" + ";".join(escape_value(part) for part in
                                 (record.family, record.given, record.middle,
                                  record.prefix, record.suffix)))
    if record.nickname:
        lines.append("NICKNAME:" + ",".join(
            escape_value(p) for p in record.nickname.split(",")))
    if record.birthday:
        lines.append("BDAY:" + escape_value(record.birthday))
    if record.anniversary:
        lines.append("ANNIVERSARY:" + escape_value(record.anniversary))
    if record.org:
        lines.append("ORG:" + escape_value(record.org))
    if record.title:
        lines.append("TITLE:" + escape_value(record.title))
    group_seq = 0
    for phone in record.phones:
        label = phone.label or ""
        needs_group = bool(label)
        params = _tel_params(phone.type, phone.is_primary)
        if needs_group:
            group_seq += 1
            tag = f"item{group_seq}"
            lines.append(f"{tag}.TEL{params}:{escape_value(phone.number_raw)}")
            lines.append(f"{tag}.X-ABLABEL:{escape_value(label)}")
        else:
            lines.append(f"TEL{params}:{escape_value(phone.number_raw)}")
    for email in record.emails:
        params = _tel_params(email.type, email.is_primary)
        lines.append(f"EMAIL{params}:{escape_value(email.address)}")
    for address in record.addresses:
        params = _tel_params(address.type, address.is_primary)
        lines.append("ADR" + params + ":" + ";".join(
            escape_value(p) for p in (address.po_box, address.extended,
                                      address.street, address.locality,
                                      address.region, address.postal_code,
                                      address.country)))
    for url in record.urls:
        params = f";TYPE={url.type}" if url.type else ""
        lines.append(f"URL{params}:{escape_value(url.value)}")
    for im in record.ims:
        params = f";TYPE={im.type}" if im.type else ""
        lines.append(f"IMPP{params}:{escape_value(im.value)}")
    for relation in record.relations:
        params = f";TYPE={relation.type}" if relation.type else ""
        lines.append(f"RELATED{params}:{escape_value(relation.value)}")
    if record.note:
        for chunk in record.note.split("\n"):
            lines.append("NOTE:" + escape_value(chunk))
    if record.groups:
        lines.append("CATEGORIES:" + ",".join(
            escape_value(g) for g in record.groups))
    if photo_bytes:
        lines.append("PHOTO:data:image/jpeg;base64,"
                     + base64.b64encode(photo_bytes).decode("ascii"))
    elif record.photo_path:
        lines.append("PHOTO;VALUE=URI:" + escape_value(record.photo_path))
    if record.starred:
        lines.append("X-STARRED:1")
    if record.business:
        lines.append("X-BUSINESS:1")
    if record.blocked:
        lines.append("X-BLOCKED:1")
    for extra in record.extra:
        lines.append(extra.logical_line())
    lines.append("END:VCARD")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"


def contact_from_vcard(text: str, default_region: str = "US"
                       ) -> tuple[ContactData, str | None]:
    """Parse the first card of ``text`` onto store models."""
    cards = parse_cards(text)
    if not cards:
        raise ValueError("no vCard block found")
    return contact_from_card(cards[0], default_region)


def contacts_from_vcard(text: str, default_region: str = "US"
                        ) -> list[tuple[ContactData, str | None]]:
    """Parse every card of ``text`` onto store models."""
    return [contact_from_card(card, default_region)
            for card in parse_cards(text)]
