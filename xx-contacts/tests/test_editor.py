"""L4 editor-model tests: validation, preview, dirty, lossless apply."""

import pytest

from xx_contacts import editor, vcard
from xx_contacts.store import ContactData, ContactStore, NotFoundError

ANDROID_CARD = (
    "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:0r1-2A3b4C5d\r\nFN:Ada Lovelace\r\n"
    "N:Lovelace;Ada;;;\r\nTEL;TYPE=CELL:+1 555 0100\r\n"
    "TEL;TYPE=HOME,VOICE:+1 555 0200\r\nEMAIL;TYPE=WORK:ada@example.com\r\n"
    "ORG:Analytical Engines\r\nTITLE:Mathematician\r\n"
    "ADR:;;1 Charles St;London;;W1;UK\r\nBDAY:1815-12-10\r\nNICKNAME:Ada\r\n"
    "NOTE:First program\\nmer\\,with comma\r\nCATEGORIES:Friends,Family\r\n"
    "X-STARRED:1\r\nX-CUSTOM-THING;FOO=bar:baz value\r\n"
    "PHOTO:data:image/jpeg;base64,/9j/AAA=\r\nEND:VCARD\r\n"
)

V30_CARD = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\n"
    "FN;ENCODING=QUOTED-PRINTABLE:Ren=C3=A9e Descartes\r\n"
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


def _named_form(**overrides):
    form = editor.EditorForm(given="Ada", family="Lovelace")
    for key, value in overrides.items():
        setattr(form, key, value)
    return form


def test_type_options_use_detail_human_labels():
    options = dict(editor.type_options())
    assert options["CELL"] == "Mobile"
    assert options["HOME"] == "Home"
    assert options["WORK"] == "Work"
    assert options["MAIN"] == "Main"
    assert options["OTHER"] == "Other"
    assert len(options) == len(set(options))


def test_validate_needs_a_name_or_a_number():
    assert editor.validate(editor.EditorForm()) is not None
    assert editor.validate(_named_form()) is None
    assert editor.validate(editor.EditorForm(
        phones=[editor.PhoneForm(number="+1 555 0100")])) is None
    assert editor.validate(editor.EditorForm(
        emails=[editor.EmailForm(address="a@example.org")])) is None
    assert editor.validate(editor.EditorForm(org="Example Inc")) is None
    assert editor.validate(editor.EditorForm(nickname="Dudu")) is None


def test_validate_rejects_bad_email_and_dates():
    form = _named_form(emails=[editor.EmailForm(address="not-an-email")])
    assert "Email 1" in (editor.validate(form) or "")
    form = _named_form(emails=[editor.EmailForm(address="a@b")])
    assert editor.validate(form) is not None
    assert editor.validate(_named_form(
        emails=[editor.EmailForm(address="a@example.org")])) is None
    assert editor.validate(_named_form(birthday="2020-02-30")) is not None
    assert editor.validate(_named_form(birthday="1815-12-10")) is None
    assert editor.validate(_named_form(anniversary="20200101")) is not None


def test_display_preview_falls_back():
    assert editor.display_preview(_named_form()) == "Ada Lovelace"
    assert editor.display_preview(
        editor.EditorForm(org="Example")) == "Example"
    assert editor.display_preview(editor.EditorForm(
        phones=[editor.PhoneForm(number="5550100")])) == "5550100"
    assert editor.display_preview(editor.EditorForm()) == ""


def test_phone_preview_uses_normalization():
    assert editor.phone_preview("") == ""
    shown = editor.phone_preview("+1 555 010 0200")
    assert "+1 555-010-0200" in shown
    assert "+15550100200" in shown


def test_dirty_tracks_snapshot():
    saved = _named_form()
    assert not editor.is_dirty(saved, editor.clone_form(saved))
    changed = editor.clone_form(saved)
    changed.family = "King"
    assert editor.is_dirty(saved, changed)
    assert editor.clone_form(saved) == saved


def test_apply_keeps_extra_and_photo_path_verbatim():
    data, _ = vcard.contact_from_vcard(ANDROID_CARD)
    store = ContactStore(":memory:")
    try:
        record = store.get(store.create(data))
        form = editor.form_from_record(record)
        assert form.given == "Ada" and form.family == "Lovelace"
        assert form.phones[0].type == "CELL"
        applied = editor.apply_form_to_data(record, form)
        assert [(e.prop, e.params, e.value, e.group)
                for e in applied.extra] == [
            (e.prop, e.params, e.value, e.group) for e in record.extra]
        assert applied.photo_path == record.photo_path
        assert applied.display_name == "Ada Lovelace"
        assert applied.phones[0].number_digits == "15550100"
        assert applied.phones[0].number_e164 == "+15550100"
    finally:
        store.close()


def test_edit_round_trip_is_lossless_for_fixture_vcards():
    for card in (ANDROID_CARD, V30_CARD):
        data, _ = vcard.contact_from_vcard(card)
        store = ContactStore(":memory:")
        try:
            record = store.get(store.create(data))
            form = editor.form_from_record(record)
            applied = editor.apply_form_to_data(record, form)
            before = sorted((e.prop, e.params, e.value, e.group)
                            for e in record.extra)
            after = sorted((e.prop, e.params, e.value, e.group)
                           for e in applied.extra)
            assert after == before
            text = vcard.contact_to_vcard(_record(store, applied))
            unfolded = text.replace("\r\n ", "")
            for line in record.extra:
                assert line.logical_line() in unfolded
        finally:
            store.close()


def _record(store, data):
    return store.get(store.create(data))


def test_save_new_and_edit_bump_revision_exactly_once():
    store = ContactStore(":memory:")
    try:
        before = store.get_revision()
        uid, revision = editor.save_new(store, _named_form())
        assert revision == before + 1
        assert store.get_revision() == before + 1
        form = editor.form_from_record(store.get(uid))
        form.family = "King"
        revision2 = editor.save_edit(store, uid, form)
        assert revision2 == before + 2
        assert store.get(uid).family == "King"
        assert store.get(uid).given == "Ada"
    finally:
        store.close()


def test_save_rejects_invalid_and_unknown_uid():
    store = ContactStore(":memory:")
    try:
        with pytest.raises(ValueError):
            editor.save_new(store, editor.EditorForm())
        with pytest.raises(ValueError):
            editor.save_edit(store, "whatever", editor.EditorForm())
        with pytest.raises(NotFoundError):
            editor.save_edit(store,
                             "00000000-0000-4000-8000-000000000000",
                             _named_form())
    finally:
        store.close()


def test_blank_rows_drop_and_single_primary_survives():
    form = _named_form(
        phones=[editor.PhoneForm(number=""),
                editor.PhoneForm(number="+1 555 0100", is_primary=True),
                editor.PhoneForm(number="+1 555 0200", is_primary=True)])
    applied = editor.apply_form_to_data(ContactData(), form)
    assert [p.number_raw for p in applied.phones] == ["+1 555 0100",
                                                      "+1 555 0200"]
    assert [p.is_primary for p in applied.phones] == [True, False]


def test_email_shape_checker():
    assert editor.is_valid_email("ada@example.org")
    assert not editor.is_valid_email("ada@")
    assert not editor.is_valid_email("ada example@org.com")
    assert editor.is_valid_date("2024-02-29")
    assert not editor.is_valid_date("2023-02-29")
