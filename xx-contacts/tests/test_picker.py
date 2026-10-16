"""L6 picker tests: kinds/rows pure logic plus the held ``Pick`` reply.

Fakes mirror the real library exactly: parameters are real
``GLib.Variant`` tuples, replies must be tuples, and the invocation
exposes only ``return_value`` / ``return_dbus_error`` — same shapes as
``test_service.py``.
"""

import pytest
from gi.repository import GLib

from xx_contacts import picker as picker_module
from xx_contacts import service_client
from xx_contacts.phones import LookupIndex
from xx_contacts.service import (
    ERROR_INVALID_ARGUMENT,
    OBJECT_PATH,
    ContactsService,
)
from xx_contacts.store import (
    ContactData,
    ContactStore,
    EmailEntry,
    PhoneEntry,
)


class FakeInvocation:
    def __init__(self):
        self.reply = None
        self.error = None

    def return_value(self, parameters):
        self.reply = parameters

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class FakeConnection:
    def __init__(self):
        self.signals = []

    def emit_signal(self, destination, path, interface, signal, parameters):
        self.signals.append((destination, path, interface, signal,
                             parameters))
        return True


@pytest.fixture
def service():
    store = ContactStore(":memory:")
    handle = ContactsService(store, LookupIndex(), "US")
    handle._connection = FakeConnection()
    yield handle
    store.close()


def _seed(service) -> tuple[str, str]:
    ada = ContactData(
        display_name="Ada Lovelace", given="Ada", family="Lovelace",
        phones=[PhoneEntry(number_raw="+1 555 0100",
                           number_e164="+15550100",
                           number_digits="15550100",
                           type="CELL", is_primary=True),
                PhoneEntry(number_raw="+1 555 0101",
                           number_e164="+15550101",
                           number_digits="15550101", type="HOME")],
        emails=[EmailEntry(address="ada@example.org", type="WORK")])
    bob = ContactData(display_name="Bob", phones=[])
    ada_uid = service._store.create(ada)
    bob_uid = service._store.create(bob)
    service._rebuild_index()
    return ada_uid, bob_uid


def _call(service, method, signature, *args):
    invocation = FakeInvocation()
    params = GLib.Variant(signature, args) if signature else None
    service.handle_method_call(None, ":1.1", OBJECT_PATH,
                               "io.piercingxx.Contacts1", method,
                               params, invocation)
    return invocation


# ------------------------------------------------------------------ pure kind

def test_normalize_kind_accepts_each_kind_case_insensitively():
    assert picker_module.normalize_kind("phone") == "phone"
    assert picker_module.normalize_kind(" Email ") == "email"
    assert picker_module.normalize_kind("CONTACT") == "contact"


def test_normalize_kind_rejects_anything_else():
    for bad in ("pager", "", None, "phone_v2", "postal"):
        with pytest.raises(ValueError):
            picker_module.normalize_kind(bad)


def test_primary_phone_prefers_the_primary_row(service):
    ada_uid, _bob = _seed(service)
    record = service._store.get(ada_uid)
    assert picker_module.primary_phone(record) == "+1 555 0100"
    assert picker_module.primary_email(record) == "ada@example.org"


def test_pick_value_carries_number_address_or_name(service):
    ada_uid, bob_uid = _seed(service)
    ada = service._store.get(ada_uid)
    assert picker_module.pick_value("phone", ada) == (
        ada_uid, "+1 555 0100")
    assert picker_module.pick_value("email", ada) == (
        ada_uid, "ada@example.org")
    assert picker_module.pick_value("contact", ada) == (
        ada_uid, "Ada Lovelace")
    assert picker_module.pick_value("phone", service._store.get(
        bob_uid)) == (bob_uid, "")


def test_build_rows_flattens_values_and_skips_missing(service):
    ada_uid, bob_uid = _seed(service)
    records = service._store.all_records()
    contact_rows = picker_module.build_pick_rows("contact", records)
    assert [(row.uid, row.value) for row in contact_rows] == [
        (ada_uid, "Ada Lovelace"), (bob_uid, "Bob")]
    phone_rows = picker_module.build_pick_rows("phone", records)
    assert [(row.uid, row.value) for row in phone_rows] == [
        (ada_uid, "+1 555 0100"), (ada_uid, "+1 555 0101")]
    email_rows = picker_module.build_pick_rows("email", records)
    assert [(row.uid, row.value) for row in email_rows] == [
        (ada_uid, "ada@example.org")]


def test_build_rows_rejects_bad_kind():
    with pytest.raises(ValueError):
        picker_module.build_pick_rows("pager", [])


