"""L3 modem-client tests: MM Messaging + mmsd-tng against faithful fakes.

The fakes model the REAL signatures and reply shapes (XXWM_RULES.md):
every ``call_sync`` reply ``unpack()``s to a TUPLE of out-args
(``GetAll`` -> ``({props},)``, ``List`` -> ``([paths],)``,
``Create`` -> ``(path,)``), ``call`` callbacks take exactly 2 args (no
trailing ``user_data``), and signal handlers take the exact 7
``signal_subscribe`` args.
"""

import inspect

import pytest
from gi.repository import Gio

from txxt import mm_client
from txxt.mm_client import (
    MESSAGING_IFACE,
    MM_BUS_NAME,
    MODEM_IFACE,
    OBJMGR_IFACE,
    PROPS_IFACE,
    SMS_IFACE,
    MmsAdapter,
)


class FakeError(Exception):
    @property
    def message(self):
        return str(self)


class FakeReply:
    def __init__(self, values):
        self._values = tuple(values)

    def unpack(self):
        return self._values


class FakeParams:
    def __init__(self, values):
        self._values = tuple(values)

    def unpack(self):
        return self._values


MODEM_A = "/org/freedesktop/ModemManager1/Modem/0"
MODEM_B = "/org/freedesktop/ModemManager1/Modem/3"


def modem_objects(*modems):
    objects = {}
    for path, messaging in modems:
        ifaces = {MODEM_IFACE: {}}
        if messaging:
            ifaces[MESSAGING_IFACE] = {}
        objects[path] = ifaces
    return objects


class FakeSystemBus:
    """System-bus fake serving the MM1 surface the client touches."""

    def __init__(self):
        self.calls = []
        self.subscriptions = []
        self.objects = modem_objects((MODEM_A, True))
        self.sms_lists = {MODEM_A: []}
        self.sms_props = {}
        self.failures = set()
        self.async_failures = set()

    # -- sync --

    def call_sync(self, bus, path, iface, method, params, *rest):
        self.calls.append((bus, path, iface, method, params))
        if (path, iface, method) in self.failures or (iface, method) in self.failures:
            raise FakeError("transport down")
        if (iface, method) == (OBJMGR_IFACE, "GetManagedObjects"):
            return FakeReply((dict(self.objects),))
        if (iface, method) == (MESSAGING_IFACE, "List"):
            return FakeReply((list(self.sms_lists.get(path, [])),))
        if (iface, method) == (MESSAGING_IFACE, "Create"):
            created = f"{path}/SMS/99"
            self.sms_lists.setdefault(path, []).append(created)
            return FakeReply((created,))
        if (iface, method) == (PROPS_IFACE, "GetAll"):
            return FakeReply((dict(self.sms_props.get(path, {})),))
        if (iface, method) in (
            (SMS_IFACE, "Send"),
            (MESSAGING_IFACE, "Delete"),
        ):
            return FakeReply(())
        raise AssertionError(f"unexpected call {(path, iface, method)}")

    # -- async --

    def call(self, bus, path, iface, method, params, *rest):
        self.calls.append((bus, path, iface, method, params))
        callback = rest[-1]
        if (iface, method) in self.async_failures:
            callback(self, _FailingResult())
            return 1
        if (iface, method) == (MESSAGING_IFACE, "Create"):
            created = f"{path}/SMS/7"
            callback(self, _OkResult(FakeReply((created,))))
        elif (iface, method) == (SMS_IFACE, "Send"):
            callback(self, _OkResult(FakeReply(())))
        else:
            raise AssertionError(f"unexpected async call {(path, iface, method)}")
        return 1

    def signal_subscribe(
        self, sender, iface, member, path, arg0, flags, handler, user_data
    ):
        self.subscriptions.append(
            (sender, iface, member, path, arg0, handler, user_data)
        )
        return 42


class _OkResult:
    def __init__(self, reply):
        self._reply = reply

    def __call__(self):
        return self._reply


class _FailingResult:
    pass


# Gio's async finish is a method on the connection in real code
# (conn.call_finish(result)); the fake bus doubles as the connection.
FakeSystemBus.call_finish = lambda self, result: (
    (_ for _ in ()).throw(FakeError("send refused"))
    if isinstance(result, _FailingResult)
    else result()
)


def sms_props(number="tel:+15551234567", text="hello", state=3, pdu=1):
    return {
        "Number": number,
        "Text": text,
        "State": state,
        "PduType": pdu,
        "Timestamp": "2024-01-01T00:00:00+00:00",
    }


# ---- modem resolution ----

