"""Name rules for the editor. Port of EditorNames.kt: First + Last only."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Split:
    given: str | None
    family: str | None


@dataclass(frozen=True)
class Loaded:
    given: str | None
    family: str | None
    display: str | None

    @classmethod
    def seed(cls, given: str | None, family: str | None,
             display: str | None) -> "Loaded":
        clean_given = (given or "").strip() or None
        clean_family = (family or "").strip() or None
        if clean_given is None and clean_family is None:
            return cls((display or "").strip() or None, None, display)
        return cls(clean_given, clean_family, display)


def split(name: str | None) -> Split:
    trimmed = (name or "").strip()
    if not trimmed:
        return Split(None, None)
    cut = trimmed.rfind(" ")
    if cut < 0:
        return Split(trimmed, None)
    given = trimmed[:cut].strip() or None
    family = trimmed[cut + 1:].strip() or None
    return Split(given, family)


def compose(given: str | None, family: str | None) -> str:
    parts = [p.strip() for p in (given, family) if p and p.strip()]
    return " ".join(parts)


def display_name(given: str | None, family: str | None,
                 loaded: Loaded | None = None,
                 fallbacks: list[str | None] | None = None) -> str | None:
    """The display name to write.

    A typed name composes from First + Last, except an unchanged name on an
    edited contact keeps the loaded display name (middle names and suffixes
    survive). With no name, the first non-blank fallback (company, phone,
    email) wins. None means nothing usable was entered.
    """
    composed = compose(given, family)
    options = fallbacks if fallbacks is not None else []
    if composed:
        unchanged = (loaded is not None
                     and (given or "").strip() == (loaded.given or "").strip()
                     and (family or "").strip() == (loaded.family or "").strip())
        kept = (loaded.display or "").strip() if loaded else ""
        return kept if unchanged and kept else composed
    for fallback in options:
        if fallback and fallback.strip():
            return fallback.strip()
    return None


def initials(display: str | None) -> str:
    """Up to two initials for an avatar disc: "Ada Lovelace" -> "AL"."""
    words = [w for w in (display or "").split() if w]
    if not words:
        return ""
    first = words[0][0].upper()
    if len(words) < 2:
        return first
    last_char = words[-1][0]
    if last_char.isalnum():
        return first + last_char.upper()
    return first
