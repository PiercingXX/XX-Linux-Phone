"""Contact resolution via xx-contacts, EDS fallback, bare numbers last.

Owner directive (review note 5): names, starred and blocked come from
xx-contacts' ``io.piercingxx.Contacts1`` D-Bus API (``LookupNumber``,
``Search`` for the recipient picker, ``SetTags``, ``Changed`` signal).
When xx-contacts is not installed, resolution falls back to read-only
EDS lookups, then to the bare number — never blank (same never-silent
rule as Android ``PermissionGate``).

Real shapes, read from ``ref/xx-contacts/src/xx_contacts/service.py``
(introspection XML + handlers) — never assumed:

- bus name ``io.piercingxx.Contacts``, object
  ``/io/piercingxx/Contacts``, interface ``io.piercingxx.Contacts1``
- ``LookupNumber(in s number)`` -> ``(s uid, s display_name, as tags)``;
  an unknown number returns ``("", "", [])`` — no error — so the
  backend reports it as None and the resolver falls through to EDS
- ``Search(in s query, in u limit)`` -> ``(a(sss))`` rows of
  ``(uid, display_name, primary_number)``; an empty query matches
  nothing (``[]``)
- ``UpdateTags(in s uid, in as add, in as remove)`` -> ``(t revision)``
  applies one atomic add/remove transaction, so two clients toggling
  different tags cannot lose each other's change (unlike Get +
  SetTags). Every tag write below goes through ``UpdateTags``;
  ``SetTags`` survives only as the low-level replace call
- ``Changed(t revision, as uids)`` signal
"""

from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

CONTACTS_BUS_NAME = "io.piercingxx.Contacts"
CONTACTS_OBJECT_PATH = "/io/piercingxx/Contacts"
CONTACTS_IFACE = "io.piercingxx.Contacts1"

SEARCH_DEFAULT_LIMIT = 50

TAG_STARRED = "starred"
TAG_BLOCKED = "blocked"


@dataclass(frozen=True)
class ContactHit:
    """One resolved number: its contact uid, display name and tags."""

    number: str
    uid: str = ""
    name: str = ""
    tags: tuple[str, ...] = ()

    @property
    def display(self) -> str:
        """Name for UI surfaces; never blank — falls back to formatted number."""
        if self.name.strip():
            return self.name.strip()
        try:
            from txxt import phone_numbers as _phones

            formatted = _phones.format_display_configured(self.number)
        except Exception:
            formatted = ""
        return formatted or self.number

    @property
    def starred(self) -> bool:
        return TAG_STARRED in self.tags

    @property
    def blocked(self) -> bool:
        return TAG_BLOCKED in self.tags


@dataclass
class SearchHit:
    """One recipient-picker row: ``(uid, display_name, primary_number)``."""

    uid: str
    name: str
    number: str

    @property
    def display(self) -> str:
        return self.name.strip() or self.number


class ContactsBackend:
    """Seam every resolver talks to: lookup / search / tag writes."""

    def lookup(self, number: str) -> ContactHit | None:
        raise NotImplementedError

    def search(self, query: str, limit: int = SEARCH_DEFAULT_LIMIT) -> list[SearchHit]:
        raise NotImplementedError

    def set_tags(self, uid: str, tags: list[str]) -> bool:
        raise NotImplementedError

    def update_tags(self, uid: str, add: list[str], remove: list[str]) -> bool:
        """Atomically add/remove tags for ``uid`` (``UpdateTags``).

        The default falls back to a read-modify-write through
        :meth:`set_tags` for backends with no atomic call; the xx-contacts
        backend overrides it with the real transaction.
        """
        raise NotImplementedError

    def _toggle_tag(self, number: str, tag: str, enabled: bool) -> bool:
        """Flip one tag for ``number`` via :meth:`update_tags`.

        The uid still resolves through :meth:`lookup`, but the write
        itself is one atomic call — concurrent toggles of different tags
        cannot clobber each other. False when the number has no uid.
        """
        try:
            hit = self.lookup(number)
        except Exception:
            return False
        if hit is None or not hit.uid:
            return False
        add = [tag] if enabled else []
        remove = [] if enabled else [tag]
        try:
            return bool(self.update_tags(hit.uid, add, remove))
        except NotImplementedError:
            pass
        try:
            current = self.lookup(number)
        except Exception:
            return False
        tags = set(current.tags) if current is not None else set()
        if enabled:
            tags.add(tag)
        else:
            tags.discard(tag)
        try:
            return bool(self.set_tags(hit.uid, sorted(tags)))
        except Exception:
            return False

    def set_starred(self, number: str, starred: bool) -> bool:
        """Toggle ``"starred"`` for ``number``, keeping every other tag."""
        return self._toggle_tag(number, TAG_STARRED, starred)

    def set_blocked(self, number: str, blocked: bool) -> bool:
        """Toggle ``"blocked"`` for ``number``, keeping every other tag."""
        return self._toggle_tag(number, TAG_BLOCKED, blocked)


