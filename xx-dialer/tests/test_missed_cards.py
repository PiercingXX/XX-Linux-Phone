"""Missed-call cards: every unanswered incoming that rang posts one.

The dialer policy service owns every missed-call card (the shell posts
none). A silenced verdict keeps the silenced wording; an enforced block
posts nothing; every other unanswered incoming — plain rings, observed
rows that rang despite the verdict, and fail-open calls with no screen
row — posts ``Missed call`` / ``N missed calls`` through the same
``decide_missed`` seen gate as the silenced path.
"""
import time

from xx_dialer import recents
from xx_dialer import ringer
from xx_dialer.service import PolicyService
from xx_dialer.store import CallRow, MirrorRow, Store

ADA = "+14155550100"
BO = "+14155550200"
UNKNOWN = "+19995550100"


class _Sink:
    """Injected FDN sender: captures cards, hands out server ids."""

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
    """Service-process contacts stand-in: records star/block writes."""

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


def _service(tmp_path, **over):
    """Default-settings service: only the FDN sender is sunk."""
    placed: list[str] = []
    messaged: list[str] = []
    contacts = _StubContacts()
    params = {"db_file": tmp_path / "calls.db", "home": tmp_path,
              "call_placer": placed.append,
              "message_launcher": messaged.append}
    params.update(over)
    service = PolicyService(**params)
    sink = _Sink()
    service._notifier._notify = sink
    service.attach_contacts_client(contacts)
    return service, sink, placed, messaged, contacts


def _save(tmp_path, number, name):
    """Mirror the number as a saved (plain, unstarred) contact."""
    with Store(tmp_path / "calls.db") as store:
        store.upsert_mirror([MirrorRow(
            "uid-%s" % name.lower(), number, name, False, None, False,
            int(time.time() * 1000))])


def _missed_rung(service, tmp_path, number, at_ms):
    """One screened computed-Ring missed call (saved contacts ring)."""
    assert service.check_number(number)[0] == "ring"
    with Store(tmp_path / "calls.db") as store:
        latest = store.latest_for(number)
    assert latest is not None and latest.verdict == "Ring"
    service.note_call(number, "incoming", at_ms, 0, "missed")


def _viewed_at(tmp_path):
    with Store(tmp_path / "calls.db") as store:
        return store.get_recents_viewed()


def test_single_known_missed_names_caller_plus_time(tmp_path):
    service, sink, _placed, _messaged, _contacts = _service(tmp_path)
    _save(tmp_path, ADA, "Ada")
    base = int(time.time() * 1000)
    _missed_rung(service, tmp_path, ADA, base)
    (card,) = sink.cards
    assert card["summary"] == "Missed call"
    assert card["body"] == "Ada · %s" % recents.format_when(base, base)
    assert [key for key, _label in card["actions"]] == [
        "call-back:%s" % ADA, "message:%s" % ADA,
        "block-contact:%s" % ADA]
    assert card["replaces"] == ringer.REPLACES_MISSED
    assert card["e164"] == ADA
    assert card["hints"]["desktop-entry"] == "io.piercingxx.XXDialer"
    assert card["hints"]["x-xx-wm-action-target"] == (
        "io.piercingxx.XXDialer.Policy /io/piercingxx/XXDialer/Policy"
        " io.piercingxx.XXDialer.Policy1")


def test_single_unknown_rung_missed_keeps_ring_next_time(tmp_path):
    service, sink, _placed, _messaged, _contacts = _service(tmp_path)
    base = int(time.time() * 1000)
    service.check_number(UNKNOWN)
    assert service.check_number(UNKNOWN)[0] == "ring"
    with Store(tmp_path / "calls.db") as store:
        assert store.latest_for(UNKNOWN).verdict == "Ring"
    service.note_call(UNKNOWN, "incoming", base, 0, "missed")
    (card,) = sink.cards
    assert card["summary"] == "Missed call"
    assert "999-555-0100" in card["body"]
    assert " · " in card["body"]
    assert [key for key, _label in card["actions"]] == [
        "call-back:%s" % UNKNOWN, "message:%s" % UNKNOWN,
        "ring-next-time:%s" % UNKNOWN, "block-contact:%s" % UNKNOWN]


def test_grouped_card_names_latest_callers_and_carries_no_actions(
        tmp_path):
    service, sink, _placed, _messaged, _contacts = _service(tmp_path)
    _save(tmp_path, ADA, "Ada")
    _save(tmp_path, BO, "Bo")
    base = int(time.time() * 1000)
    _missed_rung(service, tmp_path, ADA, base)
    _missed_rung(service, tmp_path, BO, base + 1_000)
    assert [card["summary"] for card in sink.cards] == [
        "Missed call", "2 missed calls"]
    assert [card["replaces"] for card in sink.cards] == [
        ringer.REPLACES_MISSED, ringer.REPLACES_MISSED]
    grouped = sink.cards[-1]
    assert grouped["body"].split("\n") == ["Bo", "Ada"]
    assert grouped["actions"] == []


