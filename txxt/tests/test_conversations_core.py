"""Port of the Android conversation-logic tests.

Covers core/ConversationPinTest.kt, core/MuteUntilTest.kt,
core/ConversationListTest.kt, core/PinnedFirstOrderingTest.kt,
core/PinSortArchiveTest.kt, data/ConversationMuteTest.kt,
data/ConversationPinTest.kt and ui/ConversationVisibilityTest.kt.

Store-backed pin/mute/quarantine behaviour (the data/ tests) runs against
the real SQLite store instead of the Android fake DAOs.
"""

from zoneinfo import ZoneInfo

import pytest

from txxt import conversations_core as cc
from txxt.conversations_core import (
    Conversation,
    ConversationFlags,
    ConversationList,
    ConversationSortOrder,
    Message,
    MessageDirection,
    MessageTransport,
    MutePreset,
    MuteUntil,
    UnreadCount,
    can_pin,
    is_mute_active,
    show_on_list,
    show_on_main_list,
)
from txxt.store import PinResult, Store

UTC = ZoneInfo("UTC")


def millis(year, month, day, hour, minute=0):
    from datetime import datetime

    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def make_message(mid, cid, is_read=False, timestamp=None, direction=MessageDirection.INCOMING):
    return Message(
        id=mid,
        conversation_id=cid,
        direction=direction,
        transport=MessageTransport.SMS,
        body="hello",
        timestamp_millis=mid * 1000 if timestamp is None else timestamp,
        sender_address="+15550001111",
        is_read=is_read,
    )


def make_conversation(cid, *messages):
    return Conversation(
        id=cid, participant_addresses={"+15550001111"}, messages=list(messages))


def ids(conversations):
    return [c.id for c in conversations]


# ---- ConversationPin (core) ----

def test_the_pin_cap_is_five():
    assert cc.MAX_PINNED == 5


def test_a_new_pin_is_allowed_below_the_cap():
    assert can_pin(pinned_count=0, already_pinned=False)
    assert can_pin(pinned_count=4, already_pinned=False)


def test_a_sixth_pin_is_refused():
    assert not can_pin(pinned_count=5, already_pinned=False)
    assert not can_pin(pinned_count=6, already_pinned=False)


def test_an_already_pinned_thread_can_stay_pinned_at_the_cap():
    assert can_pin(pinned_count=5, already_pinned=True)


# ---- MuteUntil ----

def test_one_hour_is_now_plus_3600000():
    now = millis(2026, 3, 4, 12, 0)
    assert MuteUntil.expiry_millis(MutePreset.ONE_HOUR, now, UTC) == now + 3_600_000


def test_eight_hours_is_now_plus_eight_hours():
    now = millis(2026, 3, 4, 12, 0)
    assert MuteUntil.expiry_millis(MutePreset.EIGHT_HOURS, now, UTC) == now + 8 * 3_600_000


def test_tonight_before_21_is_today_at_21():
    now = millis(2026, 3, 4, 18, 0)
    assert MuteUntil.expiry_millis(MutePreset.TONIGHT, now, UTC) == millis(2026, 3, 4, 21, 0)
    assert MuteUntil.TONIGHT_HOUR == 21


def test_tonight_at_or_after_21_is_tomorrow_at_21():
    assert MuteUntil.expiry_millis(MutePreset.TONIGHT, millis(2026, 3, 4, 21, 0), UTC) == \
        millis(2026, 3, 5, 21, 0)


def test_monday_from_wednesday_is_the_coming_monday_midnight():
    now = millis(2026, 3, 4, 15, 0)
    assert MuteUntil.expiry_millis(MutePreset.MONDAY, now, UTC) == millis(2026, 3, 9, 0, 0)


def test_monday_from_monday_is_next_week():
    now = millis(2026, 3, 9, 9, 0)
    assert MuteUntil.expiry_millis(MutePreset.MONDAY, now, UTC) == millis(2026, 3, 16, 0, 0)


def test_time_based_mute_active_only_while_expiry_is_in_the_future():
    assert not MuteUntil.is_active(0, 1_000)
    assert MuteUntil.is_active(2_000, 1_000)
    assert not MuteUntil.is_active(1_000, 1_000)
    assert not MuteUntil.is_active(999, 1_000)


# ---- ConversationFlags ----

def test_default_flags_are_unpinned_unarchived_unquarantined_pinned_first():
    flags = ConversationFlags()
    assert not flags.is_pinned
    assert not flags.is_archived
    assert not flags.is_quarantined
    assert not flags.is_muted
    assert flags.muted_until_millis == 0
    assert flags.sort_order is ConversationSortOrder.PINNED_FIRST