class TestResolveModem:
    def test_picks_messaging_modem_over_plain_modem(self):
        objects = modem_objects((MODEM_B, False), (MODEM_A, True))
        assert mm_client.pick_modem_path(objects) == MODEM_A

    def test_sorted_order_is_deterministic(self):
        objects = modem_objects((MODEM_B, True), (MODEM_A, True))
        assert mm_client.pick_modem_path(objects) == MODEM_A

    def test_falls_back_to_modem_without_messaging(self):
        assert mm_client.pick_modem_path(modem_objects((MODEM_B, False))) == MODEM_B

    def test_none_when_no_modem(self):
        assert mm_client.pick_modem_path({"/x": {"other.Iface": {}}}) is None

    def test_reindexes_after_reset(self):
        bus = FakeSystemBus()
        assert mm_client.resolve_modem_path(bus) == MODEM_A
        bus.objects = modem_objects((MODEM_B, True))
        assert mm_client.resolve_modem_path(bus) == MODEM_B

    def test_none_on_transport_failure(self):
        bus = FakeSystemBus()
        bus.failures.add((OBJMGR_IFACE, "GetManagedObjects"))
        assert mm_client.resolve_modem_path(bus) is None
        assert mm_client.resolve_modem_path(None) is None


# ---- outbound ----

class TestOutbound:
    def test_create_sends_only_number_and_text(self):
        from gi.repository import GLib

        bus = FakeSystemBus()
        path = mm_client.create_sms(bus, MODEM_A, "+1555", "hi")
        assert path == f"{MODEM_A}/SMS/99"
        assert bus.calls[0][0:4] == (MM_BUS_NAME, MODEM_A, MESSAGING_IFACE, "Create")
        sent = bus.calls[0][4].unpack()[0]
        assert {str(k) for k in sent} == {"number", "text"}
        assert sent["number"] == "+1555"
        assert sent["text"] == "hi"
        assert "validity" not in {str(k).lower() for k in sent}
        _ = GLib

    def test_create_and_send_round_trip(self):
        bus = FakeSystemBus()
        ok, path = mm_client.create_and_send(bus, MODEM_A, "+1555", "hi")
        assert ok and path.endswith("/SMS/99")
        methods = [(c[2], c[3]) for c in bus.calls]
        assert methods == [(MESSAGING_IFACE, "Create"), (SMS_IFACE, "Send")]

    def test_create_failure_reports_detail(self):
        bus = FakeSystemBus()
        bus.failures.add((MESSAGING_IFACE, "Create"))
        ok, detail = mm_client.create_and_send(bus, MODEM_A, "+1555", "hi")
        assert not ok and detail == "transport down"

    def test_async_send_completes_without_user_data(self):
        bus = FakeSystemBus()
        done = []
        mm_client.send_sms_async(
            bus, MODEM_A, "+1555", "hi", lambda ok, detail: done.append((ok, detail))
        )
        assert len(done) == 1 and done[0][0] is True
        for _, _, _, _, params in bus.calls:
            assert params is None or not isinstance(params, tuple)

    def test_async_send_failure_reaches_callback_once(self):
        bus = FakeSystemBus()
        bus.async_failures.add((SMS_IFACE, "Send"))
        done = []
        mm_client.send_sms_async(
            bus, MODEM_A, "+1555", "hi", lambda ok, detail: done.append((ok, detail))
        )
        assert len(done) == 1 and done[0][0] is False


# ---- inbound records ----

class TestRecords:
    def test_record_strips_tel_and_reads_state(self):
        record = mm_client.record_from_props("/s/1", sms_props())
        assert record is not None
        assert record.number == "+15551234567"
        assert record.text == "hello"
        assert record.state == mm_client.STATE_RECEIVED
        assert record.outgoing is False

    def test_half_written_rows_are_skipped(self):
        assert mm_client.record_from_props("/s/1", sms_props(text="")) is None
        assert mm_client.record_from_props("/s/1", sms_props(number="")) is None

    def test_submit_pdu_reads_outbound(self):
        record = mm_client.record_from_props("/s/1", sms_props(pdu=2))
        assert record is not None and record.outgoing is True

    def test_list_records_skips_broken_rows(self):
        bus = FakeSystemBus()
        bus.sms_lists = {MODEM_A: ["/s/1", "/s/2", "/s/3"]}
        bus.sms_props = {
            "/s/1": sms_props(text="one"),
            "/s/2": sms_props(text=""),
            "/s/3": sms_props(number="tel:+15550001111", text="three"),
        }
        records = mm_client.list_records(bus, MODEM_A)
        assert records is not None
        assert [(r.number, r.text) for r in records] == [
            ("+15551234567", "one"), ("+15550001111", "three"),
        ]

    def test_list_none_on_transport_failure(self):
        bus = FakeSystemBus()
        bus.failures.add((MESSAGING_IFACE, "List"))
        assert mm_client.list_records(bus, MODEM_A) is None


