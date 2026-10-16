"""vCard codec tests: 4.0/3.0 parse + serialize + store mapping (L1)."""

import pytest

from xx_contacts import vcard
from xx_contacts.store import ContactStore

ANDROID_EXPORT_CARD = (
    "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:0r1-2A3b4C5d\r\nFN:Ada Lovelace\r\n"
    "N:Lovelace;Ada;;;\r\nTEL;TYPE=CELL:+1 555 0100\r\n"
    "TEL;TYPE=HOME,VOICE:+1 555 0200\r\nEMAIL;TYPE=WORK:ada@example.com\r\n"
    "ORG:Analytical Engines\r\nTITLE:Mathematician\r\n"
    "ADR:;;1 Charles St;London;;W1;UK\r\nBDAY:1815-12-10\r\nNICKNAME:Ada\r\n"
    "NOTE:First program\\nmer\\,with comma\r\nCATEGORIES:Friends,Family\r\n"
    "X-STARRED:1\r\nX-CUSTOM-THING;FOO=bar:baz value\r\n"
    "PHOTO:data:image/jpeg;base64,/9j/AAA=\r\nEND:VCARD\r\n"
)

V30_EDGE_CARD = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\n"
    "FN;ENCODING=QUOTED-PRINTABLE:Ren=C3=A9e Desc=\r\nartes\r\n"
    "N;ENCODING=QUOTED-PRINTABLE:Descartes;Ren=C3=A9e;;;\r\n"
    "TEL;CELL:+33 6 12 34 56 78\r\n"
    "TEL;TYPE=HOME,VOICE:+33 1 23 45 67 89\r\n"
    "item1.TEL:+33 6 98 76 54 32\r\nitem1.X-ABLABEL:Perso\r\n"
    "EMAIL;TYPE=PREF,INTERNET:rene@example.fr\r\n"
    "ADR;TYPE=HOME:;;1 Rue de la Paix;Paris;;75000;France\r\n"
    "BDAY:1596-03-31\r\nNICKNAME;ENCODING=QUOTED-PRINTABLE:Ren=C3=A9e,Dudu\r\n"
    "NOTE:multi\r\n line one\r\n\tline two\r\n"
    "PHOTO;ENCODING=b;TYPE=JPEG:/9j/AAA=\r\nEND:VCARD\r\n"
)


def _mapped(data):
    return {
        "name": data.display_name,
        "given": data.given,
        "family": data.family,
        "phones": [(p.number_raw, p.type, p.label, p.is_primary)
                   for p in data.phones],
        "emails": [(e.address, e.type) for e in data.emails],
        "org": data.org,
        "title": data.title,
        "bday": data.birthday,
        "nick": data.nickname,
        "note": data.note,
        "groups": list(data.groups),
        "starred": data.starred,
    }


def _round_trip(data):
    store = ContactStore(":memory:")
    try:
        uid = store.create(data)
        record = store.get(uid)
        text = vcard.contact_to_vcard(record)
        data2, hint2 = vcard.contact_from_vcard(text)
        return record, text, data2, hint2
    finally:
        store.close()


def test_unfold_drops_one_folding_char():
    lines = vcard.unfold_lines("FN:Ada\r\n Lovelace\r\n\tX\r\nTEL:1\r\n")
    assert lines == ["FN:AdaLovelaceX", "TEL:1"]


def test_unfold_qp_soft_break_keeps_bytes():
    text = ("NOTE;ENCODING=QUOTED-PRINTABLE:Ren=C3=A9e=\r\nDescartes\r\n"
            "TEL:1\r\n")
    lines = vcard.unfold_lines(text)
    assert lines[0] == "NOTE;ENCODING=QUOTED-PRINTABLE:Ren=C3=A9eDescartes"


def test_fold_line_octet_limit_and_round_trip():
    line = "NOTE:" + "é" * 60
    folded = vcard.fold_line(line)
    physical = folded.split("\r\n")
    assert all(len(p.encode("utf-8")) <= 75 for p in physical)
    assert vcard.unfold_lines(folded) == [line]