def test_missed_replace_id_differs_from_silenced(tmp_path):
    assert ringer.REPLACES_MISSED != ringer.REPLACES_SILENCED


def test_message_action_opens_txxt_for_number(tmp_path):
    service, sink, _placed, messaged, _contacts = _service(tmp_path)
    _save(tmp_path, ADA, "Ada")
    _missed_rung(service, tmp_path, ADA, int(time.time() * 1000))
    (card,) = sink.cards
    assert service.handle_notification_action(
        card["server"], "message:%s" % ADA) is True
    assert messaged == [ADA]


def test_bare_message_key_falls_back_to_posted_number(tmp_path):
    service, sink, _placed, messaged, _contacts = _service(tmp_path)
    _save(tmp_path, ADA, "Ada")
    _missed_rung(service, tmp_path, ADA, int(time.time() * 1000))
    (card,) = sink.cards
    assert service.handle_notification_action(
        card["server"], "message") is True
    assert messaged == [ADA]
    assert service.handle_notification_action(
        card["server"], "sms:%s" % ADA) is True
    assert messaged == [ADA, ADA]


def test_missed_card_actions_run_in_service(tmp_path):
    service, sink, placed, _messaged, contacts = _service(tmp_path)
    _save(tmp_path, ADA, "Ada")
    _missed_rung(service, tmp_path, ADA, int(time.time() * 1000))
    (card,) = sink.cards
    assert service.handle_notification_action(
        card["server"], "call-back:%s" % ADA) is True
    assert placed == [ADA]
    assert service.handle_notification_action(
        card["server"], "block-contact:%s" % ADA) is True
    assert contacts.blocked == ["uid-ada"]
    assert service.handle_notification_action(
        card["server"], "bogus") is False
    assert service.handle_notification_action(
        999999, "call-back:%s" % ADA) is False


def test_silenced_call_posts_no_missed_card(tmp_path):
    from xx_dialer import config as app_config
    from xx_dialer.store import PatternRow

    settings = app_config.load(tmp_path)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, tmp_path)
    with Store(tmp_path / "calls.db") as store:
        store.add_pattern(PatternRow(
            e164_prefix="14155550100", wildcards=0, action="silence",
            created_at=1))
    service, sink, _placed, messaged, _contacts = _service(tmp_path)
    assert service.check_number(ADA)[0] == "silence"
    service.note_call(ADA, "incoming", int(time.time() * 1000), 0,
                      "missed")
    (card,) = sink.cards
    assert card["summary"] == "Silenced call"
    assert messaged == []


def test_rung_and_silenced_rows_card_separately(tmp_path):
    from xx_dialer import config as app_config
    from xx_dialer.store import PatternRow

    settings = app_config.load(tmp_path)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, tmp_path)
    with Store(tmp_path / "calls.db") as store:
        store.add_pattern(PatternRow(
            e164_prefix="14155550100", wildcards=0, action="silence",
            created_at=1))
    service, sink, _placed, _messaged, _contacts = _service(tmp_path)
    _save(tmp_path, BO, "Bo")
    base = int(time.time() * 1000)
    _missed_rung(service, tmp_path, BO, base)
    assert service.check_number(ADA)[0] == "silence"
    service.note_call(ADA, "incoming", base + 1_000, 0, "missed")
    assert [card["summary"] for card in sink.cards] == [
        "Missed call", "Silenced call"]
    assert sink.cards[0]["body"].startswith("Bo · ")
    assert "(415) 555-0100" not in sink.cards[0]["body"]


def test_enforced_block_posts_nothing(tmp_path):
    from xx_dialer import config as app_config

    settings = app_config.load(tmp_path)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, tmp_path)
    service, sink, _placed, _messaged, _contacts = _service(tmp_path)
    with Store(tmp_path / "calls.db") as store:
        store.add_blocked(BO)
    assert service.check_number(BO)[0] == "block"
    service.note_call(BO, "incoming", int(time.time() * 1000), 0,
                      "missed")
    assert sink.cards == []


