"""Pure conversation logic.

Port of the pure ``core/`` conversation model:

- ``core/ConversationFlags.kt`` -> :class:`ConversationFlags`
- ``core/ConversationPin.kt`` -> :data:`MAX_PINNED` / :func:`can_pin`
- ``core/MuteUntil.kt`` -> :class:`MuteUntil`
- ``core/Conversation.kt`` + ``core/Message.kt`` (shape only) ->
  :class:`Conversation` / :class:`Message`
- ``core/UnreadCount.kt`` -> :class:`UnreadCount`
- ``core/ConversationList.kt`` -> :class:`ConversationList`
- ``ui/ConversationVisibility.kt`` -> :func:`show_on_list`

Plus :func:`show_on_main_list`, derived from the data-layer contract that
archived and quarantined threads stay hidden from the main list (see
``ConversationFlags`` and ``QuarantineStore`` on Android: quarantine hides
"like archive", and new delivered activity surfaces a thread again).

All wall-clock mute math takes an explicit ``now`` so it stays unit-testable
without a device.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, time as dtime
from enum import Enum
from zoneinfo import ZoneInfo


class ConversationSortOrder(Enum):
    PINNED_FIRST = "PINNED_FIRST"
    NEWEST_FIRST = "NEWEST_FIRST"
    OLDEST_FIRST = "OLDEST_FIRST"
    UNREAD_FIRST = "UNREAD_FIRST"


class MutePreset(Enum):
    ONE_HOUR = "ONE_HOUR"
    EIGHT_HOURS = "EIGHT_HOURS"
    TONIGHT = "TONIGHT"
    MONDAY = "MONDAY"


class MessageDirection(Enum):
    INCOMING = "INCOMING"
    OUTGOING = "OUTGOING"


class MessageTransport(Enum):
    SMS = "SMS"
    MMS = "MMS"


#: Collapsed photo marker shown until the operator taps the row.
COLLAPSED_PHOTO_PLACEHOLDER = "[Photo]"


@dataclass(frozen=True)
class ConversationFlags:
    """Pin/archive/mute/quarantine flags of one conversation.

    Immutable: every transition returns a new instance.
    """

    is_pinned: bool = False
    is_archived: bool = False
    is_muted: bool = False
    is_quarantined: bool = False
    muted_until_millis: int = 0
    sort_order: ConversationSortOrder = ConversationSortOrder.PINNED_FIRST

    def pin(self) -> "ConversationFlags":
        return replace(self, is_pinned=True)

    def unpin(self) -> "ConversationFlags":
        return replace(self, is_pinned=False)

    def archive(self) -> "ConversationFlags":
        return replace(self, is_archived=True)

    def unarchive(self) -> "ConversationFlags":
        return replace(self, is_archived=False)

    def mute(self) -> "ConversationFlags":
        return replace(self, is_muted=True, muted_until_millis=0)

    def unmute(self) -> "ConversationFlags":
        return replace(self, is_muted=False, muted_until_millis=0)

    def mute_until(self, until_millis: int) -> "ConversationFlags":
        return replace(self, is_muted=False, muted_until_millis=until_millis)

    def quarantine(self) -> "ConversationFlags":
        return replace(self, is_quarantined=True)

    def release_from_quarantine(self) -> "ConversationFlags":
        return replace(self, is_quarantined=False)

    def with_sort_order(self, order: ConversationSortOrder) -> "ConversationFlags":
        return replace(self, sort_order=order)


#: Maximum number of pinned conversations.
MAX_PINNED = 5


def can_pin(pinned_count: int, already_pinned: bool) -> bool:
    """Whether a new pin fits: unpin is always allowed, and an already-pinned
    thread can stay pinned even at the cap."""
    return already_pinned or pinned_count < MAX_PINNED


class MuteUntil:
    """Wall-clock mute-until presets."""

    #: Local hour used for "tonight".
    TONIGHT_HOUR = 21

    @staticmethod
    def expiry_millis(preset: MutePreset, now_millis: int, tz: ZoneInfo | None = None) -> int:
        """Expiry epoch millis for ``preset`` relative to ``now_millis``.

        Tonight is today at 21:00 local, or tomorrow 21:00 when that time has
        already passed. Monday is the next Monday at local midnight; when
        today is already Monday that means next week.
        """
        zone = tz if tz is not None else datetime.now().astimezone().tzinfo
        zoned = datetime.fromtimestamp(now_millis / 1000, tz=zone)
        if preset is MutePreset.ONE_HOUR:
            return int((zoned.timestamp() + 3600) * 1000)
        if preset is MutePreset.EIGHT_HOURS:
            return int((zoned.timestamp() + 8 * 3600) * 1000)
        if preset is MutePreset.TONIGHT:
            target = datetime.combine(zoned.date(), dtime(MuteUntil.TONIGHT_HOUR, 0)).replace(
                tzinfo=zone
            )
            if zoned >= target:
                target = target + _one_day()
            return int(target.timestamp() * 1000)
        if preset is MutePreset.MONDAY:
            days = (0 - zoned.weekday() + 7) % 7
            base = zoned.date() + _days(7 if days == 0 else days)
            target = datetime.combine(base, dtime(0, 0)).replace(tzinfo=zone)
            return int(target.timestamp() * 1000)
        raise ValueError(f"unknown preset: {preset!r}")

    @staticmethod
    def is_active(muted_until_millis: int, now_millis: int) -> bool:
        """Whether a time-based mute is still active. ``0`` means none."""
        return muted_until_millis > 0 and muted_until_millis > now_millis


def _one_day():  # tiny helper kept local so DST edge math stays in one place
    from datetime import timedelta

    return timedelta(days=1)


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


@dataclass
class Message:
    id: int
    conversation_id: int
    direction: MessageDirection = MessageDirection.INCOMING
    transport: MessageTransport = MessageTransport.SMS
    body: str = ""
    timestamp_millis: int = 0
    sender_address: str | None = None
    is_read: bool = False
    is_sent: bool = True
    content_location: str | None = None
    media_path: str | None = None

    @property
    def is_unread(self) -> bool:
        return self.direction is MessageDirection.INCOMING and not self.is_read


@dataclass
class Conversation:
    id: int
    participant_addresses: set[str] = field(default_factory=set)
    messages: list[Message] = field(default_factory=list)
    is_muted: bool = False

    def __post_init__(self) -> None:
        bad = [m.id for m in self.messages if m.conversation_id != self.id]
        if bad:
            raise ValueError(f"all messages must belong to this conversation (id={self.id})")

    @property
    def latest_message(self) -> Message | None:
        if not self.messages:
            return None
        return max(self.messages, key=lambda m: m.timestamp_millis)

    @property
    def latest_timestamp_millis(self) -> int | None:
        latest = self.latest_message
        return latest.timestamp_millis if latest is not None else None

    @property
    def unread_count(self) -> int:
        return sum(1 for m in self.messages if m.is_unread)

    @property
    def has_unread(self) -> bool:
        return self.unread_count > 0


class UnreadCount:
    """Unread-count derivations feeding the badge and per-thread list."""

    @staticmethod
    def total(conversations: list[Conversation]) -> int:
        return sum(c.unread_count for c in conversations)

    @staticmethod
    def per_conversation(conversations: list[Conversation]) -> dict[int, int]:
        return {c.id: c.unread_count for c in conversations}

    @staticmethod
    def any_unread(conversations: list[Conversation]) -> bool:
        return any(c.has_unread for c in conversations)


class ConversationList:
    """Conversation-list ordering."""

    @staticmethod
    def sorted(
        conversations: list[Conversation],
        order: ConversationSortOrder = ConversationSortOrder.PINNED_FIRST,
        pinned_ids: set[int] | frozenset[int] = frozenset(),
    ) -> list[Conversation]:
        """Order ``conversations`` by ``order`` (recency tiebreak everywhere).

        PINNED_FIRST puts pinned ids first, then recency. UNREAD_FIRST uses
        the :class:`UnreadCount` per-conversation map, then recency.
        """
        unread = UnreadCount.per_conversation(conversations)
        rows = list(conversations)
        if order is ConversationSortOrder.UNREAD_FIRST:
            return sorted(rows, key=lambda c: (-unread.get(c.id, 0), -_recency(c)))
        if order is ConversationSortOrder.OLDEST_FIRST:
            return sorted(rows, key=_recency_asc)
        if order is ConversationSortOrder.NEWEST_FIRST:
            return sorted(rows, key=lambda c: -_recency(c))
        return sorted(rows, key=lambda c: (0 if c.id in pinned_ids else 1, -_recency(c)))


def _recency(c: Conversation) -> int:
    ts = c.latest_timestamp_millis
    return ts if ts is not None else -(2**63)


def _recency_asc(c: Conversation) -> int:
    ts = c.latest_timestamp_millis
    return ts if ts is not None else 2**63 - 1


def show_on_list(blocked: bool, search_query: str | None) -> bool:
    """Blocked-group threads stay off the open inbox; a non-blank search is
    the only way they appear."""
    if not blocked:
        return True
    return search_query is not None and bool(search_query.strip())


def show_on_main_list(is_archived: bool, is_quarantined: bool) -> bool:
    """Whether a thread appears on the main (unfiltered) conversation list."""
    return not is_archived and not is_quarantined


def is_mute_active(is_muted: bool, muted_until_millis: int, now_millis: int) -> bool:
    """Whether notifications for a conversation are currently suppressed."""
    return is_muted or MuteUntil.is_active(muted_until_millis, now_millis)
