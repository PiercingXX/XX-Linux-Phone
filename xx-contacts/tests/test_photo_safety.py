"""Findings 13 + 34 repro: photo handling destroys or leaks data.

13: ``Create`` with an existing UID and an embedded PHOTO overwrites
``photos/<uid>.jpg`` before the insert, then unlinks it when the insert
fails with "already exists" -- the stored contact loses its photo.

34: an imported ``PHOTO;VALUE=URI:/path/outside/photos`` is stored as
given and later embedded as base64 by export/share, leaking arbitrary
file bytes; non-JPEG files must never be embedded.
"""

import base64
import os

from xx_contacts import import_export as import_export_module
from xx_contacts import photos as photos_module
from xx_contacts.phones import LookupIndex
from xx_contacts.service import ContactsService
from xx_contacts.store import (
    ContactData,
    ContactStore,
    StoreError,
)


def _jpeg_bytes(fill=0x336699FF):
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 8, 8)
    pixbuf.fill(fill)
    _ok, raw = pixbuf.save_to_bufferv("jpeg", ["quality"], ["90"])
    return bytes(raw)


def _card_with_photo(uid, jpeg):
    return ("BEGIN:VCARD\r\nVERSION:4.0\r\n"
            f"UID:{uid}\r\nFN:Ada\r\n"
            "PHOTO:data:image/jpeg;base64,"
            + base64.b64encode(jpeg).decode("ascii")
            + "\r\nEND:VCARD\r\n")


def test_create_collision_keeps_existing_photo(tmp_path):
    store = ContactStore(":memory:")
    photos_dir = str(tmp_path / "photos")
    service = ContactsService(store, LookupIndex(), "US",
                              photos_dir=photos_dir)
    try:
        jpeg = _jpeg_bytes()
        uid = service.create(_card_with_photo(
            "11111111-1111-4111-8111-111111111111", jpeg))
        photo_path = os.path.join(photos_dir, uid + ".jpg")
        with open(photo_path, "rb") as handle:
            before = handle.read()
        assert before[:2] == b"\xff\xd8"
        other_jpeg = photos_module.process_photo(
            _jpeg_bytes(fill=0x993333FF))
        assert other_jpeg != before
        try:
            service.create(_card_with_photo(uid, other_jpeg))
        except Exception:
            pass
        else:
            raise AssertionError("duplicate UID create must fail")
        assert os.path.exists(photo_path)
        with open(photo_path, "rb") as handle:
            assert handle.read() == before
    finally:
        store.close()


def test_failed_import_keeps_existing_photo(tmp_path):
    store = ContactStore(":memory:")
    photos_dir = str(tmp_path / "photos")
    service = ContactsService(store, LookupIndex(), "US",
                              photos_dir=photos_dir)
    try:
        jpeg = _jpeg_bytes()
        uid = service.create(_card_with_photo(
            "22222222-2222-4222-8222-222222222222", jpeg))
        photo_path = os.path.join(photos_dir, uid + ".jpg")
        with open(photo_path, "rb") as handle:
            before = handle.read()

        real_apply = store.apply_import

        def _boom(creates, updates):
            raise StoreError("boom")

        store.apply_import = _boom
        try:
            service.import_vcards(_card_with_photo(uid, jpeg))
        except Exception:
            pass
        else:
            raise AssertionError("failing import must raise")
        finally:
            store.apply_import = real_apply
        assert os.path.exists(photo_path)
        with open(photo_path, "rb") as handle:
            assert handle.read() == before
    finally:
        store.close()


def test_read_photo_bytes_rejects_non_jpeg(tmp_path):
    secret = tmp_path / "id_rsa"
    secret.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n")
    assert photos_module.read_photo_bytes(str(secret)) is None


def test_export_never_embeds_outside_photo_path(tmp_path):
    secret = tmp_path / "id_rsa"
    secret.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n")
    store = ContactStore(":memory:")
    try:
        record = store.get(store.create(
            ContactData(display_name="Sneaky",
                        photo_path=str(secret))))
        exported = import_export_module.export_text([record])
        assert "BEGIN OPENSSH" not in exported
        assert "PHOTO:data:image/jpeg;base64," not in exported
    finally:
        store.close()


def test_service_drops_photo_path_outside_photos_dir(tmp_path):
    store = ContactStore(":memory:")
    photos_dir = str(tmp_path / "photos")
    service = ContactsService(store, LookupIndex(), "US",
                              photos_dir=photos_dir)
    try:
        secret = tmp_path / "id_rsa"
        secret.write_text("top-secret\n")
        card = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Sneaky\r\n"
                f"PHOTO;VALUE=URI:{secret}\r\nEND:VCARD\r\n")
        uid = service.create(card)
        assert store.get(uid).photo_path == ""
    finally:
        store.close()
