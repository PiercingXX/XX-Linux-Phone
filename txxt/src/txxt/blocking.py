"""Inbound blocking engine.

Pure-Python port of the Android blocking stack:

- ``core/BlockingFilter.kt`` -> :class:`BlockingFilter`
- ``core/Blocklist.kt`` -> :class:`Blocklist`
- ``core/StarredBypass.kt`` -> :class:`StarredBypass`
- ``core/UnknownSenderRule.kt`` -> :class:`UnknownSenderRule`
- ``core/BlockDecision.kt`` -> :class:`BlockDecision`
- ``block/MessageDisposition.kt`` -> :class:`MessageDisposition`
- ``block/BlockReason.kt`` -> :class:`BlockReason`
- ``block/BlockOverrideStore.kt`` -> :class:`BlockOverrideStore`
- ``block/InboundFilter.kt`` -> :class:`InboundFilter`
- ``block/LiveInboundFilter.kt`` -> process-wide holder below

Decision order (:meth:`InboundFilter.evaluate`): per-sender override first,
then the blocked-address list, then the content filter (keyword/phrase),
then the unknown-sender rule (quarantine only when opted in, default off so
the factory posture is fail-open delivery), otherwise deliver. Starred
contacts bypass every suppression and surface a can-override reason instead
of being blocked silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from txxt import phone_numbers


class BlockingFilter:
    """Keyword/phrase content filter over a message body.

    A keyword matches as a whole word (case-insensitive), so "loan" matches
    "loan offers" but not "loans". A phrase matches as a substring
    (case-insensitive). :meth:`matched_term` reports keyword-then-phrase in
    sorted order so the surfaced reason is deterministic.
    """

    def __init__(
        self,
        keywords: set[str] | frozenset[str] = frozenset(),
        phrases: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        self._keywords = set(keywords)
        self._phrases = set(phrases)

    def matches(self, text: str) -> bool:
        return self.matched_term(text) is not None

    def matched_term(self, text: str) -> str | None:
        lowered = (text or "").lower()
        for keyword in sorted(self._keywords):
            if _contains_word(lowered, keyword.lower()):
                return keyword
        for phrase in sorted(self._phrases):
            if phrase.lower() in lowered:
                return phrase
        return None


def _contains_word(text: str, word: str) -> bool:
    if not word:
        return False
    start = 0
    while True:
        index = text.find(word, start)
        if index < 0:
            return False
        before_ok = index == 0 or not text[index - 1].isalnum()
        after = index + len(word)
        after_ok = after == len(text) or not text[after].isalnum()
        if before_ok and after_ok:
            return True
        start = index + 1


class Blocklist:
    """Value type holding keyword and phrase blocking rules."""

    def __init__(
        self,
        keywords: set[str] | frozenset[str] = frozenset(),
        phrases: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        self._keywords = set(keywords)
        self._phrases = set(phrases)

    def add_keyword(self, term: str) -> "Blocklist":
        self._keywords.add(term)
        return self

    def add_phrase(self, term: str) -> "Blocklist":
        self._phrases.add(term)
        return self

    def remove_keyword(self, term: str) -> "Blocklist":
        self._keywords.discard(term)
        return self

    def remove_phrase(self, term: str) -> "Blocklist":
        self._phrases.discard(term)
        return self

    def keywords(self) -> set[str]:
        return set(self._keywords)

    def phrases(self) -> set[str]:
        return set(self._phrases)

    def is_blocked(self, term: str) -> bool:
        return term in self._keywords or term in self._phrases


class StarredBypass:
    """Starred contacts bypass every suppression."""

    def __init__(self, starred_contacts: set[str] | frozenset[str] = frozenset()) -> None:
        self._starred = set(starred_contacts)

    def is_starred(self, sender: str) -> bool:
        return any(phone_numbers.matches(sender, contact) for contact in self._starred)

    def bypasses(self, sender: str) -> bool:
        return self.is_starred(sender)

    def reason(self, sender: str) -> str | None:
        if self.is_starred(sender):
            return "Starred contacts bypass every suppression"
        return None


class UnknownSenderRule:
    """Unknown senders (not among known contacts) are held, never shown."""

    def __init__(self, known_contacts: set[str] | frozenset[str] = frozenset()) -> None:
        self._known = set(known_contacts)

    def is_unknown(self, sender: str) -> bool:
        return not self.is_known(sender)

    def is_known(self, sender: str) -> bool:
        return any(phone_numbers.matches(sender, contact) for contact in self._known)

    def reason(self, sender: str) -> str | None:
        if self.is_unknown(sender):
            return "Sender is not a known contact"
        return None


class BlockDecision:
    """Combines the content filter and unknown-sender rule with the bypass."""

    def __init__(
        self,
        blocking_filter: BlockingFilter,
        unknown_sender_rule: UnknownSenderRule,
        starred_bypass: StarredBypass,
    ) -> None:
        self._filter = blocking_filter
        self._unknown = unknown_sender_rule
        self._starred = starred_bypass

    def should_block(self, sender: str, body: str) -> bool:
        return not self._starred.is_starred(sender) and self._matching_reason(sender, body) is not None

    def reason(self, sender: str, body: str) -> str | None:
        rule_reason = self._matching_reason(sender, body)
        if rule_reason is None:
            return None
        if self._starred.is_starred(sender):
            return f"Block would match starred contact: {rule_reason}"
        return rule_reason

    def _matching_reason(self, sender: str, body: str) -> str | None:
        term = self._filter.matched_term(body)
        if term is not None:
            return f"Blocked term: {term}"
        return self._unknown.reason(sender)


class MessageDisposition(Enum):
    DELIVER = "DELIVER"
    QUARANTINE = "QUARANTINE"
    BLOCK = "BLOCK"


class BlockReasonType(Enum):
    UNKNOWN_SENDER = "UNKNOWN_SENDER"
    BLOCKED_LIST = "BLOCKED_LIST"
    STARRED_CONTACT_RULE = "STARRED_CONTACT_RULE"


@dataclass(frozen=True)
class BlockReason:
    type: BlockReasonType
    message: str
    can_override: bool = False

    @staticmethod
    def unknown_sender() -> "BlockReason":
        return BlockReason(BlockReasonType.UNKNOWN_SENDER, "Sender is not in your contacts")

    @staticmethod
    def blocked_list() -> "BlockReason":
        return BlockReason(BlockReasonType.BLOCKED_LIST, "Sender is on your block list")

    @staticmethod
    def starred_contact_rule(rule_name: str) -> "BlockReason":
        return BlockReason(
            BlockReasonType.STARRED_CONTACT_RULE,
            f'Rule "{rule_name}" matched a starred contact',
            can_override=True,
        )


class DictKeyValueStore:
    """In-memory key/value backing for :class:`BlockOverrideStore`."""

    def __init__(self) -> None:
        self._sets: dict[str, set[str]] = {}

    def get_string_set(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))

    def put_string_set(self, key: str, value: set[str]) -> None:
        self._sets[key] = set(value)


class BlockOverrideStore:
    """Per-sender "allow this sender" overrides that win over every rule."""

    KEY_OVERRIDES = "block_overrides"

    def __init__(self, kv: DictKeyValueStore | None = None) -> None:
        self._kv = kv if kv is not None else DictKeyValueStore()

    def has_override(self, sender: str) -> bool:
        return any(
            phone_numbers.matches(sender, stored)
            for stored in self._kv.get_string_set(self.KEY_OVERRIDES)
        )

    def add_override(self, sender: str) -> None:
        overrides = self._kv.get_string_set(self.KEY_OVERRIDES)
        overrides.add(phone_numbers.normalize(sender))
        self._kv.put_string_set(self.KEY_OVERRIDES, overrides)

    def remove_override(self, sender: str) -> None:
        remaining = {
            stored
            for stored in self._kv.get_string_set(self.KEY_OVERRIDES)
            if not phone_numbers.matches(sender, stored)
        }
        self._kv.put_string_set(self.KEY_OVERRIDES, remaining)


class InboundFilter:
    """App-layer inbound filter mapping rules to a disposition + reason."""

    def __init__(
        self,
        known_contacts: set[str] | frozenset[str] = frozenset(),
        blocked_addresses: set[str] | frozenset[str] = frozenset(),
        starred_contacts: set[str] | frozenset[str] = frozenset(),
        content_keywords: set[str] | frozenset[str] = frozenset(),
        content_phrases: set[str] | frozenset[str] = frozenset(),
        block_override_store: BlockOverrideStore | None = None,
        quarantine_unknown_senders: bool = False,
        known_contacts_provider: object = None,
        known_sender_lookup: object = None,
    ) -> None:
        self._filter = BlockingFilter(content_keywords, content_phrases)
        self._starred = StarredBypass(starred_contacts)
        self._blocked = set(blocked_addresses)
        self._overrides = block_override_store
        self.quarantine_unknown_senders = quarantine_unknown_senders
        snapshot = set(known_contacts)
        provider = known_contacts_provider
        self._known_provider = provider if callable(provider) else lambda: snapshot
        self._known_lookup = known_sender_lookup if callable(known_sender_lookup) else None

    def evaluate(self, sender: str, body: str) -> tuple[MessageDisposition, BlockReason | None]:
        if self._overrides is not None and self._overrides.has_override(sender):
            return MessageDisposition.DELIVER, None

        if self._is_blocked_address(sender):
            if self._starred.is_starred(sender):
                return (
                    MessageDisposition.DELIVER,
                    BlockReason.starred_contact_rule("blocked-address-list"),
                )
            return MessageDisposition.BLOCK, BlockReason.blocked_list()

        term = self._filter.matched_term(body or "")
        if term is not None:
            if self._starred.is_starred(sender):
                return (
                    MessageDisposition.DELIVER,
                    BlockReason.starred_contact_rule("content-filter"),
                )
            return MessageDisposition.BLOCK, BlockReason(
                BlockReasonType.BLOCKED_LIST,
                f"Message matched content filter: {term}",
            )

        if self._is_unknown(sender):
            if self._starred.is_starred(sender):
                return (
                    MessageDisposition.DELIVER,
                    BlockReason.starred_contact_rule("unknown-sender"),
                )
            if self.quarantine_unknown_senders:
                return MessageDisposition.QUARANTINE, BlockReason.unknown_sender()
            return MessageDisposition.DELIVER, None

        return MessageDisposition.DELIVER, None

    def _is_blocked_address(self, sender: str) -> bool:
        return any(phone_numbers.matches(sender, blocked) for blocked in self._blocked)

    def _is_unknown(self, sender: str) -> bool:
        """Unknown-sender verdict: per-sender lookup wins, set rule stays.

        The lookup consults xx-contacts/EDS plus local reply/starred
        history (see ``txxt.known_senders``); the set rule is the
        offline fallback tests and static configs still drive.
        """
        if self._known_lookup is not None:
            try:
                return not bool(self._known_lookup(sender))
            except Exception:
                return False
        return UnknownSenderRule(self._known_provider()).is_unknown(sender)


@dataclass
class _LiveFilterState:
    keywords: set[str] = field(default_factory=set)
    phrases: set[str] = field(default_factory=set)
    blocked: set[str] = field(default_factory=set)
    starred: set[str] = field(default_factory=set)
    quarantine_unknown_senders: bool = False


_live_state = _LiveFilterState()
_live_filter = InboundFilter()
_live_known_provider: object = lambda: set()  # noqa: E731
_live_known_lookup: object | None = None
_live_overrides: BlockOverrideStore | None = None


def _rebuild_live_filter() -> None:
    global _live_filter
    _live_filter = InboundFilter(
        blocked_addresses=set(_live_state.blocked),
        content_keywords=set(_live_state.keywords),
        content_phrases=set(_live_state.phrases),
        starred_contacts=set(_live_state.starred),
        quarantine_unknown_senders=_live_state.quarantine_unknown_senders,
        known_contacts_provider=_live_known_provider,
        known_sender_lookup=_live_known_lookup,
        block_override_store=_live_overrides,
    )


def live_apply(
    keywords: set[str] | frozenset[str] = frozenset(),
    phrases: set[str] | frozenset[str] = frozenset(),
    blocked_addresses: set[str] | frozenset[str] = frozenset(),
    starred_contacts: set[str] | frozenset[str] = frozenset(),
    quarantine_unknown_senders: bool = False,
    known_contacts_provider: object = None,
    known_sender_lookup: object = None,
    block_override_store: BlockOverrideStore | None = None,
) -> None:
    """Install the process-wide live filter built from the given rule sets.

    Linux adaptation of ``LiveInboundFilter.apply``: the Android form takes
    the settings-screen models (a later UI lane); this seam takes the plain
    rule sets the SQLite ``blocklist``/``starred`` tables hold, so the same
    rebuild path serves settings edits, backup restores, and tests.
    ``known_sender_lookup`` (a sender -> bool callable, see
    ``txxt.known_senders``) wins over the set provider when given.
    """
    global _live_known_provider, _live_known_lookup, _live_overrides
    _live_state.keywords = set(keywords)
    _live_state.phrases = set(phrases)
    _live_state.blocked = set(blocked_addresses)
    _live_state.starred = set(starred_contacts)
    _live_state.quarantine_unknown_senders = quarantine_unknown_senders
    _live_known_provider = known_contacts_provider if callable(known_contacts_provider) else lambda: set()
    _live_known_lookup = known_sender_lookup if callable(known_sender_lookup) else None
    _live_overrides = block_override_store
    _rebuild_live_filter()


def live_current() -> InboundFilter:
    """The current process-wide inbound filter."""
    return _live_filter


#: ``settings``-table key for the opt-in unknown-sender hold.
QUARANTINE_SETTING_KEY = "quarantine_unknown_senders"

#: ``settings``-table key for persisted per-sender deliver overrides
#: (quarantine "Deliver" writes here so the next message from the same
#: sender lands in the inbox, in both the GUI and daemon processes).
OVERRIDES_SETTING_KEY = "block_overrides"


def add_persistent_override(store, sender: str) -> None:
    """Remember ``sender`` as deliver-overridden (survives restarts)."""
    try:
        saved = store.get_setting(OVERRIDES_SETTING_KEY, [])
    except Exception:
        saved = []
    names = {str(v) for v in saved} if isinstance(saved, list) else set()
    names.add(phone_numbers.normalize(sender))
    try:
        store.set_setting(OVERRIDES_SETTING_KEY, sorted(names))
    except Exception:
        pass


def live_apply_from_store(
    store,
    known_contacts_provider: object = None,
    known_sender_lookup: object = None,
    block_override_store: BlockOverrideStore | None = None,
) -> None:
    """Rebuild the live filter from the SQLite rule tables.

    Ports Android ``SettingsBlockingStore.loadAndApply``: blocked
    senders, keyword/phrase terms, starred contacts and the quarantine
    hold come from ``blocklist``/``starred``/``settings``, so a settings
    edit reaches the very next inbound message. The daemon calls this
    at startup (its process never sees the GUI's in-memory filter);
    the settings UI calls it after every edit.
    """
    try:
        senders = {value for _, value in store.blocklist_list("sender")}
    except Exception:
        senders = set()
    try:
        keywords = {value for _, value in store.blocklist_list("keyword")}
    except Exception:
        keywords = set()
    try:
        phrases = {value for _, value in store.blocklist_list("phrase")}
    except Exception:
        phrases = set()
    try:
        starred = set(store.starred_list())
    except Exception:
        starred = set()
    try:
        quarantine = bool(store.get_setting(QUARANTINE_SETTING_KEY, False))
    except Exception:
        quarantine = False
    overrides = block_override_store
    if overrides is None:
        try:
            saved = store.get_setting(OVERRIDES_SETTING_KEY, [])
        except Exception:
            saved = []
        names = {str(v) for v in saved} if isinstance(saved, list) else set()
        if names:
            kv = DictKeyValueStore()
            kv.put_string_set(BlockOverrideStore.KEY_OVERRIDES, names)
            overrides = BlockOverrideStore(kv)
    live_apply(
        keywords=keywords,
        phrases=phrases,
        blocked_addresses=senders,
        starred_contacts=starred,
        quarantine_unknown_senders=quarantine,
        known_contacts_provider=known_contacts_provider,
        known_sender_lookup=known_sender_lookup,
        block_override_store=overrides,
    )


def live_reset_for_test() -> None:
    """Restore fresh-object state between tests."""
    global _live_known_provider, _live_known_lookup, _live_overrides
    _live_state.keywords = set()
    _live_state.phrases = set()
    _live_state.blocked = set()
    _live_state.starred = set()
    _live_state.quarantine_unknown_senders = False
    _live_known_provider = lambda: set()  # noqa: E731
    _live_known_lookup = None
    _live_overrides = None
    _rebuild_live_filter()
