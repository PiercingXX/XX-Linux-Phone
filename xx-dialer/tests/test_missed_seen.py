"""Missed-seen: already-seen calls never re-post, every ack marks seen.

Port of Android's ``MissedCallDecisionTest`` (commit 05368ac "Stop
re-posting already-seen missed calls after reboot") plus integration
tests for each Linux acknowledge path: card actions, dismiss
(NotificationClosed reason 2), Recents open, and the daily-digest /
service-restart flush. The dialer owns the call log, so the pure
decision lives in :mod:`xx_dialer.recents`.

Mapping to the Android cases: ``pending`` is the call log's unanswered
incoming set (what Telecom's ``NEW = 1`` re-send set is on Android);
``viewed_at`` is ``recents_state.viewedAt`` (last Recents visit, or the
newest call an acknowledged card covered). Linux has no count-0
re-send broadcast: an answered/outgoing/unscreened ``NoteCall`` is the
cancel event and must post nothing while leaving the watermark alone.
"""
import time

from xx_dialer import config as app_config
from xx_dialer import recents
from xx_dialer.service import PolicyService
from xx_dialer.store import CallRow, MirrorRow, PatternRow, Store

ADA = "+14155550100"
BO = "+14155550200"

VIEWED_AT = 2_000
NOW = 9_000

OLD_SEEN = recents.MissedRow(key=11, time_ms=1_000, number="+15550001")
FRESH = recents.MissedRow(key=12, time_ms=3_000, number="+15550002",
                          cached_name="Ada")


def decide(rows, viewed_at=VIEWED_AT, now=NOW):
    return recents.decide_missed(rows, viewed_at, now)


def test_boot_resend_of_seen_call_suppresses():
    outcome = decide([OLD_SEEN])
    assert isinstance(outcome, recents.MissedSuppress)
    assert outcome.stale_ids == [11]


def test_fresh_missed_call_posts():
    outcome = decide([FRESH])
    assert isinstance(outcome, recents.MissedPost)
    assert outcome.count == 1
    assert outcome.subject == FRESH
    assert outcome.ack_up_to_ms == 3_000
    assert outcome.stale_ids == []


def test_fresh_posts_before_recents_ever_opened():
    outcome = decide([OLD_SEEN], viewed_at=0)
    assert isinstance(outcome, recents.MissedPost)
    assert outcome.count == 1


def test_acknowledge_marks_covered_seen():
    outcome = decide([FRESH])
    assert isinstance(outcome, recents.MissedPost)
    seen_up_to = recents.next_viewed_at(VIEWED_AT, outcome.ack_up_to_ms)
    after_reboot = decide([FRESH], viewed_at=seen_up_to)
    assert isinstance(after_reboot, recents.MissedSuppress)
    assert after_reboot.stale_ids == [12]


def test_old_seen_plus_new_posts_count_one():
    outcome = decide([OLD_SEEN, FRESH])
    assert isinstance(outcome, recents.MissedPost)
    assert outcome.count == 1
    assert outcome.subject == FRESH
    assert outcome.covered == [FRESH]
    assert outcome.stale_ids == [11]


def test_several_unseen_newest_first():
    newer = recents.MissedRow(key=13, time_ms=4_000,
                              number="+15550003")
    outcome = recents.decide_missed([FRESH, newer, OLD_SEEN],
                                    VIEWED_AT, NOW)
    assert isinstance(outcome, recents.MissedPost)
    assert outcome.count == 2
    assert outcome.subject is None
    assert outcome.covered == [newer, FRESH]
    assert outcome.ack_up_to_ms == 4_000


def test_unreadable_log_posts_plain():
    outcome = decide(None)
    assert isinstance(outcome, recents.MissedPost)
    assert outcome.count == 1
    assert outcome.subject is None
    assert outcome.ack_up_to_ms == NOW


def test_empty_pending_posts_nothing():
    outcome = decide([])
    assert isinstance(outcome, recents.MissedSuppress)
    assert outcome.stale_ids == []


def test_viewed_at_never_moves_backwards():
    assert recents.next_viewed_at(5_000, 3_000) == 5_000
    assert recents.next_viewed_at(5_000, 6_000) == 6_000