def test_observed_block_rang_so_it_posts_a_missed_card(tmp_path):
    service, sink, _placed, _messaged, _contacts = _service(tmp_path)
    with Store(tmp_path / "calls.db") as store:
        store.add_blocked(BO)
    assert service.check_number(BO)[0] == "ring"
    with Store(tmp_path / "calls.db") as store:
        latest = store.latest_for(BO)
    assert latest.verdict == "Block" and latest.mode == "observed"
    service.note_call(BO, "incoming", int(time.time() * 1000), 0,
                      "missed")
    (card,) = sink.cards
    assert card["summary"] == "Missed call"


def test_answered_outgoing_and_withheld_post_nothing(tmp_path):
    service, sink, _placed, _messaged, _contacts = _service(tmp_path)
    _save(tmp_path, ADA, "Ada")
    base = int(time.time() * 1000)
    service.check_number(ADA)
    service.note_call(ADA, "incoming", base, 30_000, "answered")
    assert sink.cards == []
    service.note_call(ADA, "outgoing", base, 0, "missed")
    assert sink.cards == []
    service.note_call("", "incoming", base, 0, "missed")
    assert sink.cards == []


def test_seen_gate_suppresses_across_restart(tmp_path):
    service, sink, _placed, _messaged, _contacts = _service(tmp_path)
    _save(tmp_path, ADA, "Ada")
    _save(tmp_path, BO, "Bo")
    base = int(time.time() * 1000)
    _missed_rung(service, tmp_path, ADA, base)
    assert len(sink.cards) == 1
    with Store(tmp_path / "calls.db") as store:
        store.set_recents_viewed(base + 30_000)
    restarted_sink = _Sink()
    restarted, _s2, _p2, _m2, _c2 = _service(tmp_path)
    restarted._notifier._notify = restarted_sink
    assert restarted.check_number(BO)[0] == "ring"
    restarted.note_call(BO, "incoming", base - 60_000, 0, "missed")
    assert restarted_sink.cards == []
    assert len(sink.cards) == 1


def test_missed_action_acknowledges_seen(tmp_path):
    service, sink, _placed, _messaged, _contacts = _service(tmp_path)
    _save(tmp_path, ADA, "Ada")
    _save(tmp_path, BO, "Bo")
    base = int(time.time() * 1000)
    _missed_rung(service, tmp_path, ADA, base)
    (card,) = sink.cards
    assert service.handle_notification_action(
        card["server"], "call-back:%s" % ADA) is True
    first_mark = _viewed_at(tmp_path)
    assert first_mark > 0
    assert service.check_number(BO)[0] == "ring"
    service.note_call(BO, "incoming", base - 60_000, 0, "missed")
    assert len(sink.cards) == 1


def test_missed_dismiss_acknowledges_only_on_dismiss(tmp_path):
    service, sink, _placed, _messaged, _contacts = _service(tmp_path)
    _save(tmp_path, ADA, "Ada")
    _missed_rung(service, tmp_path, ADA, int(time.time() * 1000))
    (card,) = sink.cards
    service._notifier.note_notification_closed(card["server"], 1)
    assert _viewed_at(tmp_path) == 0
    service._notifier.note_notification_closed(card["server"], 2)
    assert _viewed_at(tmp_path) > 0


def test_direct_post_missed_covers_unreadable_log_shape():
    from pathlib import Path
    import tempfile

    from xx_dialer.ringer import Notifier

    sink = _Sink()
    with tempfile.TemporaryDirectory() as home:
        notifier = Notifier(db_file=Path(home) / "calls.db",
                           home=Path(home), notify=sink)
        outcome = recents.MissedPost(count=1, subject=None, covered=[],
                                     ack_up_to_ms=9_000, stale_ids=[])
        notifier.post_missed(outcome, region="US", now_ms=9_000)
    (card,) = sink.cards
    assert card["summary"] == "Missed call"
    assert card["actions"] == []
    assert card["replaces"] == ringer.REPLACES_MISSED


def test_grouped_body_lists_saved_names_first(tmp_path):
    service, sink, _placed, _messaged, _contacts = _service(tmp_path)
    with Store(tmp_path / "calls.db") as store:
        store.add_call(CallRow(date=3_000, number=ADA,
                               call_type=1, duration=0,
                               cached_name="Ada"))
        store.add_call(CallRow(date=4_000, number=BO,
                               call_type=1, duration=0))
    with Store(tmp_path / "calls.db") as store:
        pending = recents.pending_missed_rows(store)
        outcome = recents.decide_missed(pending, 0, 9_000)
    assert isinstance(outcome, recents.MissedPost)
    service._notifier.post_missed(outcome, region="US", now_ms=9_000)
    (card,) = sink.cards
    assert card["summary"] == "2 missed calls"
    assert card["body"].split("\n")[0] == "(415) 555-0200"
    assert "Ada" in card["body"]
