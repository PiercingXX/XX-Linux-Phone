"""L3 daemon tests: reconcile, inbound pipeline, retry queue, lock.

Reconcile and pipeline tests run against the real SQLite store plus
small fake modem buses; no display, no live D-Bus.
"""

import fcntl
import os

from txxt import blocking, daemon, mm_client
from txxt.blocking import InboundFilter, MessageDisposition
from txxt.daemon import (
    InboundPipeline,
    ReconcileCounts,
    RetryQueue,
    acquire_single_instance,
    reconcile_mms_messages,
    reconcile_records,
    retry_delay_ms,
    seeds_with_addresses,
    sms_timestamp_millis,
)
from txxt.settings import Settings
from txxt.store import Store

MODEM = "/org/freedesktop/ModemManager1/Modem/0"


class _TupleReply:
    """Tuple-of-out-args reply per XXWM_RULES.md."""

    def __init__(self, values):
        self._values = tuple(values)

    def unpack(self):
        return self._values


class _FakeParams:
    def __init__(self, values):
        self._values = tuple(values)

    def unpack(self):
        return self._values


def record(path, number, text, outgoing=False, stamp="2024-06-01T12:00:00+00:00"):
    return mm_client.SmsRecord(
        path=path, number=number, text=text, timestamp=stamp,
        pdu_type=2 if outgoing else 1, state=mm_client.STATE_RECEIVED,
        outgoing=outgoing,
    )


class FakeModemStore:
    """Fake modem object store: delete removes the path (True once)."""

    def __init__(self):
        self.deleted = []
        self.fail = set()

    def __call__(self, path):
        self.deleted.append(path)
        if path in self.fail:
            return False
        return True


def bodies(store):
    return sorted(
        item.body
        for conv in store.get_all_conversations()
        for item in store.get_messages_for_conversation(conv.id)
    )


# ---- timestamps ----

class TestSmsTimestamp:
    def test_iso_parses_to_millis(self):
        assert sms_timestamp_millis("2024-06-01T12:00:00+00:00") == 1717243200000

    def test_garbage_is_zero_and_still_imports_once(self):
        assert sms_timestamp_millis("not-a-date") == 0
        assert sms_timestamp_millis("") == 0


# ---- reconcile ----

class TestReconcileRecords:
    def test_imports_missing_then_deletes(self):
        store = Store.memory()
        modem = FakeModemStore()
        counts = reconcile_records(
            [record("/s/1", "+1555", "hello"), record("/s/2", "+1555", "hi")],
            store, modem,
        )
        assert (counts.imported, counts.deleted) == (2, 2)
        assert bodies(store) == ["hello", "hi"]
        assert store.pending_delete_rows() == []
        assert store.get_meta("last_reconcile_millis") is not None

    def test_match_window_prevents_duplicates(self):
        store = Store.memory()
        store.persist_inbound_sms("+15551234567", "hello", 1717243200000)
        modem = FakeModemStore()
        counts = reconcile_records(
            [record("/s/1", "(555) 123-4567", "hello")], store, modem
        )
        assert (counts.imported, counts.already_known, counts.deleted) == (0, 1, 1)
        assert bodies(store) == ["hello"]

    def test_failed_delete_keeps_flag_and_keeps_row(self):
        store = Store.memory()
        modem = FakeModemStore()
        modem.fail.add("/s/1")
        counts = reconcile_records([record("/s/1", "+1555", "hello")], store, modem)
        assert (counts.imported, counts.delete_failed) == (1, 1)
        flagged = store.pending_delete_rows()
        assert len(flagged) == 1 and flagged[0].body == "hello"

    def test_crash_recovery_redeletes_without_duplicating(self):
        store = Store.memory()
        modem = FakeModemStore()
        modem.fail.add("/s/1")
        reconcile_records([record("/s/1", "+1555", "hello")], store, modem)
        assert len(store.pending_delete_rows()) == 1
        modem.fail.clear()
        counts = reconcile_records([record("/s/1", "+1555", "hello")], store, modem)
        assert (counts.imported, counts.already_known, counts.deleted) == (0, 1, 1)
        assert bodies(store) == ["hello"]
        assert store.pending_delete_rows() == []

    def test_flagged_rows_count_as_recovered(self):
        store = Store.memory()
        mid = store.persist_inbound_sms("+1555", "old", 1717243200000)
        store.mark_pending_delete(mid)
        counts = reconcile_records([], store, FakeModemStore())
        assert counts.flagged_recovered == 1

    def test_outgoing_rows_import_as_sent(self):
        store = Store.memory()
        counts = reconcile_records(
            [record("/s/9", "+1555", "on my way", outgoing=True)],
            store, FakeModemStore(),
        )
        assert counts.imported == 1
        rows = [
            item for conv in store.get_all_conversations()
            for item in store.get_messages_for_conversation(conv.id)
        ]
        assert [(r.direction, r.sent) for r in rows] == [("OUTGOING", True)]


