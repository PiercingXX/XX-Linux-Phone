"""Finding 12 repro: Search misattributes email senders to phone contacts.

Mail from ``john1@example.com`` carries the digit "1", which prefix/suffix
matches every ``+1...`` contact, and those number hits rank first, so the
sender shows as an arbitrary contact instead of the email owner (or no
contact at all).
"""

from xx_contacts.store import ContactData, ContactStore, EmailEntry, PhoneEntry


def _store_with_bob_and_john():
    store = ContactStore(":memory:")
    store.create(ContactData(
        display_name="Bob Phone",
        phones=[PhoneEntry(number_raw="+1 555 0100",
                           number_e164="+15550100",
                           number_digits="15550100")]))
    store.create(ContactData(
        display_name="John Mail",
        emails=[EmailEntry(address="john1@example.com")]))
    return store


def test_email_query_does_not_match_phone_contacts():
    store = _store_with_bob_and_john()
    try:
        names = [name for _, name, _ in store.search("john1@example.com")]
        assert "Bob Phone" not in names
        assert "John Mail" in names
    finally:
        store.close()


def test_short_digit_queries_do_not_match_numbers():
    store = ContactStore(":memory:")
    try:
        store.create(ContactData(
            display_name="Bob Phone",
            phones=[PhoneEntry(number_raw="+1 555 0100",
                               number_e164="+15550100",
                               number_digits="15550100")]))
        assert store.search("1") == []
        assert store.search("12") == []
    finally:
        store.close()


def test_search_primary_prefers_email_for_autocomplete():
    store = ContactStore(":memory:")
    try:
        store.create(ContactData(
            display_name="Alice",
            phones=[PhoneEntry(number_raw="+1 555 0100",
                               number_digits="15550100")],
            emails=[EmailEntry(address="alice@example.com")]))
        rows = store.search("alice")
        assert rows
        assert rows[0][1] == "Alice"
        assert "@" in rows[0][2]
        assert rows[0][2] == "alice@example.com"
    finally:
        store.close()


def test_lookup_email_is_exact_and_case_insensitive():
    store = ContactStore(":memory:")
    try:
        uid = store.create(ContactData(
            display_name="John Mail",
            emails=[EmailEntry(address="John1@Example.com")]))
        assert store.lookup_email("john1@example.com") == (uid, "John Mail")
        assert store.lookup_email(" JOHN1@EXAMPLE.COM ") == (uid, "John Mail")
        assert store.lookup_email("john@example.com") is None
        assert store.lookup_email("john1@example.com.evil.com") is None
        assert store.lookup_email("not-an-address") is None
        assert store.lookup_email("") is None
    finally:
        store.close()


def test_service_lookup_email_dispatch():
    from gi.repository import Gio, GLib

    from xx_contacts.phones import LookupIndex
    from xx_contacts.service import (
        INTERFACE,
        INTROSPECTION_XML,
        OBJECT_PATH,
        ContactsService,
    )

    node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
    methods = {m.name: m for m in node.interfaces[0].methods}
    assert methods["LookupEmail"].in_args[0].signature == "s"
    assert [a.signature for a in
            methods["LookupEmail"].out_args] == ["s", "s"]

    class FakeInvocation:
        def __init__(self):
            self.reply = None
            self.error = None

        def return_value(self, parameters):
            self.reply = parameters

        def return_dbus_error(self, name, message):
            self.error = (name, message)

    store = ContactStore(":memory:")
    service = ContactsService(store, LookupIndex(), "US")
    try:
        uid = store.create(ContactData(
            display_name="John Mail",
            phones=[PhoneEntry(number_raw="+1 555 0100",
                               number_digits="15550100")],
            emails=[EmailEntry(address="john1@example.com")]))
        hit = FakeInvocation()
        service.handle_method_call(
            None, ":1.1", OBJECT_PATH, INTERFACE, "LookupEmail",
            GLib.Variant("(s)", ("JOHN1@EXAMPLE.COM",)), hit)
        assert hit.error is None
        assert hit.reply.unpack() == (uid, "John Mail")
        miss = FakeInvocation()
        service.handle_method_call(
            None, ":1.1", OBJECT_PATH, INTERFACE, "LookupEmail",
            GLib.Variant("(s)", ("ceo1234@example.com",)), miss)
        assert miss.error is None
        assert miss.reply.unpack() == ("", "")
    finally:
        store.close()
