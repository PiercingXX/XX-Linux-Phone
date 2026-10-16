"""Starred-contacts bridge toward the XX-WM Do-Not-Disturb exceptions.

Pure helpers: collect starred numbers from contacts and merge them into
the shell's ``dnd_starred_numbers`` config list without duplicates.
"""

from __future__ import annotations

from xx_contacts.contacts import Contact, normalize_number


def starred_numbers(contacts: list[Contact]) -> list[str]:
    numbers: list[str] = []
    for contact in contacts:
        if not contact.starred:
            continue
        for phone in contact.phones:
            digits = normalize_number(phone)
            if digits and digits not in numbers:
                numbers.append(digits)
    return sorted(numbers)


def merge_starred_numbers(existing: list[str], starred: list[str]) -> list[str]:
    merged = {normalize_number(n) for n in existing}
    merged.discard("")
    for number in starred:
        digits = normalize_number(number)
        if digits:
            merged.add(digits)
    return sorted(merged)