def test_escape_round_trip():
    value = "a;b,c\\d\ne"
    assert vcard.unescape_value(vcard.escape_value(value)) == value
    assert vcard.unescape_value("x\\Ny") == "x\ny"


def test_split_unescaped_keeps_escapes():
    assert vcard.split_unescaped("a\\;b;c", ";") == ["a\\;b", "c"]
    assert vcard.split_unescaped("a\\,b,c", ",") == ["a\\,b", "c"]


def test_params_type_pref_value_encoding_group():
    prop = vcard.parse_property(
        'item1.TEL;TYPE="CELL,VOICE";PREF=1:+1 555')
    assert prop is not None
    assert prop.group == "item1"
    assert prop.types() == ["CELL,VOICE"]
    assert prop.is_primary()
    multi = vcard.parse_property("TEL;TYPE=CELL,VOICE:+1 555")
    assert multi is not None and multi.types() == ["CELL", "VOICE"]
    bare = vcard.parse_property("TEL;CELL:+1 555")
    assert bare is not None and bare.types() == ["CELL"]
    pref_type = vcard.parse_property("EMAIL;TYPE=PREF,INTERNET:x@y")
    assert pref_type is not None and pref_type.is_primary()
    assert vcard.parse_property("X;NOTE=\"a:b\":v").value == "v"


def test_decode_base64_photo_30():
    prop = vcard.parse_property("PHOTO;ENCODING=b;TYPE=JPEG:/9j/AAA=")
    assert prop is not None
    assert vcard.decode_binary(prop) == bytes.fromhex("ffd8ff0000")


def test_decode_qp_value():
    prop = vcard.parse_property("FN;ENCODING=QUOTED-PRINTABLE:Ren=C3=A9e")
    assert prop is not None
    decoded = vcard.decode_binary(prop)
    assert decoded is not None and decoded.decode("utf-8") == "Renée"


def test_android_export_fixture_maps():
    data, hint = vcard.contact_from_vcard(ANDROID_EXPORT_CARD)
    assert hint is None
    assert data.display_name == "Ada Lovelace"
    assert (data.given, data.family) == ("Ada", "Lovelace")
    assert [p.number_raw for p in data.phones] == ["+1 555 0100",
                                                   "+1 555 0200"]
    assert data.phones[0].type == "CELL"
    assert data.phones[1].type == "HOME,VOICE"
    assert data.phones[0].number_e164 == "+15550100"
    assert [e.address for e in data.emails] == ["ada@example.com"]
    assert data.org == "Analytical Engines"
    assert data.title == "Mathematician"
    assert data.addresses[0].street == "1 Charles St"
    assert data.addresses[0].locality == "London"
    assert data.birthday == "1815-12-10"
    assert data.nickname == "Ada"
    assert data.note == "First program\nmer,with comma"
    assert data.groups == ["Friends", "Family"]
    assert data.starred
    assert any(e.prop == "X-CUSTOM-THING" and e.params == "FOO=bar"
               and e.value == "baz value" for e in data.extra)
    assert any(e.prop == "PHOTO" for e in data.extra)


def test_android_export_fixture_lossless_via_store():
    data, _ = vcard.contact_from_vcard(ANDROID_EXPORT_CARD)
    record, text, data2, hint2 = _round_trip(data)
    assert hint2 == record.uid
    assert _mapped(data2) == _mapped(data)
    extra1 = sorted((e.prop, e.params, e.value) for e in data.extra)
    extra2 = sorted((e.prop, e.params, e.value) for e in data2.extra)
    assert extra2 == extra1


def test_v30_edge_cases_map():
    data, _ = vcard.contact_from_vcard(V30_EDGE_CARD, default_region="FR")
    assert data.display_name == "Renée Descartes"
    assert (data.given, data.family) == ("Renée", "Descartes")
    assert len(data.phones) == 3
    assert data.phones[0].type == "CELL"
    assert data.phones[2].label == "Perso"
    assert data.emails[0].is_primary
    assert data.addresses[0].country == "France"
    assert data.nickname == "Renée,Dudu"
    assert "line one" in data.note and "line two" in data.note
    assert any(e.prop == "PHOTO" for e in data.extra)