# ---- Added signal + RECEIVED wait ----

class TestAddedSignal:
    def test_added_params_unpack_to_path_and_flag(self):
        path, received = mm_client.parse_added_params(FakeParams(("/s/5", True)))
        assert (path, received) == ("/s/5", True)

    def test_added_params_reject_garbage(self):
        assert mm_client.parse_added_params(FakeParams(())) == (None, False)

    def test_wait_returns_once_received(self):
        states = [{"State": 2}, {"State": 2}, {"State": 3, "Text": "whole"}]
        sleeps = []
        result = mm_client.wait_received(
            lambda: states.pop(0), sleeps.append, max_polls=5
        )
        assert result == {"State": 3, "Text": "whole"}
        assert sleeps == [mm_client.receive_backoff(0), mm_client.receive_backoff(1)]

    def test_backoff_grows_then_caps(self):
        delays = [mm_client.receive_backoff(n) for n in range(6)]
        assert delays[0] < delays[1] < delays[2]
        assert delays[-1] == mm_client.MAX_POLL_DELAY_S

    def test_wait_gives_up_after_max_polls(self):
        polls = []
        result = mm_client.wait_received(
            lambda: polls.append(1) or {"State": 2}, lambda s: None, max_polls=3
        )
        assert result is None
        assert len(polls) == 4

    def test_subscribe_uses_seven_arg_shape_without_user_data(self):
        bus = FakeSystemBus()
        seen = []

        def handler(conn, sender, path, iface, signal, params, user_data):
            seen.append((path, params.unpack()))

        sub = mm_client.subscribe_messaging_added(bus, handler)
        assert sub == 42
        sender, iface, member, path, arg0, kept, user_data = bus.subscriptions[0]
        assert (sender, iface, member) == (MM_BUS_NAME, MESSAGING_IFACE, "Added")
        assert user_data is None
        assert len(inspect.signature(handler).parameters) == 7
        kept(None, sender, "/m", iface, member, FakeParams(("/s/1", True)), None)
        assert seen == [("/m", ("/s/1", True))]


# ---- delete-after-import ----

class FakeStoreFlags:
    def __init__(self):
        self.flagged = []
        self.cleared = []

    def mark_pending_delete(self, message_id):
        self.flagged.append(message_id)

    def clear_pending_delete(self, message_id):
        self.cleared.append(message_id)


class TestDeleteAfterImport:
    def test_flag_cleared_only_on_success(self):
        bus = FakeSystemBus()
        store = FakeStoreFlags()
        assert mm_client.delete_sms_after_import(bus, MODEM_A, "/s/1", store, 11)
        assert store.flagged == [11] and store.cleared == [11]
        assert bus.calls[-1][2:4] == (MESSAGING_IFACE, "Delete")

    def test_flag_survives_failed_delete(self):
        bus = FakeSystemBus()
        bus.failures.add((MESSAGING_IFACE, "Delete"))
        store = FakeStoreFlags()
        assert not mm_client.delete_sms_after_import(bus, MODEM_A, "/s/1", store, 11)
        assert store.flagged == [11] and store.cleared == []


# ---- mmsd-tng adapter (real org.ofono.mms shapes, pinned by these tests) ----

class FakeSessionBus(FakeSystemBus):
    """Session-bus fake serving the documented org.ofono.mms shapes.

    ``GetMessages`` returns ``a(oa{sv})`` structs, ``SendMessage`` takes
    ``(as, v, a(sss))`` (recipients, smil-or-options variant,
    attachments), and message ``Attachments`` are documented
    ``(id, content-type, file, offset, length)`` 5-tuples.
    """

    def __init__(self):
        super().__init__()
        self.mms_paths = ["/org/ofono/mms/message/1"]
        self.mms_props = {
            "/org/ofono/mms/message/1": {
                "Sender": "+15551234567", "Text": "caption",
                "Status": "received",
            }
        }

    def call_sync(self, bus, path, iface, method, params, *rest):
        self.calls.append((bus, path, iface, method, params))
        if (path, iface, method) in self.failures or (iface, method) in self.failures:
            raise FakeError("mmsd away")
        if method == "GetMessages":
            assert path == mm_client.MMS_SERVICE_PATH, path
            return FakeReply((
                [(p, dict(self.mms_props.get(p, {}))) for p in self.mms_paths],
            ))
        if method == "GetAll":
            return FakeReply((dict(self.mms_props.get(path, {})),))
        if method == "SendMessage":
            assert path == mm_client.MMS_SERVICE_PATH, path
            recipients, smil, attachments = params.unpack()
            assert isinstance(recipients, list) and recipients
            assert isinstance(smil, str)
            for entry in attachments:
                name, content_type, staged = entry
                assert name and content_type and staged
            return FakeReply(("/org/ofono/mms/message/9",))
        if method in ("MarkRead", "Delete"):
            return FakeReply(())
        return super().call_sync(bus, path, iface, method, params, *rest)


