"""Group policy. Port of GroupChoices.kt + ContactGroups.kt naming rules."""

from __future__ import annotations

from dataclasses import dataclass


STAR = "Star"
BUSINESS = "Business"
BLOCKED = "Blocked"
CREATE = "Create new"


@dataclass(frozen=True)
class GroupChoice:
    kind: str
    name: str


def normalize(raw: str) -> str:
    return raw.strip()


def reserved_kind(name: str) -> str | None:
    match normalize(name).lower():
        case "star":
            return "star"
        case "business":
            return "business"
        case "block" | "blocked":
            return "blocked"
        case _:
            return None


def is_creatable(name: str) -> bool:
    return bool(normalize(name)) and reserved_kind(name) is None


def stored_name(name: str) -> str | None:
    cleaned = normalize(name)
    if not cleaned:
        return None
    kind = reserved_kind(cleaned)
    if kind == "blocked":
        return BLOCKED
    if kind is not None:
        return None
    return cleaned


def chooser_list(custom_names: list[str]) -> list[GroupChoice]:
    customs = [
        GroupChoice("custom", n.strip())
        for n in custom_names
        if n.strip() and not n.strip().lower() == "blocked"
    ]
    return (
        [GroupChoice("star", STAR), GroupChoice("business", BUSINESS),
         GroupChoice("blocked", BLOCKED)]
        + customs
        + [GroupChoice("create", CREATE)]
    )
