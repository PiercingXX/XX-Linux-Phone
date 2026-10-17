"""D-Bus service tests: Calendar1 dispatch over Gio shapes (L2).

Fakes mirror the real library exactly: parameters are real GLib.Variant
tuples, replies must be tuples (``reply.unpack()[0]`` is the first out-arg),
and the invocation exposes only ``return_value`` / ``return_dbus_error``.
Slow bus-level tests (activation, signals, scopes over the wire) live in
``test_service_dbus.py``; this file covers the operations headless.
"""

import time

import pytest
from gi.repository import Gio, GLib

from xx_calendar.service import (
    BOOTSTRAP_CALENDAR_ID,
    BUS_NAME,
    ERROR_INVALID_ARGUMENT,
    IDLE_EXIT_SECONDS,
    INTERFACE,
    INTROSPECTION_XML,
    OBJECT_PATH,
    CalendarService,
    emit_changed,
    resolve_idle_timeout,
)
from xx_calendar.main import APP_ID
from xx_calendar.store import CalendarStore

WEEKLY_ICS = "\r\n".join([
    "BEGIN:VEVENT", "UID:weekly-1", "DTSTAMP:20260101T000000Z",
    "DTSTART:20260302T090000Z", "DTEND:20260302T100000Z",
    "RRULE:FREQ=WEEKLY;BYDAY=MO", "SUMMARY:Standup", "END:VEVENT"])

ONE_SHOT_ICS = "\r\n".join([
    "BEGIN:VEVENT", "UID:one-1", "DTSTAMP:20260101T000000Z",
    "DTSTART:20260302T090000Z", "DTEND:20260302T100000Z",
    "SUMMARY:Solo", "END:VEVENT"])


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
    store = CalendarStore(":memory:")
    handle = CalendarService(store, start_worker=False)
    handle._connection = FakeConnection()
    yield handle
    handle.stop()
    store.close()


def _window():
    from datetime import datetime

    def utc(iso):
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00"))
                   .timestamp() * 1000)

    return utc("2026-03-01T00:00:00Z"), utc("2026-05-01T00:00:00Z")


