"""L2 list/search logic without GTK: sections, jump strip, debounce, URIs.

The GTK layer (``list_ui``) runs :func:`fetch_rows` in a worker thread and
posts results with ``GLib.idle_add``; every helper here is pure so pytest
runs headless. Blocked contacts stay search-only per ``visibility``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from xx_contacts.store import NotFoundError
from xx_contacts.visibility import show_on_list

SEARCH_DEBOUNCE_MS = 150
SEARCH_RESULT_LIMIT = 200
JUMP_MIN_TOUCH_PX = 40
STAR_JUMP_LABEL = "*"
STARRED_SECTION_KEY = "starred"
OTHER_SECTION_KEY = "#"


@dataclass(frozen=True)
class ListRow:
    uid: str = ""
    display_name: str = ""
    primary_number: str = ""
    primary_email: str = ""
    starred: bool = False
    blocked: bool = False
    has_phone: bool = False
    photo_path: str = ""


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    rows: tuple = ()


@dataclass(frozen=True)
class SectionHeader:
    title: str


@dataclass(frozen=True)
class ListFilter:
    has_phone_only: bool = False
    starred_only: bool = False


@dataclass(frozen=True)
class RefreshPlan:
    refresh: bool
    interesting: tuple = ()


def section_key(display_name: str | None) -> str:
    """First-letter bucket for a display name; non-letters go to ``#``."""
    for char in (display_name or "").strip():
        if char.isalpha():
            return char.upper()
        return OTHER_SECTION_KEY
    return OTHER_SECTION_KEY


def _row_sort_key(row: ListRow) -> tuple:
    return (row.display_name.casefold(), row.uid)


def build_sections(rows: list[ListRow]) -> list[Section]:
    """Starred section first, then A-Z, with ``#`` last; rows sorted."""
    starred = sorted((r for r in rows if r.starred), key=_row_sort_key)
    buckets: dict[str, list[ListRow]] = {}
    for row in rows:
        if row.starred:
            continue
        buckets.setdefault(section_key(row.display_name), []).append(row)
    sections: list[Section] = []
    if starred:
        sections.append(Section(STARRED_SECTION_KEY, "Starred",
                                tuple(starred)))
    for key in sorted(buckets, key=lambda k: (k == OTHER_SECTION_KEY, k)):
        sections.append(Section(key, key,
                                tuple(sorted(buckets[key],
                                             key=_row_sort_key))))
    return sections


def jump_labels(sections: list[Section]) -> list[str]:
    """Jump-strip labels in section order; starred renders as ``*``."""
    return [STAR_JUMP_LABEL if s.key == STARRED_SECTION_KEY else s.title
            for s in sections]


def flatten_sections(sections: list[Section]
                     ) -> list[ListRow | SectionHeader]:
    """Single list of headers + rows for a ``Gio.ListStore`` model."""
    flat: list[ListRow | SectionHeader] = []
    for section in sections:
        flat.append(SectionHeader(section.title))
        flat.extend(section.rows)
    return flat


def apply_filters(rows: list[ListRow], query: str | None,
                  list_filter: ListFilter | None = None) -> list[ListRow]:
    """Keep has-phone matches; blocked contacts only surface in search."""
    wanted = list_filter or ListFilter()
    kept: list[ListRow] = []
    for row in rows:
        if wanted.has_phone_only and not row.has_phone:
            continue
        if wanted.starred_only and not row.starred:
            continue
        if not show_on_list(row.blocked, query):
            continue
        kept.append(row)
    return kept


class SearchDebouncer:
    """Release a typed query once it has been stable for the interval."""

    def __init__(self, interval_ms: int = SEARCH_DEBOUNCE_MS) -> None:
        self.interval_ms = max(0, int(interval_ms))
        self._pending: str | None = None
        self._edit_at_ms = 0
        self._sent: str | None = None

    def edit(self, query: str, now_ms: int) -> None:
        self._pending = query or ""
        self._edit_at_ms = int(now_ms)

    @property
    def has_unsent(self) -> bool:
        return self._pending is not None and self._pending != self._sent

    def due(self, now_ms: int) -> str | None:
        if not self.has_unsent or self._pending is None:
            return None
        if int(now_ms) - self._edit_at_ms < self.interval_ms:
            return None
        self._sent = self._pending
        return self._sent


def _compact_number(number: str) -> str:
    return re.sub(r"\s+", "", number or "")


def call_uri(number: str) -> str:
    """``tel:`` URI for a raw number, or ``""`` when there is no number."""
    compact = _compact_number(number)
    return f"tel:{compact}" if compact else ""


def message_uri(number: str) -> str:
    """``sms:`` URI for a raw number, or ``""`` when there is no number."""
    compact = _compact_number(number)
    return f"sms:{compact}" if compact else ""


def email_uri(address: str) -> str:
    """``mailto:`` URI for an address, or ``""`` when there is none."""
    cleaned = (address or "").strip()
    return f"mailto:{cleaned}" if cleaned else ""


def row_menu(row: ListRow) -> list[str]:
    """Row actions: call/message need a number, email needs an address."""
    actions: list[str] = []
    if row.primary_number:
        actions += ["call", "message"]
    if row.primary_email:
        actions += ["email"]
    actions += ["unstar" if row.starred else "star"]
    actions += ["unblock" if row.blocked else "block"]
    return actions + ["share", "delete"]


def action_uri(action: str, row: ListRow) -> str:
    """Outbound URI for a row action, or ``""`` for store-only actions."""
    if action == "call":
        return call_uri(row.primary_number)
    if action == "message":
        return message_uri(row.primary_number)
    if action == "email":
        return email_uri(row.primary_email)
    return ""


def plan_refresh(known_revision: int, notice_revision: int,
                 notice_uids: list[str] | tuple[str, ...],
                 visible_uids: list[str] | tuple[str, ...] = ()) -> RefreshPlan:
    """Decide whether a ``Changed`` notice needs a list reload.

    Stale notices (at or below the revision already shown) need nothing;
    anything newer reloads, hinting which visible rows are affected.
    """
    if int(notice_revision) <= int(known_revision):
        return RefreshPlan(False, ())
    visible = set(visible_uids or ())
    interesting = tuple(u for u in notice_uids if u in visible)
    return RefreshPlan(True, interesting)


def record_to_row(record) -> ListRow:
    """Project a store record onto the fields the list renders."""
    number = record.phones[0].number_raw if record.phones else ""
    address = record.emails[0].address if record.emails else ""
    name = record.display_name or number or address or "Unnamed"
    return ListRow(uid=record.uid, display_name=name, primary_number=number,
                   primary_email=address, starred=bool(record.starred),
                   blocked=bool(record.blocked),
                   has_phone=bool(record.phones),
                   photo_path=getattr(record, "photo_path", "") or "")


def fetch_rows(store, query: str, limit: int = SEARCH_RESULT_LIMIT,
               list_filter: ListFilter | None = None) -> list[ListRow]:
    """Load list rows off the GTK thread: store search, then filters.

    An empty query lists every contact; a typed query goes through the
    store's search so name/number/email ranking stays in one place.
    """
    text = (query or "").strip()
    rows: list[ListRow] = []
    if text:
        for uid, _name, _primary in store.search(text, limit):
            try:
                rows.append(record_to_row(store.get(uid)))
            except NotFoundError:
                continue
    else:
        rows = [record_to_row(record) for record in store.all_records()]
    return apply_filters(rows, text, list_filter)