def test_pin_archive_transitions_and_immutability():
    assert ConversationFlags().pin().is_pinned
    assert not ConversationFlags().pin().unpin().is_pinned
    assert ConversationFlags().archive().is_archived
    assert not ConversationFlags().archive().unarchive().is_archived
    flags = ConversationFlags()
    pinned, archived = flags.pin(), flags.archive()
    assert not flags.is_pinned and not flags.is_archived
    assert pinned.is_pinned and not pinned.is_archived
    assert archived.is_archived and not archived.is_pinned
    both = ConversationFlags().pin().archive()
    assert both.is_pinned and both.is_archived


def test_quarantine_and_mute_until_transitions():
    assert ConversationFlags().quarantine().is_quarantined
    assert not ConversationFlags().quarantine().release_from_quarantine().is_quarantined
    flags = ConversationFlags().mute_until(1_700_000_000_000)
    assert not flags.is_muted
    assert flags.muted_until_millis == 1_700_000_000_000
    assert flags.unmute().muted_until_millis == 0
    assert flags.mute().is_muted
    assert flags.mute().muted_until_millis == 0


def test_with_sort_order_keeps_pin_and_archive_state():
    flags = ConversationFlags().with_sort_order(ConversationSortOrder.UNREAD_FIRST)
    assert flags.sort_order is ConversationSortOrder.UNREAD_FIRST
    flags = ConversationFlags().pin().archive().with_sort_order(ConversationSortOrder.OLDEST_FIRST)
    assert flags.is_pinned and flags.is_archived
    assert flags.sort_order is ConversationSortOrder.OLDEST_FIRST
    assert len(set(ConversationSortOrder)) == 4


# ---- ConversationList ordering ----

def test_unread_first_surfaces_unread_before_read():
    read = make_conversation(1, make_message(1, 1, is_read=True, timestamp=1000))
    unread = make_conversation(2, make_message(2, 2, is_read=False, timestamp=2000))
    assert ids(ConversationList.sorted([read, unread], ConversationSortOrder.UNREAD_FIRST)) == [2, 1]


def test_unread_first_orders_by_count_then_recency():
    one = make_conversation(1, make_message(1, 1, timestamp=3000))
    two = make_conversation(
        2, make_message(2, 2, timestamp=1000), make_message(3, 2, timestamp=2000))
    assert ids(ConversationList.sorted([one, two], ConversationSortOrder.UNREAD_FIRST)) == [2, 1]


def test_newest_and_oldest_first_ignore_unread_state():
    older = make_conversation(1, make_message(1, 1, timestamp=1000))
    newer = make_conversation(2, make_message(2, 2, timestamp=2000))
    assert ids(ConversationList.sorted([older, newer], ConversationSortOrder.NEWEST_FIRST)) == [2, 1]
    assert ids(ConversationList.sorted([older, newer], ConversationSortOrder.OLDEST_FIRST)) == [1, 2]


def test_unread_first_is_driven_by_the_unread_count_map():
    read = make_conversation(1, make_message(1, 1, is_read=True, timestamp=3000))
    two = make_conversation(
        2, make_message(2, 2, timestamp=1000), make_message(3, 2, timestamp=2000))
    one = make_conversation(3, make_message(4, 3, timestamp=500))
    conversations = [read, two, one]
    by_unread = UnreadCount.per_conversation(conversations)
    ordered = ConversationList.sorted(conversations, ConversationSortOrder.UNREAD_FIRST)
    expected = sorted(conversations, key=lambda c: -by_unread[c.id])
    assert ids(ordered) == ids(expected)


def test_pinned_first_beats_unread_and_recency():
    pinned_read = make_conversation(1, make_message(1, 1, is_read=True, timestamp=1000))
    unpinned_unread = make_conversation(2, make_message(2, 2, is_read=False, timestamp=3000))
    ordered = ConversationList.sorted([unpinned_unread, pinned_read], pinned_ids={1})
    assert ids(ordered) == [1, 2]