def _starts(instances):
    from datetime import datetime, timezone

    return [datetime.fromtimestamp(i["start"].get_int64() / 1000,
                                   timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            for i in instances]


def test_introspection_lists_full_interface():
    node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
    assert len(node.interfaces) == 1
    iface = node.interfaces[0]
    assert iface.name == INTERFACE
    methods = {m.name: m for m in iface.methods}
    assert set(methods) == {"ListCalendars", "ListInstances", "Get",
                            "Create", "Update", "Delete", "List",
                            "Import", "Export"}
    assert methods["ListInstances"].in_args[0].signature == "x"
    assert methods["ListInstances"].in_args[2].signature == "as"
    assert methods["ListInstances"].out_args[0].signature == "aa{sv}"
    assert methods["ListCalendars"].out_args[0].signature == "aa{sv}"
    assert methods["Get"].in_args[0].signature == "s"
    assert methods["Create"].in_args[0].signature == "s"
    assert methods["Update"].in_args[2].signature == "s"
    assert methods["Update"].in_args[3].signature == "x"
    assert methods["Delete"].in_args[2].signature == "x"
    assert methods["List"].in_args[0].signature == "t"
    assert methods["List"].out_args[0].signature == "t"
    assert methods["Import"].in_args[0].signature == "s"
    assert methods["Import"].in_args[1].signature == "s"
    assert [a.signature for a in methods["Import"].out_args] == ["u", "u", "u"]
    assert methods["Export"].in_args[0].signature == "as"
    assert methods["Export"].out_args[0].signature == "s"
    signals = {s.name: s for s in iface.signals}
    assert set(signals) == {"Changed"}
    assert signals["Changed"].args[0].signature == "t"
    assert signals["Changed"].args[1].signature == "as"


def test_service_never_owns_gui_app_id():
    assert BUS_NAME == "io.piercingxx.Calendar"
    assert APP_ID == "io.piercingxx.XXCalendar"
    assert BUS_NAME != APP_ID
    assert OBJECT_PATH == "/io/piercingxx/Calendar"


def test_bootstrap_calendar_exists(service):
    calendars = service.list_calendars()
    ids = [c["id"].get_string() for c in calendars]
    assert BOOTSTRAP_CALENDAR_ID in ids


def test_create_get_round_trip(service):
    uid = service.create("local", ONE_SHOT_ICS)
    assert uid == "one-1"
    assert "SUMMARY:Solo" in service.get(uid)
    start, end = _window()
    found = service.list_instances(start, end, ["local"])
    assert _starts(found) == ["2026-03-02T09:00:00Z"]
    assert found[0]["title"].get_string() == "Solo"


def test_create_rejects_unknown_calendar_and_non_vevent(service):
    with pytest.raises(LookupError):
        service.create("missing", ONE_SHOT_ICS)
    with pytest.raises(ValueError):
        service.create("local", "SUMMARY:lonely line\r\n")


def test_get_update_delete_unknown_uid(service):
    with pytest.raises(LookupError):
        service.get("nope")
    with pytest.raises(LookupError):
        service.update("nope", ONE_SHOT_ICS, "all", 0)
    with pytest.raises(LookupError):
        service.delete("nope", "all", 0)


def test_update_rejects_bad_scope(service):
    service.create("local", ONE_SHOT_ICS)
    with pytest.raises(ValueError):
        service.update("one-1", ONE_SHOT_ICS, "someday", 0)
    with pytest.raises(ValueError):
        service.delete("one-1", "someday", 0)


def test_count_split_is_refused(service):
    service.create("local", WEEKLY_ICS.replace(
        "RRULE:FREQ=WEEKLY;BYDAY=MO", "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=8"))
    start, end = _window()
    victim = service.list_instances(start, end, ["local"])[2]
    with pytest.raises(ValueError, match="COUNT"):
        service.update("weekly-1", WEEKLY_ICS, "this-and-following",
                       victim["start"].get_int64())
    with pytest.raises(ValueError, match="COUNT"):
        service.delete("weekly-1", "this-and-following",
                       victim["start"].get_int64())


def test_scoped_edit_this_moves_one_instance(service):
    service.create("local", WEEKLY_ICS)
    start, end = _window()
    before = service.list_instances(start, end, ["local"])
    assert len(before) == 9
    second = before[1]["start"].get_int64()
    moved = ONE_SHOT_ICS.replace("UID:one-1", "UID:weekly-1").replace(
        "DTSTART:20260302T090000Z", "DTSTART:20260309T110000Z").replace(
        "DTEND:20260302T100000Z", "DTEND:20260309T120000Z").replace(
        "SUMMARY:Solo", "SUMMARY:Moved")
    service.update("weekly-1", moved, "this", second)
    after = service.list_instances(start, end, ["local"])
    assert _starts(after)[1] == "2026-03-09T11:00:00Z"
    assert after[1]["is_override"].get_boolean() is True
    assert after[1]["title"].get_string() == "Moved"
    assert len(after) == 9


def test_scoped_edit_following_splits_series(service):
    service.create("local", WEEKLY_ICS)
    start, end = _window()
    before = service.list_instances(start, end, ["local"])
    split_at = before[2]["start"].get_int64()
    tail = WEEKLY_ICS.replace("SUMMARY:Standup", "SUMMARY:Tail").replace(
        "DTSTART:20260302T090000Z", "DTSTART:20260316T090000Z").replace(
        "DTEND:20260302T100000Z", "DTEND:20260316T100000Z")
    service.update("weekly-1", tail, "this-and-following", split_at)
    parent_ics = service.get("weekly-1")
    assert "UNTIL=20260316T085959Z" in parent_ics
    after = service.list_instances(start, end, ["local"])
    assert _starts(after)[:4] == ["2026-03-02T09:00:00Z",
                                 "2026-03-09T09:00:00Z",
                                 "2026-03-16T09:00:00Z",
                                 "2026-03-23T09:00:00Z"]
    assert after[0]["title"].get_string() == "Standup"
    assert after[2]["title"].get_string() == "Tail"
    assert after[0]["uid"].get_string() != after[2]["uid"].get_string()
    _revision, changed, _deleted = service.list_changes(0)
    assert "weekly-1" in changed and len(changed) == 2


def test_scoped_edit_all_replaces_series(service):
    service.create("local", WEEKLY_ICS)
    renamed = WEEKLY_ICS.replace("SUMMARY:Standup", "SUMMARY:Everything")
    service.update("weekly-1", renamed, "all", 0)
    assert "SUMMARY:Everything" in service.get("weekly-1")
    start, end = _window()
    assert all(i["title"].get_string() == "Everything"
               for i in service.list_instances(start, end, ["local"]))


def test_scoped_delete_this_and_all(service):
    service.create("local", WEEKLY_ICS)
    start, end = _window()
    before = service.list_instances(start, end, ["local"])
    victim = before[1]["start"].get_int64()
    service.delete("weekly-1", "this", victim)
    after = service.list_instances(start, end, ["local"])
    assert victim not in [i["start"].get_int64() for i in after]
    assert len(after) == len(before) - 1
    service.delete("weekly-1", "all", 0)
    assert service.list_instances(start, end, ["local"]) == []
    _revision, _changed, deleted = service.list_changes(0)
    assert "weekly-1" in deleted


def test_scoped_delete_following_truncates(service):
    service.create("local", WEEKLY_ICS)
    start, end = _window()
    before = service.list_instances(start, end, ["local"])
    cut = before[3]["start"].get_int64()
    service.delete("weekly-1", "this-and-following", cut)
    after = service.list_instances(start, end, ["local"])
    assert _starts(after) == ["2026-03-02T09:00:00Z",
                             "2026-03-09T09:00:00Z",
                             "2026-03-16T09:00:00Z"]


def test_list_since_semantics(service):
    service.create("local", ONE_SHOT_ICS)
    revision, changed, deleted = service.list_changes(0)
    assert changed == ["one-1"] and deleted == []
    service.delete("one-1", "all", 0)
    revision2, changed2, deleted2 = service.list_changes(revision)
    assert changed2 == [] and deleted2 == ["one-1"]
    assert revision2 > revision


def test_changed_signal_on_writes(service):
    conn = service._connection
    uid = service.create("local", ONE_SHOT_ICS)
    assert len(conn.signals) == 1
    _dest, path, iface, signal, params = conn.signals[-1]
    assert (path, iface, signal) == (OBJECT_PATH, INTERFACE, "Changed")
    assert params.unpack() == (2, [uid])
    service.update(uid, ONE_SHOT_ICS, "all", 0)
    assert conn.signals[-1][4].unpack() == (3, [uid])
    service.delete(uid, "all", 0)
    assert conn.signals[-1][4].unpack() == (4, [uid])


def test_emit_changed_without_connection_is_fail_open():
    assert emit_changed(None, 1, ["u"]) is False


def test_resolve_idle_timeout_prefers_cli_then_env():
    assert resolve_idle_timeout(5) == 5.0
    assert resolve_idle_timeout() == IDLE_EXIT_SECONDS


def test_handle_method_call_dispatches_without_blocking():
    store = CalendarStore(":memory:")
    handle = CalendarService(store)
    try:
        invocation = FakeInvocation()
        handle.handle_method_call(None, ":1.1", OBJECT_PATH, INTERFACE,
                                  "ListCalendars", None, invocation)
        assert invocation.reply is None
        deadline = time.monotonic() + 5
        while invocation.reply is None and invocation.error is None:
            assert time.monotonic() < deadline
            GLib.main_context_default().iteration(False)
            time.sleep(0.005)
        assert invocation.error is None
        (calendars,) = invocation.reply.unpack()
        assert any(c["id"] == BOOTSTRAP_CALENDAR_ID for c in calendars)
    finally:
        handle.stop()
        store.close()


def test_handle_method_call_unknown_method_reports_error():
    store = CalendarStore(":memory:")
    handle = CalendarService(store)
    try:
        invocation = FakeInvocation()
        handle.handle_method_call(None, ":1.1", OBJECT_PATH, INTERFACE,
                                  "Nope", None, invocation)
        deadline = time.monotonic() + 5
        while invocation.reply is None and invocation.error is None:
            assert time.monotonic() < deadline
            GLib.main_context_default().iteration(False)
            time.sleep(0.005)
        assert invocation.error is not None
        assert invocation.error[0] == ERROR_INVALID_ARGUMENT
    finally:
        handle.stop()
        store.close()
