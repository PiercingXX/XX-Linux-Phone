"""Unknown-sender fix tests: per-sender known verdicts.

A sender is known when xx-contacts' ``LookupNumber`` returns a
non-empty uid, when EDS knows the number (xx-contacts absent), when
the user has replied to the number, or when it is starred. Lookups
cache for 60 s and Contacts1 ``Changed`` invalidates the cache.
"""

from txxt import blocking
from txxt.blocking import (
    InboundFilter,
    MessageDisposition,
    live_apply,
    live_current,
    live_reset_for_test,
)
from txxt.contacts import ContactHit, ContactsResolver
from txxt.known_senders import CACHE_TTL_S, KnownSenderChecker, install_live_lookup
from txxt.store import Store

ALICE = "+15551234567"
STRANGER = "+19998887777"
NOW = 1_750_000_000_000


class DictBackend:
    """In-memory contacts backend with call counting and failure."""

    def __init__(self, hits=None, fail=False):
        self.hits = dict(hits or {})
        self.fail = fail
        self.lookups = 0

    def lookup(self, number):
        self.lookups += 1
        if self.fail:
            raise OSError("contacts away")
        return self.hits.get(number)

    def search(self, query, limit=50):
        return []

    def set_tags(self, uid, tags):
        return True


def checker_with(hits=None, fail=False, store=None, now=None):
    backend = DictBackend(hits=hits, fail=fail)
    resolver = ContactsResolver([backend])
    checker = KnownSenderChecker(
        resolver=resolver, store=store if store is not None else Store.memory(),
        now=now if now is not None else lambda: 0.0,
    )
    return checker, backend


def test_contact_delivered_stranger_quarantined_when_enabled():
    checker, _ = checker_with(hits={ALICE: ContactHit(ALICE, "uid-alice", "Alice")})
    filt = InboundFilter(
        quarantine_unknown_senders=True, known_sender_lookup=checker.is_known)
    disposition, _ = filt.evaluate(ALICE, "Hello")
    assert disposition is MessageDisposition.DELIVER
    disposition, reason = filt.evaluate(STRANGER, "Hello")
    assert disposition is MessageDisposition.QUARANTINE
    assert reason is not None


def test_quarantine_off_delivers_everyone():
    checker, _ = checker_with(hits={ALICE: ContactHit(ALICE, "uid-alice", "Alice")})
    filt = InboundFilter(known_sender_lookup=checker.is_known)
    assert filt.evaluate(ALICE, "Hi")[0] is MessageDisposition.DELIVER
    assert filt.evaluate(STRANGER, "Hi")[0] is MessageDisposition.DELIVER


def test_xx_contacts_absent_falls_back_to_eds_name():
    eds_like = DictBackend(hits={ALICE: ContactHit(ALICE, "", "Alice")})
    checker = KnownSenderChecker(
        resolver=ContactsResolver([eds_like]), store=Store.memory(),
        now=lambda: 0.0,
    )
    filt = InboundFilter(
        quarantine_unknown_senders=True, known_sender_lookup=checker.is_known)
    assert filt.evaluate(ALICE, "Hi")[0] is MessageDisposition.DELIVER
    assert filt.evaluate(STRANGER, "Hi")[0] is MessageDisposition.QUARANTINE


def test_replied_to_number_is_known():
    store = Store.memory()
    store.persist_outgoing_sms(STRANGER, "you first")
    checker, _ = checker_with(store=store)
    assert checker.is_known(STRANGER) is True
    filt = InboundFilter(
        quarantine_unknown_senders=True, known_sender_lookup=checker.is_known)
    assert filt.evaluate(STRANGER, "reply")[0] is MessageDisposition.DELIVER


def test_inbound_only_thread_is_not_replied_to():
    store = Store.memory()
    store.persist_inbound_sms(STRANGER, "spam?", NOW)
    checker, _ = checker_with(store=store)
    assert checker.is_known(STRANGER) is False


def test_starred_number_is_known():
    store = Store.memory()
    store.star(STRANGER)
    checker, _ = checker_with(store=store)
    assert checker.is_known(STRANGER) is True


def test_backend_failure_falls_back_to_store():
    store = Store.memory()
    store.persist_outgoing_sms(STRANGER, "you first")
    checker, _ = checker_with(fail=True, store=store)
    assert checker.is_known(STRANGER) is True
    assert checker.is_known("+10000000000") is False


def test_empty_sender_is_unknown():
    checker, _ = checker_with()
    assert checker.is_known("") is False
    assert checker.is_known("   ") is False


