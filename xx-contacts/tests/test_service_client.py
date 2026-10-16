"""GUI write-helper tests: vCard payloads and D-Bus error mapping.

These drive ``service_client`` — the exact helper the list, detail
and editor pages write through — against a fake bus connection, so a
regression in argument encoding or error mapping fails here, not on
the device. Real-bus behavior (activation, sender-filtered
``Changed``) is covered by ``test_dbus_activation``.
"""

import pytest
from gi.repository import GLib

from xx_contacts import editor as editor_module
from xx_contacts import service_client
from xx_contacts.service import (
    ERROR_INVALID_ARGUMENT,
    ERROR_NOT_FOUND,
    INTERFACE,
)
from xx_contacts.store import ContactData, NotFoundError


class FakeReply:
    def __init__(self, values):
        self._values = values

    def unpack(self):
        return self._values


class FakeConnection:
    def __init__(self, reply=(), error=None):
        self.calls = []
        self._reply = reply
        self._error = error

    def call_sync(self, bus_name, path, interface, method, parameters,
                  reply_type, flags, timeout, cancellable):
        self.calls.append((bus_name, path, interface, method,
                           parameters.unpack() if parameters is not None
                           else ()))
        if self._error is not None:
            raise self._error
        return FakeReply(self._reply)


def _glib_error(name: str, message: str) -> GLib.Error:
    return GLib.Error(f"GDBus.Error:{name}: {message}")


def _named_form(**overrides):
    form = editor_module.EditorForm(
        given="Ada", family="Lovelace",
        phones=[editor_module.PhoneForm(number="+1 555 0100",
                                       type="CELL", is_primary=True)],
        emails=[editor_module.EmailForm(address="ada@example.com",
                                       type="HOME")])
    for key, value in overrides.items():
        setattr(form, key, value)
    return form


def test_form_to_vcard_round_trips_tags_groups_and_extra():
    from xx_contacts.store import ExtraLine

    original = ContactData(display_name="Ada Lovelace", starred=True,
                           photo_path="/photos/keep.jpg",
                           extra=[ExtraLine(prop="X-KEEP", params="",
                                           value="yes", group="")])
    form = editor_module.form_from_record(original)
    form.groups = ["Family"]
    form.blocked = True
    data = editor_module.apply_form_to_data(original, form, "US")
    card = service_client.form_to_vcard("some-uid", data)
    assert "X-STARRED:1" in card
    assert "X-BLOCKED:1" in card
    assert "CATEGORIES:Family" in card
    assert "PHOTO;VALUE=URI:/photos/keep.jpg" in card
    assert "X-KEEP:yes" in card

    from xx_contacts import vcard as vcard_module

    parsed, _hint = vcard_module.contact_from_vcard(card, "US")
    assert parsed.starred and parsed.blocked
    assert parsed.groups == ["Family"]
    assert parsed.photo_path == "/photos/keep.jpg"
    assert any(line.prop == "X-KEEP" for line in parsed.extra)


def test_form_to_vcard_carries_photo_bytes_as_data_uri():
    data = ContactData(display_name="Ada")
    card = service_client.form_to_vcard("", data, b"\xff\xd8fake")
    assert "PHOTO:data:image/jpeg;base64," in card
    assert card.count("UID:") == 1


def test_form_to_vcard_new_contact_uid_line_is_dropped_by_parser():
    from xx_contacts import vcard as vcard_module

    card = service_client.form_to_vcard("", ContactData(display_name="A"))
    _data, hint = vcard_module.contact_from_vcard(card, "US")
    assert hint is None


def test_create_sync_returns_uid_and_targets_service():
    from xx_contacts.service import BUS_NAME, OBJECT_PATH

    conn = FakeConnection(reply=("new-uid",))
    assert service_client.create_sync(conn, "BEGIN:VCARD\r\nEND:VCARD\r\n") \
        == "new-uid"
    bus_name, path, iface, method, args = conn.calls[0]
    assert (bus_name, path, iface, method) == (
        BUS_NAME, OBJECT_PATH, INTERFACE, "Create")
    assert args == ("BEGIN:VCARD\r\nEND:VCARD\r\n",)


def test_update_tags_sync_returns_revision():
    conn = FakeConnection(reply=(7,))
    assert service_client.update_tags_sync(
        conn, "uid-1", add=("starred",), remove=("blocked",)) == 7
    assert conn.calls[0][3] == "UpdateTags"
    assert conn.calls[0][4] == ("uid-1", ["starred"], ["blocked"])


def test_sync_maps_not_found_onto_store_exception():
    conn = FakeConnection(error=_glib_error(ERROR_NOT_FOUND,
                                            "unknown contact: uid-1"))
    with pytest.raises(NotFoundError):
        service_client.delete_sync(conn, "uid-1")


def test_sync_maps_invalid_argument_onto_value_error():
    conn = FakeConnection(error=_glib_error(ERROR_INVALID_ARGUMENT,
                                            "unknown tags: vip"))
    with pytest.raises(ValueError, match="vip"):
        service_client.set_tags_sync(conn, "uid-1", ["vip"])


def test_sync_maps_transport_failure_onto_runtime_error():
    conn = FakeConnection(error=GLib.Error("No such name on the bus"))
    with pytest.raises(RuntimeError, match="not reachable"):
        service_client.update_tags_sync(conn, "uid-1", add=("starred",))


def test_reply_types_are_variant_types_not_strings():
    seen = []

    class TypeSniffConnection(FakeConnection):
        def call_sync(self, *args):
            seen.append(args[5])
            return FakeReply((0,))

    service_client.update_tags_sync(TypeSniffConnection(), "u",
                                    add=("starred",))
    assert isinstance(seen[0], GLib.VariantType)
    assert seen[0].dup_string() == "(t)"