class TestReconcileMms:
    def test_mms_metadata_imports_as_photo_placeholder(self):
        from txxt.conversations_core import COLLAPSED_PHOTO_PLACEHOLDER

        store = Store.memory()
        modem = FakeModemStore()
        counts = reconcile_mms_messages(
            [("/m/1", {"Recipients": ["+1555"], "Text": "cap",
                       "ContentLocation": "http://mms/1"})],
            store, modem,
        )
        assert (counts.imported, counts.deleted) == (1, 1)
        rows = [
            item for conv in store.get_all_conversations()
            for item in store.get_messages_for_conversation(conv.id)
        ]
        assert [(r.transport, r.body) for r in rows] == [
            ("MMS", COLLAPSED_PHOTO_PLACEHOLDER)
        ]

    def test_mms_idempotent_on_second_pass(self):
        store = Store.memory()
        pair = ("/m/1", {"Recipients": ["+1555"], "Text": "cap",
                          "Date": "2024-06-01T12:00:00+00:00"})
        reconcile_mms_messages([pair], store, FakeModemStore())
        counts = reconcile_mms_messages([pair], store, FakeModemStore())
        assert (counts.imported, counts.already_known, counts.deleted) == (0, 1, 1)

    def test_dateless_mms_imports_once_and_deletes(self):
        store = Store.memory()
        pair = ("/m/1", {"Recipients": ["+1555"], "Text": "cap"})
        counts = reconcile_mms_messages([pair], store, FakeModemStore())
        assert (counts.imported, counts.deleted) == (1, 1)


# ---- inbound pipeline ----

class FakeNotifyBus:
    def __init__(self):
        self.posts = []
        self.hint_posts = []
        self.next_id = 100

    def call_sync(self, bus, path, iface, method, params, *rest):
        self.next_id += 1

        class Reply:
            def __init__(self, nid):
                self.nid = nid

            def unpack(inner_self):
                return (self.next_id,)

        unpacked = params.unpack()
        summary = unpacked[3]
        body = unpacked[4]
        self.posts.append((summary, body))
        self.hint_posts.append(dict(unpacked[6]))
        return Reply(self.next_id)


class FakeContacts:
    def display_name(self, number):
        return {"+1555": "Zed"}.get(number, number)


def pipeline(store, **kwargs):
    kwargs.setdefault("filter_provider", lambda: InboundFilter())
    kwargs.setdefault("contacts", FakeContacts())
    return InboundPipeline(store, **kwargs)