def test_pinned_first_falls_back_to_recency_within_each_group():
    newer_unpinned = make_conversation(1, make_message(1, 1, timestamp=3000))
    older_unpinned = make_conversation(2, make_message(2, 2, timestamp=1000))
    older_pinned = make_conversation(3, make_message(3, 3, timestamp=500))
    newer_pinned = make_conversation(4, make_message(4, 4, timestamp=2000))
    ordered = ConversationList.sorted(
        [newer_unpinned, older_unpinned, older_pinned, newer_pinned],
        ConversationSortOrder.PINNED_FIRST,
        pinned_ids={3, 4},
    )
    assert ids(ordered) == [4, 3, 1, 2]


def test_pinned_first_defaults_to_recency_and_empty_stays_empty():
    older = make_conversation(1, make_message(1, 1, timestamp=1000))
    newer = make_conversation(2, make_message(2, 2, timestamp=2000))
    assert ids(ConversationList.sorted([older, newer], ConversationSortOrder.PINNED_FIRST)) == [2, 1]
    assert ConversationList.sorted([]) == []


def test_unread_count_derivations():
    read = make_conversation(1, make_message(1, 1, is_read=True, timestamp=1000))
    unread = make_conversation(
        2, make_message(2, 2, timestamp=2000),
        make_message(3, 2, direction=MessageDirection.OUTGOING, timestamp=3000))
    assert UnreadCount.total([read, unread]) == 1
    assert UnreadCount.per_conversation([read, unread]) == {1: 0, 2: 1}
    assert UnreadCount.any_unread([read, unread])
    assert not UnreadCount.any_unread([read])
    assert unread.unread_count == 1 and unread.has_unread
    assert not read.has_unread


def test_conversation_rejects_messages_from_another_thread():
    with pytest.raises(ValueError):
        Conversation(id=1, participant_addresses=set(), messages=[make_message(9, 2)])


# ---- visibility ----

def test_blocked_threads_stay_hidden_until_search():
    assert show_on_list(blocked=False, search_query=None)
    assert not show_on_list(blocked=True, search_query=None)
    assert not show_on_list(blocked=True, search_query="  ")
    assert show_on_list(blocked=True, search_query="Ada")


def test_main_list_hides_archived_and_quarantined():
    assert show_on_main_list(False, False)
    assert not show_on_main_list(True, False)
    assert not show_on_main_list(False, True)
    assert not show_on_main_list(True, True)


# ---- store-backed: ConversationMute (data/) ----

def test_forever_mute_stays_on_and_mute_until_expires():
    assert is_mute_active(True, 0, 1_000)
    assert is_mute_active(True, 0, 2**62)
    assert is_mute_active(False, 2_000, 1_000)
    assert not is_mute_active(False, 2_000, 2_000)
    assert not is_mute_active(False, 2_000, 3_000)
    assert not is_mute_active(False, 0, 1_000)


def test_store_is_muted_matches_address_and_respects_expiry():
    store = Store.memory()
    cid = store.find_or_create_conversation("+15551234567")
    row = store.get_conversation(cid)
    store.set_flags(cid, row.flags().mute_until(2_000))
    assert store.is_muted("+15551234567", 1_000)
    assert not store.is_muted("+15551234567", 3_000)
    assert not store.is_muted("+15559990000", 1_000)
    row = store.get_conversation(cid)
    store.set_flags(cid, row.flags().mute())
    assert store.is_muted("555-123-4567", 2**62)


# ---- store-backed: ConversationPin (data/) ----

def test_store_pin_two_threads_and_unpin():
    store = Store.memory()
    for address in ("a", "b", "c"):
        store.find_or_create_conversation(address)
    rows = {r.participant_addresses: r.id for r in store.get_all_conversations()}
    assert store.toggle_pin(rows["a"]) is PinResult.PINNED
    assert store.toggle_pin(rows["b"]) is PinResult.PINNED
    assert store.get_conversation(rows["a"]).is_pinned
    assert store.get_conversation(rows["b"]).is_pinned
    assert not store.get_conversation(rows["c"]).is_pinned
    assert store.toggle_pin(rows["a"]) is PinResult.UNPINNED
    assert not store.get_conversation(rows["a"]).is_pinned


def test_store_sixth_pin_refused_and_writes_nothing():
    store = Store.memory()
    for n in range(1, 6):
        cid = store.find_or_create_conversation(f"n{n}")
        assert store.toggle_pin(cid) is PinResult.PINNED
    sixth = store.find_or_create_conversation("n6")
    assert store.toggle_pin(sixth) is PinResult.CAP_REACHED
    assert not store.get_conversation(sixth).is_pinned
    assert sum(1 for r in store.get_all_conversations() if r.is_pinned) == 5
    assert store.toggle_pin(999_999_999) is PinResult.MISSING
