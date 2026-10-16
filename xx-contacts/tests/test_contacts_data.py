from xx_contacts.contacts import (
    Contact,
    MemoryBook,
    lookup_number,
    numbers_match,
    search,
)
from xx_contacts.dnd_bridge import merge_starred_numbers, starred_numbers
from xx_contacts.vcard import VCardContact, parse, serialize


def _book():
    return [
        Contact(uid="1", display_name="Ada Lovelace",
                phones=["+1 555 0101"], starred=True),
        Contact(uid="2", display_name="Grace Hopper",
                phones=["+1 555 0202"]),
    ]


def test_tail_match_lookup():
    assert lookup_number(_book(), "5550101") == "Ada Lovelace"
    assert lookup_number(_book(), "+15550202") == "Grace Hopper"
    assert lookup_number(_book(), "5559999") is None
    assert not numbers_match("", "5550101")


def test_search_ranking():
    hits = search(_book(), "ada")
    assert [c.display_name for c in hits] == ["Ada Lovelace"]
    assert search(_book(), "") == []
    assert [c.display_name for c in search(_book(), "0202")] == ["Grace Hopper"]


def test_memory_book_upsert_and_delete():
    book = MemoryBook(_book())
    assert len(book.all) == 2
    book.upsert(Contact(uid="1", display_name="Ada King",
                        phones=["+1 555 0101"]))
    assert book.all[0].display_name == "Ada King"
    assert book.delete("1")
    assert not book.delete("missing")


def test_starred_bridge_merge():
    starred = starred_numbers(_book())
    assert starred == ["15550101"]
    merged = merge_starred_numbers(["+1 555 0202"], starred)
    assert merged == ["15550101", "15550202"]
    assert merge_starred_numbers(merged, starred) == merged


def test_vcard_round_trip():
    contact = VCardContact(display_name="Ada Lovelace",
                           phones=["+1 555 0101"],
                           emails=["ada@example.org"],
                           company="Analytical", birthday="1815-12-10",
                           nickname="Ada", note="First programmer")
    parsed = parse(serialize(contact))
    assert parsed.display_name == "Ada Lovelace"
    assert parsed.phones == ["+1 555 0101"]
    assert parsed.emails == ["ada@example.org"]
    assert parsed.company == "Analytical"
    assert parsed.birthday == "1815-12-10"
    assert parsed.note == "First programmer"


def test_vcard_tolerant_parse():
    text = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Grace Hopper\r\n"
            "TEL;TYPE=CELL:+1 555 0202\r\n"
            "NOTE:line one\r\n continued\r\nEND:VCARD\r\n")
    parsed = parse(text)
    assert parsed.display_name == "Grace Hopper"
    assert parsed.phones == ["+1 555 0202"]
    assert parsed.note == "line onecontinued"
