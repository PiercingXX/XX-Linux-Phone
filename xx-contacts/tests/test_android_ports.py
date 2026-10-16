"""Ports of the Android app's names/match/vCard/groups unit tests.

Sources: ``ref/xx-contacts/app/src/test/...`` — only the pure-logic tests
are portable; provider/UI/wiring tests are listed as not ported in the
lane report. One intentional deviation: the Android ``VCard.parse`` kept
the folding space (``FN:Ada`` + `` Lovelace`` → ``Ada Lovelace``) while
its own backup codec dropped it; the Linux codec follows RFC 2426 §2.1
(drop one character), matching the backup codec and real-world folders.
"""

from xx_contacts import names
from xx_contacts.app_config import AppConfig
from xx_contacts.groups import (
    chooser_list,
    is_creatable,
    reserved_kind,
    stored_name,
)
from xx_contacts.store import ContactStore
from xx_contacts.vcard import VCardContact
from xx_contacts.vcard import parse as legacy_parse
from xx_contacts.vcard import serialize as legacy_serialize
from xx_contacts.vcard import (
    contact_from_vcard,
    contact_to_vcard,
)


def test_splits_at_the_last_space():
    assert names.split("Ada Lovelace") == names.Split("Ada", "Lovelace")
    assert names.split("  Difference Engine Repair Co ") == names.Split(
        "Difference Engine Repair", "Co")
    assert names.split("Ampere") == names.Split("Ampere", None)
    assert names.split("   ") == names.Split(None, None)
    assert names.split(None) == names.Split(None, None)


def test_composes_whichever_parts_are_present():
    assert names.compose(" Ada ", "Lovelace") == "Ada Lovelace"
    assert names.compose(None, "Lovelace") == "Lovelace"
    assert names.compose("Ada", "") == "Ada"
    assert names.compose(None, None) == ""


def test_typed_name_becomes_the_display_name():
    assert names.display_name("Grace", "Hopper") == "Grace Hopper"


def test_untouched_name_keeps_the_loaded_display_name():
    loaded = names.Loaded.seed("Ada", "Lovelace", "Ada King Lovelace")
    assert names.display_name("Ada", "Lovelace", loaded) == \
        "Ada King Lovelace"
    assert names.display_name("Ada", "Byron", loaded) == "Ada Byron"


def test_unsplit_display_name_seeds_first_and_survives_a_save():
    loaded = names.Loaded.seed(None, None, "Difference Engine Repair Co")
    assert loaded.given == "Difference Engine Repair Co"
    assert loaded.family is None
    assert names.display_name(loaded.given, loaded.family, loaded) == \
        "Difference Engine Repair Co"


def test_no_name_falls_back_to_company_then_phone_then_email():
    assert names.display_name(None, None,
                               fallbacks=["Ampere", "555", "a@b"]) == "Ampere"
    assert names.display_name("", " ",
                               fallbacks=[None, "555-0100", "a@b"]) == \
        "555-0100"
    assert names.display_name(None, None,
                               fallbacks=["", None, "a@b"]) == "a@b"
    assert names.display_name(None, None,
                               fallbacks=[None, "", " "]) is None


def test_initials_take_first_and_last_word():
    assert names.initials("Ada Lovelace") == "AL"
    assert names.initials("Ampere") == "A"
    assert names.initials("Beatrice Margaret Anne Dubois") == "BD"
    assert names.initials("  ") == ""
    assert names.initials(None) == ""


def test_vcard_fold_joins_without_adding_a_space():
    card = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada\r\n Lovelace\r\n"
            "TEL:+1 555 0100\r\nEND:VCARD\r\n")
    assert legacy_parse(card).display_name == "AdaLovelace"


def test_vcard_tel_type_parameters():
    card = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\n"
            "TEL;TYPE=CELL:+1 555 0100\r\n"
            "TEL;TYPE=HOME,VOICE:+1 555 0200\r\nEND:VCARD\r\n")
    assert legacy_parse(card).phones == ["+1 555 0100", "+1 555 0200"]


def test_vcard_tolerates_version_40_marker():
    card = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada Lovelace\r\n"
            "TEL;TYPE=cell:+1 555 0100\r\nEND:VCARD\r\n")
    contact = legacy_parse(card)
    assert contact.display_name == "Ada Lovelace"
    assert contact.phones == ["+1 555 0100"]


def test_vcard_version_30_round_trip_preserves_name_and_phones():
    original = VCardContact("Ada Lovelace", ["+1 555 0100", "+1 555 0200"])
    reparsed = legacy_parse(legacy_serialize(original))
    assert reparsed.display_name == original.display_name
    assert reparsed.phones == original.phones