class GioContactsBackend(ContactsBackend):
    """xx-contacts over D-Bus. ``bus`` is injected (real session bus in
    production, a fake in tests); replies unpack as tuples of out-args —
    ``LookupNumber`` unpacks to 3 values directly, ``Search`` to 1."""

    def __init__(self, bus, timeout_ms: int = 5_000) -> None:
        self._bus = bus
        self._timeout_ms = timeout_ms

    def lookup(self, number: str) -> ContactHit | None:
        try:
            reply = self._bus.call_sync(
                CONTACTS_BUS_NAME, CONTACTS_OBJECT_PATH, CONTACTS_IFACE,
                "LookupNumber", GLib.Variant("(s)", (number,)), None,
                Gio.DBusCallFlags.NONE, self._timeout_ms, None,
            )
            uid, name, tags = reply.unpack()
        except Exception:
            return None
        if not uid:
            return None
        return ContactHit(
            number=number, uid=str(uid),
            name=str(name), tags=tuple(str(t) for t in tags),
        )

    def search(
        self, query: str, limit: int = SEARCH_DEFAULT_LIMIT,
    ) -> list[SearchHit]:
        try:
            reply = self._bus.call_sync(
                CONTACTS_BUS_NAME, CONTACTS_OBJECT_PATH, CONTACTS_IFACE,
                "Search", GLib.Variant("(su)", (query, int(limit))), None,
                Gio.DBusCallFlags.NONE, self._timeout_ms, None,
            )
            rows = reply.unpack()[0]
        except Exception:
            return []
        return [
            SearchHit(uid=str(uid), name=str(name), number=str(number))
            for uid, name, number in rows
        ]

    def set_tags(self, uid: str, tags: list[str]) -> bool:
        try:
            self._bus.call_sync(
                CONTACTS_BUS_NAME, CONTACTS_OBJECT_PATH, CONTACTS_IFACE,
                "SetTags",
                GLib.Variant("(sas)", (uid, [str(t) for t in tags])), None,
                Gio.DBusCallFlags.NONE, self._timeout_ms, None,
            )
        except Exception:
            return False
        return True

    def update_tags(self, uid: str, add: list[str], remove: list[str]) -> bool:
        """One atomic ``UpdateTags`` transaction; revision is discarded."""
        try:
            reply = self._bus.call_sync(
                CONTACTS_BUS_NAME, CONTACTS_OBJECT_PATH, CONTACTS_IFACE,
                "UpdateTags",
                GLib.Variant("(sasas)", (
                    uid, [str(t) for t in add], [str(t) for t in remove],
                )), None,
                Gio.DBusCallFlags.NONE, self._timeout_ms, None,
            )
            reply.unpack()[0]
        except Exception:
            return False
        return True

    def subscribe_changed(self, handler) -> int | None:
        """Subscribe to ``Changed(t revision, as uids)`` (7-arg handler)."""
        try:
            return self._bus.signal_subscribe(
                CONTACTS_BUS_NAME, CONTACTS_IFACE, "Changed", None,
                None, Gio.DBusSignalFlags.NONE, handler, None,
            )
        except Exception:
            return None


class EdsFallbackBackend(ContactsBackend):
    """Read-only EDS lookup when xx-contacts is absent.

    EDS access is lazy (``EBook`` may not be installed) and strictly
    read-only: names only, never tags. Any failure resolves to None so
    the caller falls through to bare numbers.
    """

    def lookup(self, number: str) -> ContactHit | None:
        try:
            from txxt.contacts_eds import eds_lookup_name
        except Exception:
            return None
        try:
            name = eds_lookup_name(number)
        except Exception:
            return None
        if not name:
            return None
        return ContactHit(number=number, name=name)

    def search(
        self, query: str, limit: int = SEARCH_DEFAULT_LIMIT,
    ) -> list[SearchHit]:
        return []

    def set_tags(self, uid: str, tags: list[str]) -> bool:
        return False

    def update_tags(self, uid: str, add: list[str], remove: list[str]) -> bool:
        return False