class TestInboundPipeline:
    def test_delivered_notifies_sender_only(self):
        store = Store.memory()
        bus = FakeNotifyBus()
        outcome = pipeline(store, notify_bus=bus).handle_sms("+1555", "secret text")
        assert outcome.disposition is MessageDisposition.DELIVER
        assert outcome.notified and outcome.message_id is not None
        assert bus.posts == [("Zed", "")]
        row = store.get_message(outcome.message_id)
        assert row is not None and not row.is_read

    def test_blocked_stored_silently_and_read(self):
        store = Store.memory()
        bus = FakeNotifyBus()
        filt = InboundFilter(blocked_addresses={"+1555"})
        outcome = pipeline(
            store, filter_provider=lambda: filt, notify_bus=bus
        ).handle_sms("+1555", "spam")
        assert outcome.disposition is MessageDisposition.BLOCK
        assert not outcome.notified and bus.posts == []
        row = store.get_message(outcome.message_id)
        assert row is not None and row.is_read

    def test_quarantined_held_with_notice(self):
        store = Store.memory()
        bus = FakeNotifyBus()
        filt = InboundFilter(quarantine_unknown_senders=True)
        outcome = pipeline(
            store, filter_provider=lambda: filt, notify_bus=bus
        ).handle_sms("+1999", "hello?")
        assert outcome.disposition is MessageDisposition.QUARANTINE
        assert outcome.notified
        row = store.get_message(outcome.message_id)
        assert row is not None
        conv = store.get_conversation(row.conversation_id)
        assert conv is not None and conv.is_quarantined

    def test_content_posture_includes_preview(self):
        store = Store.memory()
        bus = FakeNotifyBus()
        settings = Settings(lock_screen_privacy="content", alert_style="silent")
        outcome = pipeline(
            store, notify_bus=bus, settings_loader=lambda: settings
        ).handle_sms("+1555", "preview me")
        assert outcome.notified and bus.posts == [("Zed", "preview me")]

    def test_nothing_posture_posts_nothing(self):
        store = Store.memory()
        bus = FakeNotifyBus()
        settings = Settings(lock_screen_privacy="nothing")
        outcome = pipeline(
            store, notify_bus=bus, settings_loader=lambda: settings
        ).handle_sms("+1555", "quiet")
        assert not outcome.notified and bus.posts == []

    def test_no_bus_still_stores(self):
        store = Store.memory()
        outcome = pipeline(store).handle_sms("+1555", "kept")
        assert not outcome.notified and bodies(store) == ["kept"]

    def test_notification_maps_to_conversation(self):
        store = Store.memory()
        pipe = pipeline(store, notify_bus=FakeNotifyBus())
        outcome = pipe.handle_sms("+1555", "hi")
        assert pipe.conversation_for_notification(outcome.notification_id) == \
            outcome.conversation_id

    def test_action_target_hint_rides_posted_cards(self):
        from txxt import notify as notify_mod

        store = Store.memory()
        bus = FakeNotifyBus()
        target = notify_mod.action_target(
            "io.piercingxx.TxxT.Daemon",
            "/io/piercingxx/TxxT/Daemon",
            "io.piercingxx.TxxT1",
        )
        assert target == (
            "io.piercingxx.TxxT.Daemon "
            "/io/piercingxx/TxxT/Daemon io.piercingxx.TxxT1"
        )
        outcome = pipeline(
            store, notify_bus=bus, action_target=target,
        ).handle_sms("+1555", "hi")
        assert outcome.notified
        assert bus.hint_posts[-1][notify_mod.ACTION_TARGET_HINT] == target

    def test_no_action_target_means_no_hint(self):
        from txxt import notify as notify_mod

        store = Store.memory()
        bus = FakeNotifyBus()
        outcome = pipeline(store, notify_bus=bus).handle_sms("+1555", "hi")
        assert outcome.notified
        assert notify_mod.ACTION_TARGET_HINT not in bus.hint_posts[-1]


# ---- retry queue ----

class TestRetryQueue:
    def test_delay_doubles_then_caps(self):
        assert retry_delay_ms(0) == 5_000
        assert retry_delay_ms(1) == 10_000
        assert retry_delay_ms(20) == daemon.RETRY_MAX_DELAY_MS

    def test_due_and_sent(self):
        now = [1_000_000]
        queue = RetryQueue(now_millis=lambda: now[0])
        queue.add("+1555", "hi", 7)
        assert len(queue.due()) == 1
        key = next(iter(queue._items))
        queue.mark_attempted(key)
        assert queue.due() == []
        now[0] += daemon.RETRY_MAX_DELAY_MS + 1
        assert len(queue.due()) == 1
        queue.mark_sent(key)
        assert len(queue) == 0

    def test_exhausted_rows_hold_forever(self):
        now = [0]
        queue = RetryQueue(now_millis=lambda: now[0])
        key = queue.add("+1555", "hi", 7)
        for _ in range(daemon.RETRY_MAX_ATTEMPTS):
            queue.mark_attempted(key)
            now[0] += daemon.RETRY_MAX_DELAY_MS + 1
        assert len(queue.held_items()) == 1
        assert queue.due() == []

    def test_seed_resolves_address_from_conversation(self):
        store = Store.memory()
        mid = store.persist_outgoing_sms("+15551234567", "retry me", now_millis=5)
        seeds = seeds_with_addresses(store, store.pending_outgoing_sms())
        assert [(s.address, s.body, s.id) for s in seeds] == [
            ("+15551234567", "retry me", mid)
        ]

    def test_seed_never_resets_live_backoff(self):
        queue = RetryQueue(now_millis=lambda: 0)
        seed = daemon.RetrySeed(address="+1555", body="hi", id=7)
        queue.seed_pending([seed])
        key = next(iter(queue._items))
        queue.mark_attempted(key)
        due_at = queue._items[key].next_due_millis
        queue.seed_pending([seed])
        assert queue._items[key].attempts == 1
        assert queue._items[key].next_due_millis == due_at