def test_vcard_serialize_emits_tel_with_type_parameter():
    card = legacy_serialize(VCardContact("Ada Lovelace", ["+1 555 0100"]))
    assert "TEL;TYPE=CELL:+1 555 0100" in card
    assert "VERSION:3.0" in card
    assert "BEGIN:VCARD" in card and "END:VCARD" in card


def test_vcard_round_trip_preserves_emails_org_and_address():
    original = VCardContact("Ada Lovelace", ["+1 555 0100"],
                            ["ada@example.com"], "Analytical Engines", "",
                            "1 Charles St, London")
    reparsed = legacy_parse(legacy_serialize(original))
    assert reparsed.emails == original.emails
    assert reparsed.company == original.company
    assert reparsed.address == original.address


def test_vcard_parse_reads_name_and_phones():
    card = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\n"
            "TEL:+1 555 0100\r\nTEL:+1 555 0200\r\nEND:VCARD\r\n")
    contact = legacy_parse(card)
    assert contact.display_name == "Ada Lovelace"
    assert contact.phones == ["+1 555 0100", "+1 555 0200"]


def test_vcard_parse_handles_structured_n():
    card = ("BEGIN:VCARD\r\nVERSION:3.0\r\nN:Lovelace;Ada;;;\r\n"
            "TEL:+1 555 0100\r\nEND:VCARD\r\n")
    assert legacy_parse(card).display_name == "Ada Lovelace"


def test_vcard_import_maps_onto_store_fields():
    card = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\n"
            "N:Lovelace;Ada;;;\r\nTEL;TYPE=CELL:+1 555 0100\r\n"
            "EMAIL:ada@example.com\r\nORG:Analytical Engines\r\n"
            "ADR:1 Charles St, London\r\nEND:VCARD\r\n")
    data, _ = contact_from_vcard(card)
    assert data.display_name == "Ada Lovelace"
    assert (data.given, data.family) == ("Ada", "Lovelace")
    assert [p.number_raw for p in data.phones] == ["+1 555 0100"]
    assert [e.address for e in data.emails] == ["ada@example.com"]
    assert data.org == "Analytical Engines"
    assert data.addresses[0].po_box == "1 Charles St, London"
    store = ContactStore(":memory:")
    try:
        record = store.get(store.create(data))
        assert "FN:Ada Lovelace" in contact_to_vcard(record)
    finally:
        store.close()


def test_vcard_import_remaining_kinds_map_when_present():
    card = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\n"
            "TEL:+1 555 0100\r\nEMAIL:ada@example.com\r\n"
            "ORG:Analytical Engines\r\nADR:1 Charles St, London\r\n"
            "BDAY:1815-12-10\r\nNICKNAME:Ada\r\nNOTE:First programmer\r\n"
            "END:VCARD\r\n")
    data, _ = contact_from_vcard(card)
    assert data.birthday == "1815-12-10"
    assert data.nickname == "Ada"
    assert data.note == "First programmer"


def test_group_chooser_order_and_reserved_names():
    rows = chooser_list(["Family", "Blocked", "Gym"])
    assert [(c.kind, c.name) for c in rows] == [
        ("star", "Star"), ("business", "Business"), ("blocked", "Blocked"),
        ("custom", "Family"), ("custom", "Gym"), ("create", "Create new"),
    ]
    assert reserved_kind("Star") == "star"
    assert reserved_kind("  star ") == "star"
    assert reserved_kind("Business") == "business"
    assert reserved_kind("Blocked") == "blocked"
    assert reserved_kind("block") == "blocked"
    assert stored_name("blocked") == "Blocked"
    assert reserved_kind("Family") is None
    assert is_creatable("Family")
    assert not is_creatable(" ")
    assert not is_creatable("star")
    assert not is_creatable("Blocked")


def test_search_matches_name_phone_and_email():
    store = ContactStore(":memory:")
    try:
        from xx_contacts.store import ContactData, EmailEntry, PhoneEntry

        store.create(ContactData(
            display_name="Alice",
            phones=[PhoneEntry(number_raw="+1 555 0100",
                                number_digits="15550100")],
            emails=[EmailEntry(address="alice@example.com")]))
        assert [n for _, n, _ in store.search("alice")] == ["Alice"]
        assert [n for _, n, _ in store.search("5550100")] == ["Alice"]
        assert [n for _, n, _ in store.search("alice@example")] == ["Alice"]
    finally:
        store.close()


def test_has_phone_and_photos_toggles_default_off_and_remembered(tmp_path):
    path = str(tmp_path / "config.json")
    assert AppConfig.load(path) == AppConfig()
    assert AppConfig().default_region == "US"
    cfg = AppConfig(has_phone_only=True, show_photos=True,
                    default_region="gb")
    cfg.save(path)
    assert AppConfig.load(path) == AppConfig(has_phone_only=True,
                                             show_photos=True,
                                             default_region="GB")
