"""L4 photo tests: normalize, store, file modes, vCard PHOTO wire-up."""

import base64
import os
import stat

import pytest

from xx_contacts import photos, vcard
from xx_contacts.store import ContactData, ContactStore


def _test_jpeg(width=800, height=400, quality="90"):
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8,
                                  width, height)
    pixbuf.fill(0x336699ff)
    _ok, raw = pixbuf.save_to_bufferv("jpeg", ["quality"], [quality])
    return bytes(raw)


def _decoded_dims(blob):
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib

    loader = GdkPixbuf.PixbufLoader.new()
    loader.write_bytes(GLib.Bytes(blob))
    loader.close()
    pixbuf = loader.get_pixbuf()
    return pixbuf.get_width(), pixbuf.get_height()


def test_process_crops_square_downscales_and_reencodes():
    out = photos.process_photo(_test_jpeg(800, 400))
    assert out[:2] == b"\xff\xd8"
    assert _decoded_dims(out) == (400, 400)


def test_process_downscales_long_edge_to_512():
    out = photos.process_photo(_test_jpeg(1600, 1200))
    assert out[:2] == b"\xff\xd8"
    width, height = _decoded_dims(out)
    assert max(width, height) == 512
    assert (width, height) == (512, 512)


def test_process_rejects_garbage_and_empty():
    with pytest.raises(photos.PhotoError):
        photos.process_photo(b"")
    with pytest.raises(photos.PhotoError):
        photos.process_photo(b"this is not an image" * 10)


def test_save_photo_writes_0600_file_and_reads_back(tmp_path):
    photos_dir = str(tmp_path / "photos")
    path = photos.save_photo(photos_dir, "some-uid", _test_jpeg())
    assert path == os.path.join(photos_dir, "some-uid.jpg")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert photos.photo_file_mode_ok(path)
    blob = photos.read_photo_bytes(path)
    assert blob is not None and blob[:2] == b"\xff\xd8"
    photos.delete_photo(path)
    assert not os.path.exists(path)
    photos.delete_photo(path)
    assert photos.read_photo_bytes(path) is None


def test_photo_value_decodes_data_uri_and_30_base64():
    out = photos.process_photo(_test_jpeg())
    uri = photos.photo_data_uri(out)
    assert uri.startswith("data:image/jpeg;base64,")
    assert photos.photo_bytes_from_value("", uri) == out
    b64 = base64.b64encode(out).decode("ascii")
    assert photos.photo_bytes_from_value("ENCODING=b;TYPE=JPEG", b64) == out
    assert photos.photo_bytes_from_value("", "not-base64!!") is None


def test_photo_bytes_from_extra_picks_first_decodable():
    from xx_contacts.store import ExtraLine

    out = photos.process_photo(_test_jpeg())
    lines = [ExtraLine(prop="X-FOO", params="", value="bar", group=""),
             ExtraLine(prop="PHOTO", params="",
                       value=photos.photo_data_uri(out), group="")]
    assert photos.photo_bytes_from_extra(lines) == out
    assert photos.photo_bytes_from_extra([]) is None


def test_adopt_photo_moves_payload_to_file(tmp_path):
    data, _ = vcard.contact_from_vcard(
        "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada\r\n"
        "PHOTO:data:image/jpeg;base64,"
        + base64.b64encode(_test_jpeg()).decode("ascii")
        + "\r\nX-KEEP:yes\r\nEND:VCARD\r\n")
    assert any(e.prop == "PHOTO" for e in data.extra)
    path = photos.adopt_photo_from_extra(data, str(tmp_path), "ada-uid")
    assert path.endswith("ada-uid.jpg")
    assert data.photo_path == path
    assert not any(e.prop == "PHOTO" for e in data.extra)
    assert any(e.prop == "X-KEEP" for e in data.extra)
    assert photos.photo_file_mode_ok(path)


def test_vcard_export_embeds_photo_bytes_as_data_uri(tmp_path):
    blob = photos.process_photo(_test_jpeg())
    store = ContactStore(":memory:")
    try:
        record = store.get(store.create(ContactData(display_name="Ada")))
        text = vcard.contact_to_vcard(record, photo_bytes=blob)
        assert "PHOTO:data:image/jpeg;base64," in text
        data2, _ = vcard.contact_from_vcard(text)
        photo_lines = [e for e in data2.extra if e.prop == "PHOTO"]
        assert len(photo_lines) == 1
        assert photos.photo_bytes_from_value(
            photo_lines[0].params, photo_lines[0].value) == blob
    finally:
        store.close()


def test_vcard_export_without_bytes_keeps_uri_line():
    store = ContactStore(":memory:")
    try:
        data = ContactData(display_name="Ada", photo_path="/x/y.jpg")
        record = store.get(store.create(data))
        assert "PHOTO;VALUE=URI:/x/y.jpg" in vcard.contact_to_vcard(record)
    finally:
        store.close()