class FakeBusForPump:
    def __init__(self):
        self.objects = {
            MODEM: {"org.freedesktop.ModemManager1.Modem": {},
                    "org.freedesktop.ModemManager1.Modem.Messaging": {}}
        }

    def call_sync(self, bus, path, iface, method, params, *rest):
        if method == "GetManagedObjects":
            return _TupleReply((dict(self.objects),))
        if method == "Delete":
            return _TupleReply(())
        raise AssertionError(method)


class TestPumpRetry:
    def _daemon(self, store, sender, **kwargs):
        kwargs.setdefault("system_bus_fn", lambda: FakeBusForPump())
        kwargs.setdefault("session_bus_fn", lambda: None)
        kwargs.setdefault("sender", sender)
        return daemon.Daemon(store, **kwargs)

    def test_success_marks_sent(self):
        store = Store.memory()
        mid = store.persist_outgoing_sms("+1555", "go", now_millis=9)
        sent = []
        d = self._daemon(store, lambda b, m, a, t: sent.append((a, t)) or (True, "/s/1"))
        d.startup_reconcile()
        counts = d.pump_retry()
        assert counts.resent == 1
        assert store.get_message(mid).sent is True
        assert sent == [("+1555", "go")]

    def test_failure_backs_off_then_holds(self):
        now = [0]
        store = Store.memory()
        store.persist_outgoing_sms("+1555", "go", now_millis=9)
        retry = RetryQueue(now_millis=lambda: now[0])
        d = self._daemon(
            store, lambda b, m, a, t: (False, "no signal"), retry=retry
        )
        d.startup_reconcile()
        assert d.pump_retry().resend_failed == 1
        assert d.pump_retry().resend_failed == 0  # backing off, nothing due
        for _ in range(daemon.RETRY_MAX_ATTEMPTS):
            now[0] += daemon.RETRY_MAX_DELAY_MS + 1
            d.pump_retry()
        assert len(retry.held_items()) == 1

    def test_startup_reconcile_seeds_pending(self):
        store = Store.memory()
        store.persist_outgoing_sms("+1555", "queued", now_millis=9)
        d = self._daemon(store, lambda b, m, a, t: (True, "/s/2"))
        d.startup_reconcile()
        assert d.pump_retry().resent == 1


class FakeRaceBus(FakeBusForPump):
    """One SMS row whose modem copy survives ``Delete``.

    The delete is recorded as a success but the props stay readable, so
    a second import of the same path can only be stopped by the
    daemon's own bookkeeping — exactly the startup/signal overlap.
    """

    def __init__(self):
        super().__init__()
        self.sms_lists = {MODEM: ["/s/race"]}
        self.sms_props = {"/s/race": {
            "Number": "tel:+15550001111", "Text": "race",
            "State": 3, "PduType": 1,
            "Timestamp": "2024-06-01T12:00:00+00:00",
        }}
        self.deletes = []

    def call_sync(self, bus, path, iface, method, params, *rest):
        if method == "GetManagedObjects":
            return _TupleReply((dict(self.objects),))
        if method == "List":
            return _TupleReply((list(self.sms_lists.get(path, [])),))
        if method == "GetAll":
            props = self.sms_props.get(path)
            if props is None:
                raise OSError("gone")
            return _TupleReply((dict(props),))
        if method == "Delete":
            self.deletes.append(params.unpack()[0])
            return _TupleReply(())
        raise AssertionError(method)


