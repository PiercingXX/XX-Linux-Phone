"""Per-sender known-contact check for the unknown-sender rule.

The production live filter used to consult an (empty) known-numbers
set, so enabling "quarantine unknown senders" quarantined every
message — including messages from contacts. A set can never be right
here: the contact list is owned by xx-contacts and changes under us.

A sender is known when any of these holds:

- xx-contacts' ``LookupNumber`` returns a non-empty uid for the number,
- the number matches an EDS address-book name (the resolver's second
  backend, consulted automatically when xx-contacts is absent), or
- the number appears in a local conversation the user has replied to
  (an outgoing message in a matching thread), or
- it is starred (local starred table or the contact's tags).

Lookups cache briefly (60 s) and the Contacts1 ``Changed`` signal
invalidates the cache. No GTK imports: headless-testable.
"""

from __future__ import annotations

import time

from txxt import phone_numbers

#: How long a per-sender verdict stays cached (seconds).
CACHE_TTL_S = 60


class KnownSenderChecker:
    """Live known/unknown verdicts over a contacts resolver and store."""

    def __init__(self, resolver=None, store=None, now=None) -> None:
        self._resolver = resolver
        self._store = store
        self._now = now or time.monotonic
        self._cache: dict[str, tuple[bool, float]] = {}

    def is_known(self, sender: str) -> bool:
        """True when ``sender`` is an established correspondent."""
        key = phone_numbers.conversation_key(sender) or (sender or "").strip()
        if not key:
            return False
        cached = self._cache.get(key)
        if cached is not None and self._now() - cached[1] < CACHE_TTL_S:
            return cached[0]
        verdict = self._lookup(sender)
        self._cache[key] = (verdict, self._now())
        return verdict

    def invalidate(self, number: str | None = None) -> None:
        """Drop cached verdicts: one number, or everything when unknown."""
        if number is None:
            self._cache.clear()
            return
        key = phone_numbers.conversation_key(number) or number.strip()
        self._cache.pop(key, None)

    def handle_contacts_changed(
        self, _conn, _sender, _path, _iface, _signal, params, _user_data,
    ) -> None:
        """Contacts1 ``Changed`` handler (7-arg shape): drop the cache."""
        try:
            _revision, _uids = params.unpack()
        except Exception:
            pass
        self.invalidate()

    def _lookup(self, sender: str) -> bool:
        if self._contacts_know(sender):
            return True
        if self._store is not None:
            try:
                if self._store.is_starred(sender):
                    return True
            except Exception:
                pass
            try:
                if self._replied_to(sender):
                    return True
            except Exception:
                pass
        try:
            starred = (
                self._resolver.is_starred(sender)
                if self._resolver is not None else False
            )
        except Exception:
            starred = False
        return bool(starred)

    def _contacts_know(self, sender: str) -> bool:
        if self._resolver is None:
            return False
        try:
            hit = self._resolver.lookup(sender)
        except Exception:
            return False
        if hit is None:
            return False
        if getattr(hit, "uid", ""):
            return True
        name = (getattr(hit, "name", "") or "").strip()
        return bool(name) and name != (sender or "").strip()

    def _replied_to(self, sender: str) -> bool:
        """An outgoing message in a thread holding ``sender``."""
        for conversation in self._store.get_all_conversations():
            parts = [
                part for part in conversation.addresses() if part.strip()
            ]
            if not any(
                phone_numbers.matches(part, sender) for part in parts
            ):
                continue
            try:
                messages = self._store.get_messages_for_conversation(
                    conversation.id
                )
            except Exception:
                continue
            for message in messages or []:
                if (message.direction or "").upper() == "OUTGOING":
                    return True
        return False


def install_live_lookup(checker: KnownSenderChecker) -> None:
    """Point the process-wide live filter at ``checker``'s verdicts."""
    from txxt import blocking

    state = (
        blocking._live_state.keywords,
        blocking._live_state.phrases,
        blocking._live_state.blocked,
        blocking._live_state.starred,
        blocking._live_state.quarantine_unknown_senders,
    )
    blocking.live_apply(
        keywords=set(state[0]),
        phrases=set(state[1]),
        blocked_addresses=set(state[2]),
        starred_contacts=set(state[3]),
        quarantine_unknown_senders=bool(state[4]),
        known_sender_lookup=checker.is_known,
    )