class _Sink:
    def __init__(self):
        self.cards = []
        self.next_id = 40

    def __call__(self, replaces_id, summary, body, actions, hints,
                 e164=""):
        self.next_id += 1
        self.cards.append({"replaces": replaces_id,
                           "summary": summary, "body": body,
                           "actions": list(actions), "hints": hints,
                           "server": self.next_id, "e164": e164})
        return self.next_id


class _StubContacts:
    def __init__(self):
        self.revision = 0
        self.starred: list[str] = []
        self.blocked: list[str] = []

    def set_star(self, uid, starred):
        if starred:
            self.starred.append(uid)

    def set_blocked(self, uid, blocked):
        if blocked:
            self.blocked.append(uid)
        return []


def _silenced_service(tmp_path):
    settings = app_config.load(tmp_path)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, tmp_path)
    with Store(tmp_path / "calls.db") as store:
        store.add_pattern(PatternRow(
            e164_prefix="14155550100", wildcards=0, action="silence",
            created_at=1))
        store.add_pattern(PatternRow(
            e164_prefix="14155550200", wildcards=0, action="silence",
            created_at=2))
    sink = _Sink()
    placed: list[str] = []
    contacts = _StubContacts()
    service = PolicyService(db_file=tmp_path / "calls.db",
                            home=tmp_path, call_placer=placed.append)
    service._notifier._notify = sink
    service.attach_contacts_client(contacts)
    return service, sink, placed, contacts


def _missed(service, number, at_ms):
    assert service.check_number(number)[0] == "silence"
    service.note_call(number, "incoming", at_ms, 0, "missed")


def _viewed_at(tmp_path):
    with Store(tmp_path / "calls.db") as store:
        return store.get_recents_viewed()


def test_cancel_event_posts_nothing_and_leaves_watermark(tmp_path):
    service, sink, _placed, _contacts = _silenced_service(tmp_path)
    base = int(time.time() * 1000)
    service.check_number(ADA)
    service.note_call(ADA, "incoming", base, 30_000, "answered")
    service.note_call(ADA, "outgoing", base, 0, "missed")
    assert sink.cards == []
    assert _viewed_at(tmp_path) == 0


def test_unscreened_rung_missed_posts_missed_card(tmp_path):
    """A fail-open call has no screen row, but it rang: it posts the
    missed-call card (never the silenced one), and posting alone does
    not move the watermark — only viewing does."""
    service, sink, _placed, _contacts = _silenced_service(tmp_path)
    base = int(time.time() * 1000)
    service.note_call("+19995550100", "incoming", base, 0, "missed")
    (card,) = sink.cards
    assert card["summary"] == "Missed call"
    assert _viewed_at(tmp_path) == 0


def test_service_posts_only_unseen_calls(tmp_path):
    service, sink, _placed, _contacts = _silenced_service(tmp_path)
    base = int(time.time() * 1000)
    with Store(tmp_path / "calls.db") as store:
        store.add_call(CallRow(date=base - 60_000, number=ADA,
                               call_type=1, duration=0))
        store.set_recents_viewed(base - 30_000)
    _missed(service, BO, base)
    assert len(sink.cards) == 1
    with Store(tmp_path / "calls.db") as store:
        store.set_recents_viewed(base + 30_000)
    _missed(service, ADA, base + 60_000 - 120_000)
    assert len(sink.cards) == 1


def test_each_card_action_acknowledges(tmp_path):
    service, sink, placed, contacts = _silenced_service(tmp_path)
    with Store(tmp_path / "calls.db") as store:
        store.upsert_mirror([MirrorRow(
            "uid-ada", ADA, "Ada", False, None, True,
            int(time.time() * 1000))])
    base = int(time.time() * 1000)
    _missed(service, ADA, base)
    (card,) = sink.cards
    assert service.handle_notification_action(
        card["server"], "call-back:%s" % ADA) is True
    assert placed == [ADA]
    first_mark = _viewed_at(tmp_path)
    assert first_mark > 0
    assert service.handle_notification_action(
        card["server"], "ring-next-time:%s" % ADA) is True
    assert contacts.starred == ["uid-ada"]
    assert _viewed_at(tmp_path) >= first_mark
    assert service.handle_notification_action(
        card["server"], "block-contact:%s" % ADA) is True
    assert contacts.blocked == ["uid-ada"]
    assert service.handle_notification_action(
        card["server"], "bogus") is False