class TestMmsAdapter:
    def test_list_and_get_shape(self):
        adapter = MmsAdapter(FakeSessionBus())
        assert adapter.available() is True
        assert adapter.list_messages() == ["/org/ofono/mms/message/1"]
        props = adapter.get_message("/org/ofono/mms/message/1")
        assert props is not None and props["Text"] == "caption"

    def test_unavailable_when_service_away(self):
        bus = FakeSessionBus()
        bus.failures.add(("org.ofono.mms.Service", "GetMessages"))
        assert MmsAdapter(bus).available() is False

    def test_send_returns_path_or_detail(self):
        bus = FakeSessionBus()
        adapter = MmsAdapter(bus)
        staged = [("image_0.jpg", "image/jpeg", "/tmp/p.jpg")]
        ok, path = adapter.send_message(["+1555"], "<smil/>", staged)
        assert ok and path == "/org/ofono/mms/message/9"
        sent = bus.calls[-1][4].unpack()
        assert sent[0] == ["+1555"] and sent[1] == "<smil/>" and sent[2] == staged

    def test_send_message_payload_is_recipients_smil_attachments(self):

        bus = FakeSessionBus()
        adapter = MmsAdapter(bus)
        staged = [("text_0.txt", "text/plain", "/tmp/t.txt")]
        ok, _path = adapter.send_message(["+1555"], "<smil>hi</smil>", staged)
        assert ok
        payload = bus.calls[-1][4]
        assert payload.get_type_string() == "(asva(sss))"
        recipients, smil, attachments = payload.unpack()
        assert recipients == ["+1555"]
        assert smil == "<smil>hi</smil>"
        assert list(attachments) == staged

    def test_send_message_variant_builds_under_real_glib(self):
        from gi.repository import GLib

        payload = GLib.Variant(
            "(asva(sss))",
            (["+1555"], GLib.Variant("s", "<smil/>"), [("a.jpg", "image/jpeg", "/f")]),
        )
        assert payload.get_type_string() == "(asva(sss))"
        assert payload.unpack() == (
            ["+1555"], "<smil/>", [("a.jpg", "image/jpeg", "/f")],
        )

    def test_send_failure_reports_detail(self):
        bus = FakeSessionBus()
        bus.failures.add(("org.ofono.mms.Service", "SendMessage"))
        ok, detail = MmsAdapter(bus).send_message(["+1555"], "", [])
        assert not ok and detail == "mmsd away"

    def test_mark_read_and_delete(self):
        adapter = MmsAdapter(FakeSessionBus())
        assert adapter.mark_read("/org/ofono/mms/message/1") is True
        assert adapter.delete_message("/org/ofono/mms/message/1") is True

    def test_mms_added_subscription_shape(self):
        bus = FakeSessionBus()
        seen = []

        def handler(conn, sender, path, iface, signal, params, user_data):
            seen.append(params.unpack()[0])

        assert MmsAdapter(bus).subscribe_added(handler) == 42
        assert bus.subscriptions[0][5] is handler
        assert bus.subscriptions[0][6] is None
        assert len(inspect.signature(handler).parameters) == 7


def test_dbus_call_flags_present():
    assert Gio.DBusCallFlags.NONE is not None


def test_async_create_dispatch_failure_calls_back():
    class DeadBus(FakeSystemBus):
        def call(self, *args, **kwargs):
            raise FakeError("dead")

    done = []
    mm_client.send_sms_async(
        DeadBus(), MODEM_A, "+1555", "hi", lambda ok, d: done.append(ok)
    )
    assert done == [False]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_gio_tuple_unpack_contract():
    from gi.repository import GLib

    reply = GLib.Variant("(ao)", ([MODEM_A],))
    assert reply.unpack()[0] == [MODEM_A]
