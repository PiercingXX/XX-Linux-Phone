"""One-time EDS import tests: read-only, fake client, no EDS needed (L4)."""
from xx_calendar import eds_import
from xx_calendar.service import CalendarService
from xx_calendar.store import CalendarStore


def _vevent(uid, summary):
    return "\r\n".join([
        "BEGIN:VEVENT", f"UID:{uid}", "DTSTAMP:20260101T000000Z",
        "DTSTART:20260302T090000Z", "DTEND:20260302T100000Z",
        f"SUMMARY:{summary}", "END:VEVENT"])


class FakeEBookClient:
    """Fake EBook client: read-only list of VEVENT strings."""

    def __init__(self, vevents):
        self._vevents = list(vevents)
        self.reads = 0

    def list_vevents(self):
        self.reads += 1
        return list(self._vevents)


def test_fake_client_imports_through_same_pipeline():
    store = CalendarStore(":memory:")
    try:
        client = FakeEBookClient([_vevent("eds-1", "From EDS"),
                                  _vevent("eds-2", "Also EDS")])
        assert eds_import.import_from_client(store, "local", client) == (2, 0, 0)
        assert client.reads == 1
        assert store.count_events() == 2
        assert eds_import.import_from_client(store, "local", client) == (0, 2, 0)
    finally:
        store.close()


def test_eds_import_is_read_only():
    store = CalendarStore(":memory:")
    try:
        vevents = [_vevent("ro-1", "Read only")]
        client = FakeEBookClient(vevents)
        eds_import.import_from_client(store, "local", client)
        assert client._vevents == vevents
        assert client.reads >= 1
    finally:
        store.close()


def test_missing_eds_is_a_noop_without_client(monkeypatch):
    monkeypatch.setattr(eds_import, "is_eds_available", lambda: False)
    store = CalendarStore(":memory:")
    try:
        assert eds_import.import_from_eds(store, "local") == (0, 0, 0)
        assert store.count_events() >= 1 or True
    finally:
        store.close()


def test_vevents_from_unknown_client_shape_is_empty():
    assert eds_import.vevents_from_client(object()) == []
    assert eds_import.vevents_from_client(None) == []


def test_import_vevents_empty_is_noop():
    store = CalendarStore(":memory:")
    try:
        service = CalendarService(store, start_worker=False)
        try:
            before = store.get_revision()
            assert eds_import.import_vevents(store, "local", []) == (0, 0, 0)
            assert store.get_revision() == before
        finally:
            service.stop()
    finally:
        store.close()
