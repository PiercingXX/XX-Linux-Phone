"""L4 Me-card tests: designate, resolve, and share one contact."""

from xx_contacts import mecard, vcard
from xx_contacts.app_config import AppConfig
from xx_contacts.store import ContactData, ContactStore, PhoneEntry


def _contact(name="Ada Lovelace"):
    return ContactData(
        display_name=name,
        phones=[PhoneEntry(number_raw="+1 555 0100",
                           number_e164="+15550100",
                           number_digits="15550100")])


def test_me_designate_persists_and_resolves(tmp_path):
    config_path = str(tmp_path / "config.json")
    config = AppConfig()
    assert mecard.resolve_me(ContactStore(":memory:"), config) is None
    store = ContactStore(":memory:")
    try:
        uid = store.create(_contact())
        assert not mecard.is_me(config, uid)
        mecard.set_me_uid(config, config_path, uid)
        assert mecard.is_me(config, uid)
        assert not mecard.is_me(config, "other")
        reloaded = AppConfig.load(config_path)
        assert mecard.get_me_uid(reloaded) == uid
        record = mecard.resolve_me(store, reloaded)
        assert record is not None and record.uid == uid
        mecard.clear_me_uid(reloaded, config_path)
        assert mecard.get_me_uid(reloaded) == ""
        assert mecard.resolve_me(store, reloaded) is None
        assert AppConfig.load(config_path).me_uid == ""
    finally:
        store.close()


def test_me_resolve_missing_contact_is_none():
    store = ContactStore(":memory:")
    try:
        config = AppConfig(
            me_uid="00000000-0000-4000-8000-000000000000")
        assert mecard.resolve_me(store, config) is None
        assert mecard.me_share_vcard(store, config) == ""
    finally:
        store.close()


def test_me_share_yields_parseable_vcard_with_photo():
    from xx_contacts import photos as photos_module

    store = ContactStore(":memory:")
    try:
        uid = store.create(_contact())
        config = AppConfig(me_uid=uid)
        card = mecard.me_share_vcard(store, config)
        assert "FN:Ada Lovelace" in card
        data, _ = vcard.contact_from_vcard(card)
        assert data.display_name == "Ada Lovelace"

        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False,
                                      8, 64, 64)
        _ok, raw = pixbuf.save_to_bufferv("jpeg", ["quality"], ["85"])
        blob = photos_module.process_photo(bytes(raw))
        card_with_photo = mecard.me_share_vcard(store, config,
                                                photo_bytes=blob)
        assert "PHOTO:data:image/jpeg;base64," in card_with_photo
    finally:
        store.close()


def test_config_me_uid_ignores_blank_and_bad_payload(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"me_uid": 42}', encoding="utf-8")
    assert AppConfig.load(str(path)).me_uid == ""
    path.write_text('{"me_uid": "  "}', encoding="utf-8")
    assert AppConfig.load(str(path)).me_uid == ""