def test_lookups_cache_for_sixty_seconds():
    assert CACHE_TTL_S == 60
    clock = [1000.0]
    checker, backend = checker_with(
        hits={ALICE: ContactHit(ALICE, "uid-alice", "Alice")},
        now=lambda: clock[0],
    )
    assert checker.is_known(ALICE) is True
    assert checker.is_known(ALICE) is True
    assert backend.lookups == 1
    clock[0] += CACHE_TTL_S + 1
    assert checker.is_known(ALICE) is True
    assert backend.lookups == 2


def test_contacts_changed_invalidates_cache():
    clock = [1000.0]
    checker, backend = checker_with(
        hits={ALICE: ContactHit(ALICE, "uid-alice", "Alice")},
        now=lambda: clock[0],
    )
    assert checker.is_known(ALICE) is True
    assert backend.lookups == 1

    class Params:
        def unpack(self):
            return (42, ["uid-alice"])

    checker.handle_contacts_changed(None, None, None, None, None, Params(), None)
    assert checker.is_known(ALICE) is True
    assert backend.lookups == 2


def test_changed_with_bad_params_still_invalidates():
    checker, backend = checker_with(
        hits={ALICE: ContactHit(ALICE, "uid-alice", "Alice")},
    )
    assert checker.is_known(ALICE) is True

    class Bad:
        def unpack(self):
            raise ValueError("nope")

    checker.handle_contacts_changed(None, None, None, None, None, Bad(), None)
    assert checker.is_known(ALICE) is True
    assert backend.lookups == 2


def test_live_filter_uses_lookup_and_reset_clears_it():
    checker, _ = checker_with(hits={ALICE: ContactHit(ALICE, "uid-alice", "Alice")})
    try:
        live_apply(
            quarantine_unknown_senders=True,
            known_sender_lookup=checker.is_known,
        )
        assert live_current().evaluate(ALICE, "Hi")[0] is MessageDisposition.DELIVER
        assert live_current().evaluate(STRANGER, "Hi")[0] is MessageDisposition.QUARANTINE
    finally:
        live_reset_for_test()
    assert live_current().evaluate(STRANGER, "Hi")[0] is MessageDisposition.DELIVER


def test_install_live_lookup_keeps_existing_rules():
    checker, _ = checker_with(hits={ALICE: ContactHit(ALICE, "uid-alice", "Alice")})
    try:
        live_apply(
            blocked_addresses={"+1222"},
            quarantine_unknown_senders=True,
        )
        install_live_lookup(checker)
        assert live_current().evaluate("+1222", "Hi")[0] is MessageDisposition.BLOCK
        assert live_current().evaluate(ALICE, "Hi")[0] is MessageDisposition.DELIVER
        assert live_current().evaluate(STRANGER, "Hi")[0] is MessageDisposition.QUARANTINE
    finally:
        live_reset_for_test()


def test_lookup_exception_fails_open_to_deliver():
    def _boom(sender):
        raise RuntimeError("cache corrupt")

    filt = InboundFilter(
        quarantine_unknown_senders=True, known_sender_lookup=_boom)
    assert filt.evaluate(STRANGER, "Hi")[0] is MessageDisposition.DELIVER


def test_block_toggle_preserves_other_tags():
    seen = {}

    class TagBus:
        def __init__(self):
            self.tags = {"uid-alice": ["starred", "business"]}

        def lookup(self, number):
            if number == ALICE:
                return ContactHit(ALICE, "uid-alice", "Alice",
                                  tuple(self.tags["uid-alice"]))
            return None

        def search(self, query, limit=50):
            return []

        def set_tags(self, uid, tags):
            seen[uid] = list(tags)
            self.tags[uid] = list(tags)
            return True

    from txxt.contacts import ContactsBackend

    class Backend(ContactsBackend):
        def __init__(self, bus):
            self._bus = bus

        def lookup(self, number):
            return self._bus.lookup(number)

        def search(self, query, limit=50):
            return []

        def set_tags(self, uid, tags):
            return self._bus.set_tags(uid, tags)

    backend = Backend(TagBus())
    resolver = ContactsResolver([backend])
    assert resolver.set_blocked(ALICE, True) is True
    assert sorted(seen["uid-alice"]) == ["blocked", "business", "starred"]
    assert resolver.set_blocked(ALICE, False) is True
    assert sorted(seen["uid-alice"]) == ["business", "starred"]
    assert resolver.set_blocked("+10000000000", True) is False
    assert blocking.live_current() is not None