def test_filter_rows_matches_title_or_detail(service):
    ada_uid, _bob = _seed(service)
    rows = picker_module.build_pick_rows(
        "phone", service._store.all_records())
    assert picker_module.filter_pick_rows(rows, "") == rows
    assert [row.value for row in picker_module.filter_pick_rows(
        rows, "0101")] == ["+1 555 0101"]
    assert picker_module.filter_pick_rows(rows, "grace") == []
    assert {row.uid for row in picker_module.filter_pick_rows(
        rows, "aDa")} == {ada_uid}


# ------------------------------------------------------- held Pick dispatch

def test_pick_replies_on_pick_and_emits_no_changed(service):
    ada_uid, _bob = _seed(service)
    held = {}

    def present(kind, rows, on_done):
        held["kind"] = kind
        held["rows"] = rows
        held["done"] = on_done
        return True

    service.pick_presenter = present
    invocation = _call(service, "Pick", "(s)", "phone")
    assert invocation.error is None and invocation.reply is None
    assert service.pick_pending()
    assert held["kind"] == "phone"
    assert [row.value for row in held["rows"]] == [
        "+1 555 0100", "+1 555 0101"]
    held["done"](ada_uid, "+1 555 0101")
    assert invocation.error is None
    assert invocation.reply.get_type_string() == "(ss)"
    assert invocation.reply.unpack() == (ada_uid, "+1 555 0101")
    assert not service.pick_pending()
    assert service._connection.signals == []


def test_pick_cancel_replies_empty(service):
    _seed(service)
    held = {}

    def present(_kind, _rows, on_done):
        held["done"] = on_done
        return True

    service.pick_presenter = present
    invocation = _call(service, "Pick", "(s)", "contact")
    held["done"]("", "")
    assert invocation.error is None
    assert invocation.reply.unpack() == ("", "")
    assert not service.pick_pending()


def test_pick_rejects_unknown_kind(service):
    invocation = _call(service, "Pick", "(s)", "pager")
    assert invocation.reply is None
    assert invocation.error[0] == ERROR_INVALID_ARGUMENT
    assert not service.pick_pending()


def test_pick_rejects_wrong_arity(service):
    assert _call(service, "Pick", None).error[0] == ERROR_INVALID_ARGUMENT


def test_second_pick_while_open_fails(service):
    _seed(service)
    held = {}

    def present(_kind, _rows, on_done):
        held["done"] = on_done
        return True

    service.pick_presenter = present
    first = _call(service, "Pick", "(s)", "phone")
    second = _call(service, "Pick", "(s)", "email")
    assert second.reply is None
    assert second.error[0] == ERROR_INVALID_ARGUMENT
    assert service.pick_pending()
    held["done"]("", "")
    assert first.reply.unpack() == ("", "")
    assert not service.pick_pending()


# ------------------------------------------------------- client pick helpers

class FakeReply:
    def __init__(self, values):
        self._values = values

    def unpack(self):
        return self._values


class FakeBusConnection:
    def __init__(self, reply=(), error=None):
        self.calls = []
        self._reply = reply
        self._error = error

    def call_sync(self, bus_name, path, interface, method, parameters,
                  reply_type, flags, timeout, cancellable):
        self.calls.append((bus_name, path, interface, method,
                           parameters.unpack() if parameters is not None
                           else (), reply_type, timeout))
        if self._error is not None:
            raise self._error
        return FakeReply(self._reply)


def test_pick_sync_returns_uid_and_value_with_human_timeout():
    conn = FakeBusConnection(reply=("uid-9", "+1 555 0100"))
    assert service_client.pick_sync(conn, "phone") == (
        "uid-9", "+1 555 0100")
    _bus, _path, _iface, method, args, reply_type, timeout = conn.calls[0]
    assert method == "Pick"
    assert args == ("phone",)
    assert reply_type.dup_string() == "(ss)"
    assert timeout == service_client.PICK_TIMEOUT_MS
    assert timeout > service_client.CALL_TIMEOUT_MS


def test_pick_sync_maps_failure_onto_runtime_error():
    conn = FakeBusConnection(error=GLib.Error("timed out"))
    with pytest.raises(RuntimeError, match="not reachable"):
        service_client.pick_sync(conn, "contact")


def test_pick_async_wires_method_timeout_and_mapping(monkeypatch):
    seen = {}

    def fake_call_async(method, parameters, reply_signature, on_done,
                        on_error=None,
                        timeout_ms=service_client.CALL_TIMEOUT_MS):
        seen["method"] = method
        seen["args"] = parameters.unpack()
        seen["reply_signature"] = reply_signature
        seen["timeout_ms"] = timeout_ms
        on_done(("uid-3", "ada@example.org"))

    monkeypatch.setattr(service_client, "_call_async", fake_call_async)
    got = []
    service_client.pick_async("email", got.append)
    assert got == [("uid-3", "ada@example.org")]
    assert seen["method"] == "Pick"
    assert seen["args"] == ("email",)
    assert seen["reply_signature"] == "(ss)"
    assert seen["timeout_ms"] == service_client.PICK_TIMEOUT_MS