def test_dismiss_acknowledges_only_reason_dismissed(tmp_path):
    service, sink, _placed, _contacts = _silenced_service(tmp_path)
    _missed(service, ADA, int(time.time() * 1000))
    (card,) = sink.cards
    service._notifier.note_notification_closed(card["server"], 1)
    assert _viewed_at(tmp_path) == 0
    service._notifier.note_notification_closed(987654, 2)
    assert _viewed_at(tmp_path) == 0
    service._notifier.note_notification_closed(card["server"], 2)
    assert _viewed_at(tmp_path) > 0


def test_recents_open_clears_unseen(tmp_path):
    from xx_dialer.ui_recents import RecentsController, RecentsDeps

    service, sink, _placed, _contacts = _silenced_service(tmp_path)
    base = int(time.time() * 1000)
    _missed(service, ADA, base)
    assert len(sink.cards) == 1
    when = base + 5_000
    controller = RecentsController(
        RecentsDeps(db_file=tmp_path / "calls.db",
                    now_ms=lambda: when))
    controller.load()
    assert controller.unread == 1
    controller.mark_viewed()
    assert controller.unread == 0
    with Store(tmp_path / "calls.db") as store:
        pending = recents.pending_missed_rows(store)
        outcome = recents.decide_missed(
            pending, store.get_recents_viewed(), when)
    assert isinstance(outcome, recents.MissedSuppress)


def test_digest_flush_drops_seen_entries(tmp_path):
    settings = app_config.load(tmp_path)
    settings["silenced_notif_policy"] = "daily"
    app_config.save(settings, tmp_path)
    service, sink, _placed, _contacts = _silenced_service(tmp_path)
    base = int(time.time() * 1000)
    service.check_number(ADA)
    service.note_call(ADA, "incoming", base, 0, "missed")
    assert sink.cards == []
    with Store(tmp_path / "calls.db") as store:
        assert len(store.read_digest()) == 1
        store.set_recents_viewed(base + 1_000)
    assert service.flush_daily_digest() is False
    assert sink.cards == []
    with Store(tmp_path / "calls.db") as store:
        assert store.read_digest() == []


def test_card_carries_number_and_display_text(tmp_path):
    service, sink, _placed, _contacts = _silenced_service(tmp_path)
    _missed(service, ADA, int(time.time() * 1000))
    (card,) = sink.cards
    assert [key for key, _label in card["actions"]] == [
        "call-back:%s" % ADA, "ring-next-time:%s" % ADA,
        "block-contact:%s" % ADA]
    assert "(415) 555-0100" in card["body"]
    assert card["hints"]["desktop-entry"] == "io.piercingxx.XXDialer"


def test_acknowledge_is_monotonic_in_store(tmp_path):
    with Store(tmp_path / "calls.db") as store:
        assert recents.acknowledge(store, 0) == 0
        assert recents.acknowledge(store, -5) == 0
        assert recents.acknowledge(store, 5_000) == 5_000
        assert recents.acknowledge(store, 3_000) == 5_000
        assert store.get_recents_viewed() == 5_000


def test_pending_missed_rows_newest_first(store):
    store.add_call(CallRow(date=1_000, number=ADA,
                           call_type=1, duration=0))
    store.add_call(CallRow(date=3_000, number=BO,
                           call_type=1, duration=0))
    store.add_call(CallRow(date=2_000, number=ADA,
                           call_type=2, duration=30))
    rows = recents.pending_missed_rows(store)
    assert [(row.key, row.time_ms) for row in rows] == [
        (2, 3_000), (1, 1_000)]


def test_posted_at_accessor_round_trip(store):
    assert store.notif_posted_at(41) == 0
    store.record_posted_notif(41, ADA, 7_000)
    assert store.notif_posted_at(41) == 7_000