def test_v30_edge_lossless_via_store():
    data, _ = vcard.contact_from_vcard(V30_EDGE_CARD, default_region="FR")
    record, _text, data2, hint2 = _round_trip(data)
    assert hint2 == record.uid
    assert _mapped(data2) == _mapped(data)
    extra1 = sorted((e.prop, e.params, e.value) for e in data.extra)
    extra2 = sorted((e.prop, e.params, e.value) for e in data2.extra)
    assert extra2 == extra1


def test_structured_n_fallback_name():
    data, _ = vcard.contact_from_vcard(
        "BEGIN:VCARD\r\nVERSION:3.0\r\nN:Lovelace;Ada;;;\r\nEND:VCARD\r\n")
    assert data.display_name == "Ada Lovelace"


def test_uuid_uid_hint_survives():
    text = ("BEGIN:VCARD\r\nVERSION:4.0\r\n"
            "UID:64eebae7-ddee-4b13-8ff5-515fded82237\r\nFN:Ada\r\n"
            "END:VCARD\r\n")
    data, hint = vcard.contact_from_vcard(text)
    assert hint == "64eebae7-ddee-4b13-8ff5-515fded82237"
    store = ContactStore(":memory:")
    try:
        uid = store.create(data, hint)
        assert uid == hint
        assert f"UID:{hint}" in vcard.contact_to_vcard(store.get(uid))
    finally:
        store.close()


def test_serialized_card_shape():
    data, _ = vcard.contact_from_vcard(ANDROID_EXPORT_CARD)
    store = ContactStore(":memory:")
    try:
        record = store.get(store.create(data))
        text = vcard.contact_to_vcard(record)
    finally:
        store.close()
    assert text.startswith("BEGIN:VCARD\r\nVERSION:4.0\r\n")
    assert text.endswith("END:VCARD\r\n")
    assert "X-STARRED:1" in text
    assert "PHOTO:data:image/jpeg;base64,/9j/AAA=" in text


def test_unstarred_card_has_no_x_starred():
    data, _ = vcard.contact_from_vcard(
        "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada\r\nEND:VCARD\r\n")
    assert not data.starred
    store = ContactStore(":memory:")
    try:
        record = store.get(store.create(data))
        assert "X-STARRED" not in vcard.contact_to_vcard(record)
    finally:
        store.close()


def test_multi_card_and_empty():
    assert vcard.parse_cards("") == []
    cards = vcard.parse_cards(ANDROID_EXPORT_CARD + V30_EDGE_CARD)
    assert len(cards) == 2
    assert cards[0].version == "4.0"
    assert cards[1].version == "3.0"
    with pytest.raises(ValueError):
        vcard.contact_from_vcard("FN:lonely line\r\n")


def test_impp_related_url_anniversary_title_round_trip():
    text = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada\r\n"
            "URL;TYPE=HOME:https://example.org/a\r\n"
            "IMPP:xmpp:ada@example.org\r\nRELATED;TYPE=SPOUSE:Grace\r\n"
            "ANNIVERSARY:20200101\r\nTITLE:Dr\r\nKIND:individual\r\n"
            "X-EVOLUTION-FILE-AS:whatever\r\nEND:VCARD\r\n")
    data, _ = vcard.contact_from_vcard(text)
    assert data.urls[0].value == "https://example.org/a"
    assert data.ims[0].protocol == "xmpp"
    assert data.relations[0].type == "SPOUSE"
    assert data.anniversary == "20200101"
    assert data.title == "Dr"
    _record, _text, data2, _hint = _round_trip(data)
    assert data2.urls == data.urls
    assert data2.ims == data.ims
    assert data2.relations == data.relations
    assert any(e.prop == "KIND" for e in data2.extra)
    assert any(e.prop == "X-EVOLUTION-FILE-AS" for e in data2.extra)