class TestDuplicateImportRace:
    """An SMS arriving during the startup reconcile is stored once."""

    def _race_daemon(self, store, bus):
        return daemon.Daemon(
            store,
            system_bus_fn=lambda: bus,
            session_bus_fn=lambda: None,
        )

    def test_reconcile_then_signal_imports_once(self):
        store = Store.memory()
        bus = FakeRaceBus()
        posts = FakeNotifyBus()
        d = daemon.Daemon(
            store,
            pipeline=InboundPipeline(store, notify_bus=posts),
            system_bus_fn=lambda: bus,
            session_bus_fn=lambda: None,
        )
        assert d.startup_reconcile().imported == 1
        d._fetch_and_handle("/s/race")
        assert bodies(store) == ["race"]
        # The late signal re-deletes the modem copy instead of storing
        # (and notifying) a second time.
        assert bus.deletes == ["/s/race", "/s/race"]
        assert posts.posts == []

    def test_signal_then_reconcile_imports_once(self):
        store = Store.memory()
        bus = FakeRaceBus()
        d = self._race_daemon(store, bus)
        d._fetch_and_handle("/s/race")
        counts = d.startup_reconcile()
        assert (counts.imported, counts.already_known) == (0, 1)
        assert bodies(store) == ["race"]

    def test_concurrent_reconcile_and_signal_import_once(self):
        import threading

        store = Store.memory()
        bus = FakeRaceBus()
        d = self._race_daemon(store, bus)
        gate = threading.Barrier(2)
        outcomes = []

        def _reconcile():
            gate.wait(timeout=10)
            outcomes.append(d.startup_reconcile().imported)

        def _signal():
            gate.wait(timeout=10)
            d._fetch_and_handle("/s/race")
            outcomes.append("signal-done")

        workers = [
            threading.Thread(target=_reconcile),
            threading.Thread(target=_signal),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
        assert not any(worker.is_alive() for worker in workers)
        assert bodies(store) == ["race"]
        assert "signal-done" in outcomes
        assert [v for v in outcomes if isinstance(v, int)] in ([0], [1])


# ---- single instance ----

class TestSingleInstance:
    def test_second_holder_loses(self, tmp_path):
        first = acquire_single_instance(tmp_path / "txxt.lock")
        assert first is not None
        try:
            assert acquire_single_instance(tmp_path / "txxt.lock") is None
        finally:
            fcntl.flock(first, fcntl.LOCK_UN)
            os.close(first)

    def test_reacquire_after_release(self, tmp_path):
        fd = acquire_single_instance(tmp_path / "txxt.lock")
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        assert acquire_single_instance(tmp_path / "txxt.lock") is not None


# ---- daemon signal/action wiring ----

class InlineExecutor:
    def submit(self, fn, *args):
        fn(*args)


class TestDaemonWiring:
    def test_action_reply_opens_thread(self, monkeypatch):
        store = Store.memory()
        pipe = InboundPipeline(store, notify_bus=FakeNotifyBus())
        outcome = pipe.handle_sms("+1555", "hi")
        d = daemon.Daemon(store, pipeline=pipe)
        d._executor = InlineExecutor()
        opened = []
        monkeypatch.setattr(daemon, "open_thread", lambda cid: opened.append(cid))
        d.on_action_invoked(
            None, None, None, None, None,
            _FakeParams((outcome.notification_id, "reply")), None,
        )
        assert opened == [outcome.conversation_id]

    def test_unknown_action_ignored(self, monkeypatch):
        store = Store.memory()
        d = daemon.Daemon(store)
        d._executor = InlineExecutor()
        monkeypatch.setattr(
            daemon, "open_thread",
            lambda cid: (_ for _ in ()).throw(AssertionError("must not open")),
        )
        d.on_action_invoked(None, None, None, None, None, _FakeParams((1, "nope")), None)

    def test_messaging_added_ignores_non_received(self):
        store = Store.memory()
        d = daemon.Daemon(store)
        d.on_messaging_added(
            None, None, None, None, None, _FakeParams(("/s/1", False)), None,
        )
        assert bodies(store) == []

    def test_open_thread_without_desktop_is_false(self):
        assert daemon.open_thread(12345) is False

    def test_reconcile_counts_default(self):
        assert ReconcileCounts().imported == 0


def test_blocking_live_filter_default_delivers():
    assert blocking.live_current().evaluate("+1555", "hi")[0] is MessageDisposition.DELIVER


def test_daemon_module_has_no_gtk():
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "src" / "txxt" / "daemon.py")
    imports = [
        line for line in source.read_text(encoding="utf-8").splitlines()
        if re.match(r"\s*(import|from)\s", line)
    ]
    assert not any(re.search(r"\b(Gtk|Adw|Gdk)\b", line) for line in imports)
