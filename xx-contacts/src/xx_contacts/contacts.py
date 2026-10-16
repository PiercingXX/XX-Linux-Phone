"""Contact model and lookup helpers. EDS binding arrives in a later lane.

This module stays importable without EDS typelibs: the EBook import
happens lazily inside EdsBook so headless machines and tests keep working.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Contact:
    uid: str = ""
    display_name: str = ""
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    starred: bool = False
    blocked: bool = False


def normalize_number(number: str) -> str:
    return re.sub(r"[^\d]", "", number)


def numbers_match(a: str, b: str, tail: int = 7) -> bool:
    digits_a = normalize_number(a)
    digits_b = normalize_number(b)
    if not digits_a or not digits_b:
        return False
    width = min(tail, len(digits_a), len(digits_b))
    return digits_a[-width:] == digits_b[-width:]


def lookup_number(contacts: list[Contact], number: str) -> str | None:
    for contact in contacts:
        for phone in contact.phones:
            if numbers_match(phone, number):
                return contact.display_name
    return None


def search(contacts: list[Contact], query: str, limit: int = 50) -> list[Contact]:
    if not query:
        return []
    folded = query.casefold()
    digits = normalize_number(query)
    number_hits: list[Contact] = []
    prefix_hits: list[Contact] = []
    sub_hits: list[Contact] = []
    for contact in contacts:
        if digits and any(normalize_number(p).startswith(digits)
                          or normalize_number(p).endswith(digits)
                          for p in contact.phones):
            number_hits.append(contact)
            continue
        name = contact.display_name.casefold()
        if name.startswith(folded):
            prefix_hits.append(contact)
        elif folded in name:
            sub_hits.append(contact)
    return (number_hits + prefix_hits + sub_hits)[:limit]


class MemoryBook:
    def __init__(self, contacts: list[Contact] | None = None) -> None:
        self._contacts = list(contacts or [])

    @property
    def all(self) -> list[Contact]:
        return list(self._contacts)

    def upsert(self, contact: Contact) -> None:
        for i, existing in enumerate(self._contacts):
            if existing.uid and existing.uid == contact.uid:
                self._contacts[i] = contact
                return
        self._contacts.append(contact)

    def delete(self, uid: str) -> bool:
        before = len(self._contacts)
        self._contacts = [c for c in self._contacts if c.uid != uid]
        return len(self._contacts) != before


class EdsBook:
    """Evolution Data Server address-book backend (needs eds typelibs)."""

    def __init__(self, source_uid: str | None = None) -> None:
        self._source_uid = source_uid

    def _client(self):
        try:
            import gi  # noqa: F401
            gi.require_version("EBook", "3.0")
            gi.require_version("EBookContacts", "1.2")
        except (ImportError, ValueError) as exc:
            raise RuntimeError("EDS typelibs are not installed") from exc
        from gi.repository import EBook
        registry = EBook.SourceRegistry.new_sync(None)
        source = (registry.ref_source(self._source_uid)
                  if self._source_uid else registry.ref_default_address_book())
        return EBook.BookClient.connect_sync(source, None)

    def all(self) -> list[Contact]:
        raise NotImplementedError("EDS query lands in the data-layer lane")