class ContactsResolver:
    """xx-contacts first, EDS read-only second, formatted numbers last."""

    def __init__(self, backends: list[ContactsBackend] | None = None) -> None:
        self._backends: list[ContactsBackend] = list(backends or [])

    def lookup(self, number: str) -> ContactHit:
        """Resolve ``number``; always returns a hit (bare number at worst)."""
        for backend in self._backends:
            try:
                hit = backend.lookup(number)
            except Exception:
                continue
            if hit is not None:
                return hit
        return ContactHit(number=number)

    def display_name(self, number: str) -> str:
        """Never blank: contact name when known, else the formatted number."""
        try:
            hit = self.lookup(number)
        except Exception:
            hit = None
        if hit is not None:
            name = (hit.name or "").strip()
            if name:
                return name
        try:
            from txxt import phone_numbers as _phones

            formatted = _phones.format_display_configured(number)
        except Exception:
            formatted = ""
        return formatted or number

    def is_starred(self, number: str) -> bool:
        try:
            return self.lookup(number).starred
        except Exception:
            return False

    def is_blocked(self, number: str) -> bool:
        try:
            return self.lookup(number).blocked
        except Exception:
            return False

    def search(
        self, query: str, limit: int = SEARCH_DEFAULT_LIMIT,
    ) -> list[SearchHit]:
        for backend in self._backends:
            try:
                hits = backend.search(query, limit)
            except Exception:
                continue
            if hits:
                return hits
        return []

    def set_starred(self, number: str, starred: bool) -> bool:
        """Toggle ``"starred"`` via the first backend that holds the number."""
        for backend in self._backends:
            try:
                if backend.set_starred(number, starred):
                    return True
            except Exception:
                continue
        return False

    def set_blocked(self, number: str, blocked: bool) -> bool:
        """Toggle ``"blocked"`` via the first backend that holds the number."""
        for backend in self._backends:
            try:
                if backend.set_blocked(number, blocked):
                    return True
            except Exception:
                continue
        return False

    def known_numbers(self, local: set[str] | frozenset[str] = frozenset()) -> set[str]:
        """Numbers the resolver layer knows (for the unknown-sender rule).

        Unions ``local`` (starred + conversation participants held in
        SQLite) with whatever an empty-query ``Search`` returns — the
        real service matches nothing on an empty query, so this
        degrades honestly to local knowledge there.
        """
        known = set(local)
        for backend in self._backends:
            try:
                for hit in backend.search(""):
                    if hit.number.strip():
                        known.add(hit.number)
            except Exception:
                continue
        return known


class DisplayNameCache:
    """60 s TTL display-name cache with Contacts1 Changed invalidation.

    The UI renders first from the fast path (cached name or formatted
    number, never D-Bus) so a D-Bus-activated contacts service (~1 s
    first call) never blocks the list render. Misses resolve on a worker
    thread and post back via the caller's idle callback.
    """

    def __init__(self, resolver=None, now=None, ttl_s: float = 60) -> None:
        self._resolver = resolver
        self._ttl = float(ttl_s)
        try:
            import time as _time

            self._now = now or _time.monotonic
        except Exception:
            self._now = now or (lambda: 0.0)
        self._cache: dict[str, tuple[str, float]] = {}

    def set_resolver(self, resolver) -> None:
        self._resolver = resolver

    @staticmethod
    def _key(number: str) -> str:
        try:
            from txxt import phone_numbers as _phones

            key = _phones.conversation_key(number)
            if key:
                return key
        except Exception:
            pass
        return (number or "").strip()

    def fast(self, number: str) -> str:
        key = self._key(number)
        if key:
            cached = self._cache.get(key)
            if cached is not None:
                name, stamp = cached
                try:
                    fresh = (self._now() - stamp) < self._ttl
                except Exception:
                    fresh = True
                if fresh and name.strip():
                    return name
        try:
            from txxt import phone_numbers as _phones

            formatted = _phones.format_display_number(number)
        except Exception:
            formatted = ""
        return formatted or number

    def resolve_blocking(self, number: str) -> str:
        try:
            display = self._resolver.display_name(number) if self._resolver else number
        except Exception:
            display = number
        if not (display or "").strip():
            display = number
        try:
            self._cache[self._key(number)] = (display, self._now())
        except Exception:
            pass
        return display

    def invalidate(self, number: str | None = None) -> None:
        if number is None:
            self._cache.clear()
            return
        self._cache.pop(self._key(number), None)

    def handle_contacts_changed(
        self, _conn, _sender, _path, _iface, _signal, params, _user_data,
    ) -> None:
        try:
            params.unpack()
        except Exception:
            pass
        self.invalidate()


def session_resolver(session_bus=None) -> ContactsResolver:
    """Production resolver: xx-contacts when present, EDS fallback after."""
    backends: list[ContactsBackend] = []
    if session_bus is not None:
        backends.append(GioContactsBackend(session_bus))
    backends.append(EdsFallbackBackend())
    return ContactsResolver(backends)


def tags_for_display() -> tuple[str, ...]:
    """Tag vocabulary the resolver understands."""
    return (TAG_STARRED, TAG_BLOCKED)
