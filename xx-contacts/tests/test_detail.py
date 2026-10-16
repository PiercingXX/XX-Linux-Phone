"""L3 detail-model tests: sections, display numbers, URIs, history seam."""

from xx_contacts.detail import (
    CALL_HISTORY_AVAILABLE,
    build_detail_sections,
    detail_title,
    format_address,
    get_call_history,
    maps_uri,
)
from xx_contacts.phones import format_display_number
from xx_contacts.store import (
    AddressEntry,
    ContactData,
    ContactStore,
    DateEntry,
    EmailEntry,
    ImEntry,
    PhoneEntry,
    RelationEntry,
    UrlEntry,
)


def _full_data() -> ContactData:
    return ContactData(
        display_name="Grace Hopper",
        phones=[PhoneEntry(number_raw="+15550100200",
                           number_e164="+15550100200",
                           number_digits="15550100200",
                           type="CELL", is_primary=True),
                PhoneEntry(number_raw="+447700900123",
                           number_e164="+447700900123",
                           number_digits="447700900123", type="WORK")],
        emails=[EmailEntry(address="grace@example.org", type="HOME")],
        addresses=[AddressEntry(street="1 Main St", locality="Springfield",
                                region="IL", postal_code="62701",
                                country="USA")],
        urls=[UrlEntry(value="https://example.org", type="HOME")],
        ims=[ImEntry(value="xmpp:grace@example.org", protocol="xmpp")],
        relations=[RelationEntry(value="Ada Lovelace", type="friend")],
        dates=[DateEntry(value="1906-12-09", kind="birthday-note")],
        birthday="1906-12-09",
        anniversary="1945-08-14",
        note="Computer pioneer.\nSecond line.",
        groups=["Friends"],
        starred=True,
    )


def _record(data: ContactData):
    store = ContactStore(":memory:")
    return store.get(store.create(data))


def test_detail_sections_cover_every_field():
    record = _record(_full_data())
    sections = build_detail_sections(record)
    assert [s.key for s in sections] == [
        "phones", "emails", "addresses", "dates", "web", "notes",
        "groups"]
    assert detail_title(record) == "Grace Hopper"


def test_numbers_show_spaced_but_dial_raw():
    record = _record(_full_data())
    phones = next(s for s in build_detail_sections(record)
                  if s.key == "phones")
    first, second = phones.fields
    assert " " in first.display
    assert first.display == "+1 555 010 0200"
    assert second.display.startswith("+44 ")
    kinds = {a.kind: a.uri for a in first.actions}
    assert kinds["call"] == "tel:+15550100200"
    assert kinds["message"] == "sms:+15550100200"
    assert " " not in kinds["call"]


def test_empty_fields_are_omitted():
    record = _record(ContactData(display_name="Bare"))
    assert build_detail_sections(record) == []
    assert detail_title(record) == "Bare"


def test_title_falls_back_without_a_name():
    assert detail_title(ContactData(
        phones=[PhoneEntry(number_raw="+15550100199")])
    ) == "+15550100199"
    assert detail_title(ContactData(
        emails=[EmailEntry(address="a@example.org")])
    ) == "a@example.org"
    assert detail_title(ContactData()) == "Unnamed"


def test_dates_urls_notes_and_groups_sections():
    record = _record(_full_data())
    by_key = {s.key: s for s in build_detail_sections(record)}
    assert [f.label for f in by_key["dates"].fields] == [
        "Birthday", "Anniversary", "birthday-note"]
    assert [f.label for f in by_key["web"].fields] == [
        "HOME", "xmpp", "friend"]
    assert by_key["notes"].fields[0].display.startswith(
        "Computer pioneer.")
    assert [f.display for f in by_key["groups"].fields] == ["Friends"]


def test_blank_entries_are_skipped():
    data = ContactData(
        display_name="Gappy",
        phones=[PhoneEntry(number_raw="   ")],
        emails=[EmailEntry(address="  ")],
        addresses=[AddressEntry()],
        note="  ",
        groups=["  "],
    )
    assert build_detail_sections(data) == []


def test_call_history_stays_hidden():
    assert CALL_HISTORY_AVAILABLE is False
    assert get_call_history("no-such-uid") == []


def test_format_display_number_grouping():
    assert format_display_number("+15550100200") == "+1 555 010 0200"
    assert format_display_number("+1 (555) 010-0200") == "+1 555 010 0200"
    assert format_display_number("+447700900123") == "+44 770 090 0123"
    assert format_display_number("+358401234567") == "+358 401 234 567"
    assert format_display_number("911") == "911"
    assert format_display_number("") == ""
    assert format_display_number(None) == ""


def test_format_address_and_maps_uri():
    entry = AddressEntry(street="1 Main St", locality="Springfield",
                         region="IL", postal_code="62701", country="USA")
    display = format_address(entry)
    assert display == "1 Main St\nSpringfield IL 62701\nUSA"
    uri = maps_uri(display)
    assert uri.startswith("geo:0,0?q=")
    assert " " not in uri
    assert maps_uri("  ") == ""
    assert format_address(AddressEntry()) == ""
