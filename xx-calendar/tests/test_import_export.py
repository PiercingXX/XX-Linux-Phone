"""Batched .ics import/export through the service (L4).

Headless: no display. Import runs on the service operation methods (the
same code the Gio worker dispatches), so no bus is needed here;
D-Bus-level Import/Export round-trips live in test_service_dbus.py style
tests via _dispatch tuples.
"""
import pytest
from gi.repository import GLib

from xx_calendar import ics as ics_module
from xx_calendar.service import (
    CalendarService,
    MAX_IMPORT_BYTES,
    export_store,
    iter_vevent_blocks,
)
from xx_calendar.store import CalendarStore


def _vevent(uid, summary, start="20260302T090000Z", end="20260302T100000Z",
            extra=""):
    lines = ["BEGIN:VEVENT", f"UID:{uid}", "DTSTAMP:20260101T000000Z",
             f"DTSTART:{start}", f"DTEND:{end}", f"SUMMARY:{summary}"]
    if extra:
        lines.append(extra)
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def _calendar(*blocks):
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0",
                        "PRODID:-//X//EN", *blocks, "END:VCALENDAR"]) + "\r\n"


@pytest.fixture
def service():
    store = CalendarStore(":memory:")
    handle = CalendarService(store, start_worker=False)
    handle._connection = None
    yield handle
    handle.stop()
    store.close()


class _Signals:
    def __init__(self):
        self.items = []

    def emit_signal(self, destination, path, interface, signal, parameters):
        self.items.append(parameters.unpack())
        return True


def test_import_counts_created_updated_skipped(service):
    service._connection = _Signals()
    text = _calendar(
        _vevent("a1", "One"),
        _vevent("a2", "Two"),
        _vevent("a1", "One dup"),
        _vevent("c1", "Gone", extra="STATUS:CANCELLED"),
        "BEGIN:VEVENT\r\nUID:bad\r\nDTSTAMP:20260101T000000Z\r\nSUMMARY:No start\r\nEND:VEVENT",
    )
    assert service.import_ics("local", text) == (2, 0, 3)
    assert len(service._connection.items) == 1
    assert service._store.count_events() == 2
    service._connection.items.clear()
    assert service.import_ics("local", text) == (0, 2, 3)
    assert len(service._connection.items) == 1


def test_import_is_one_transaction_one_signal(service):
    service._connection = _Signals()
    before = service._store.get_revision()
    blocks = [_vevent(f"u-{i}", f"Event {i}") for i in range(50)]
    created, updated, skipped = service.import_ics("local", _calendar(*blocks))
    assert (created, updated, skipped) == (50, 0, 0)
    assert len(service._connection.items) == 1
    revision, changed, _deleted = service.list_changes(before)
    assert revision == before + 1
    assert len(changed) == 50


def test_import_dedupes_by_uid_and_skips_canceled(service):
    text = _calendar(_vevent("k1", "Keep"),
                     _vevent("k1", "Keep dup"),
                     _vevent("k2", "Lost", extra="STATUS:CANCELED"))
    assert service.import_ics("local", text) == (1, 0, 2)
    assert service.get("k1").count("SUMMARY") == 1


def test_import_rejects_unknown_calendar_and_oversize(service):
    with pytest.raises(LookupError):
        service.import_ics("missing", _calendar(_vevent("x", "X")))
    with pytest.raises(ValueError, match="too large"):
        service.import_ics("local", "x" * (MAX_IMPORT_BYTES + 1))


def test_import_streams_two_thousand_events_in_one_go(service):
    service._connection = _Signals()
    blocks = [_vevent(f"bulk-{i}", f"Event {i}") for i in range(2000)]
    text = _calendar(*blocks)
    created, updated, skipped = service.import_ics("local", text)
    assert (created, updated, skipped) == (2000, 0, 0)
    assert len(service._connection.items) == 1
    assert service._store.count_events() == 2000


def test_blocks_stream_without_holding_all():
    text = _calendar(*[_vevent(f"s-{i}", "X") for i in range(10)])
    assert len(list(iter_vevent_blocks(text))) == 10
    assert list(iter_vevent_blocks("BEGIN:VCALENDAR\r\nEND:VCALENDAR")) == []


def test_export_per_calendar_and_all_are_lossless(service):
    service.import_ics("local", _calendar(_vevent("e1", "Alpha")))
    service._store.create_calendar(
        __import__("xx_calendar.store", fromlist=["CalendarRecord"])
        .CalendarRecord(id="work", display_name="Work"))
    service.import_ics("work", _calendar(_vevent("e2", "Beta")))
    one = service.export_ics(["local"])
    assert "UID:e1" in one and "UID:e2" not in one
    everything = service.export_ics(None)
    assert "UID:e1" in everything and "UID:e2" in everything
    reparsed = ics_module.parse_calendar(everything)
    assert {event.uid() for event in reparsed.events} == {"e1", "e2"}
    assert "PRODID:-//PiercingXX//XX-Calendar//EN" in everything
    assert export_store(service._store, []) == everything


def test_export_import_round_trip_dedupes(service):
    service.import_ics("local", _calendar(_vevent("r1", "Round")))
    exported = service.export_ics(["local"])
    assert service.import_ics("local", exported) == (0, 1, 0)


def test_dispatch_import_export_tuples(service):
    created_variant = service._dispatch("Import", ("local", _calendar(
        _vevent("d1", "Dispatch"))))
    assert created_variant.unpack() == (1, 0, 0)
    exported_variant = service._dispatch("Export", (["local"],))
    (text,) = exported_variant.unpack()
    assert "UID:d1" in text
    assert isinstance(created_variant, GLib.Variant)


def test_bounded_file_read_rejects_huge(tmp_path):
    from xx_calendar.service import read_bounded_text

    big = tmp_path / "big.ics"
    big.write_bytes(b"x" * 64)
    assert "x" in read_bounded_text(str(big), max_bytes=64)
    with pytest.raises(ValueError, match="too large"):
        read_bounded_text(str(big), max_bytes=8)


def test_no_socket_imports_in_package():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "xx_calendar"
    offenders = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for banned in ("import socket", "import urllib", "import http",
                       "from socket", "from urllib", "from http"):
            if banned in text:
                offenders.append((path.name, banned))
    assert offenders == []
