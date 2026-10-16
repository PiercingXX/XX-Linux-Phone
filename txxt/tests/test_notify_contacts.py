"""L3 notify + contacts tests: redaction posture and resolver chain."""

import inspect

import pytest

from txxt import contacts, notify
from txxt.contacts import (
    CONTACTS_BUS_NAME,
    CONTACTS_IFACE,
    ContactsResolver,
    ContactHit,
    EdsFallbackBackend,
    GioContactsBackend,
    SearchHit,
)


class FakeReply:
    def __init__(self, values):
        self._values = tuple(values)

    def unpack(self):
        return self._values


class FakeContactsBus:
    """Fake modeling the REAL xx-contacts shapes: ``LookupNumber`` packs
    3 out-args (``unpack()`` is the triple directly), ``Search`` takes
    ``(su)`` and returns 1 tuple of ``(uid, name, number)`` rows,
    ``SetTags`` addresses the contact by uid and replaces the tag set."""

    def __init__(self):
        self.calls = []
        self.directory = {"+15551234567": ("uid-alice", "Alice", ["starred", "business"])}
        self.fail = False
        self.revision = 41

    def _by_uid(self, uid):
        for number, (entry_uid, _name, _tags) in self.directory.items():
            if entry_uid == uid:
                return number
        return None

    def call_sync(self, bus, path, iface, method, params, *rest):
        self.calls.append((bus, path, iface, method, params))
        if self.fail:
            raise OSError("contacts away")
        if method == "LookupNumber":
            number = params.unpack()[0]
            if number in self.directory:
                uid, name, tags = self.directory[number]
                return FakeReply((uid, name, list(tags)))
            return FakeReply(("", "", []))
        if method == "Search":
            query, _limit = params.unpack()
            query = query.lower()
            if query == "":
                return FakeReply(([],))
            return FakeReply(([
                (uid, name, number)
                for number, (uid, name, _tags) in self.directory.items()
                if query in name.lower() or query in number
            ],))
        if method == "SetTags":
            uid, tags = params.unpack()
            number = self._by_uid(uid)
            if number is None:
                raise OSError(f"unknown contact: {uid}")
            _uid, name, _old = self.directory[number]
            self.directory[number] = (_uid, name, list(tags))
            return FakeReply(())
        if method == "UpdateTags":
            uid, add, remove = params.unpack()
            number = self._by_uid(uid)
            if number is None:
                raise OSError(f"unknown contact: {uid}")
            _uid, name, old = self.directory[number]
            tags = (set(old) | {str(t) for t in add}) - {str(t) for t in remove}
            self.directory[number] = (_uid, name, sorted(tags))
            self.revision += 1
            return FakeReply((self.revision,))
        raise AssertionError(method)

    def signal_subscribe(
        self, sender, iface, member, path, arg0, flags, handler, user_data
    ):
        self.last_sub = (sender, iface, member, handler, user_data)
        return 7


class TestGioContactsBackend:
    def test_lookup_returns_hit_with_uid_and_tags(self):
        hit = GioContactsBackend(FakeContactsBus()).lookup("+15551234567")
        assert hit == ContactHit(
            "+15551234567", "uid-alice", "Alice", ("starred", "business"),
        )
        assert hit.display == "Alice" and hit.starred and not hit.blocked

    def test_unknown_number_returns_none_for_eds_fallthrough(self):
        assert GioContactsBackend(FakeContactsBus()).lookup("+1999") is None

    def test_bus_down_resolves_to_none(self):
        bus = FakeContactsBus()
        bus.fail = True
        assert GioContactsBackend(bus).lookup("+15551234567") is None

    def test_search_lists_uid_name_number_triples(self):
        hits = GioContactsBackend(FakeContactsBus()).search("ali")
        assert hits == [SearchHit("uid-alice", "Alice", "+15551234567")]

    def test_search_sends_query_and_limit(self):
        bus = FakeContactsBus()
        GioContactsBackend(bus).search("ali", limit=10)
        _bus, _path, _iface, method, params = bus.calls[-1]
        assert method == "Search"
        assert params.unpack() == ("ali", 10)

    def test_empty_query_matches_nothing_like_the_service(self):
        assert GioContactsBackend(FakeContactsBus()).search("") == []

    def test_set_tags_addresses_uid_and_replaces(self):
        bus = FakeContactsBus()
        assert GioContactsBackend(bus).set_tags("uid-alice", ["blocked"]) is True
        assert bus.calls[-1][3] == "SetTags"
        assert bus.calls[-1][4].unpack() == ("uid-alice", ["blocked"])
        hit = GioContactsBackend(bus).lookup("+15551234567")
        assert hit is not None and hit.blocked and not hit.starred

    def test_starred_toggle_preserves_other_tags(self):
        bus = FakeContactsBus()
        backend = GioContactsBackend(bus)
        assert backend.set_starred("+15551234567", False) is True
        hit = backend.lookup("+15551234567")
        assert hit is not None and not hit.starred
        assert "business" in hit.tags
        assert backend.set_starred("+15551234567", True) is True
        hit = backend.lookup("+15551234567")
        assert hit is not None and hit.starred
        assert "business" in hit.tags

    def test_starred_toggle_unknown_number_is_false(self):
        assert GioContactsBackend(FakeContactsBus()).set_starred("+1999", True) is False

    def test_update_tags_sends_atomic_add_remove(self):
        from gi.repository import GLib

        bus = FakeContactsBus()
        assert GioContactsBackend(bus).update_tags("uid-alice", ["blocked"], ["starred"]) is True
        _bus, _path, _iface, method, params = bus.calls[-1]
        assert method == "UpdateTags"
        assert params.unpack() == ("uid-alice", ["blocked"], ["starred"])
        hit = GioContactsBackend(bus).lookup("+15551234567")
        assert hit is not None and hit.blocked and not hit.starred
        assert "business" in hit.tags
        real = GLib.Variant("(sasas)", ("uid-1", ["a"], ["b"]))
        assert real.unpack() == ("uid-1", ["a"], ["b"])

    def test_starred_toggle_is_one_atomic_call(self):
        bus = FakeContactsBus()
        backend = GioContactsBackend(bus)
        assert backend.set_blocked("+15551234567", True) is True
        methods = [call[3] for call in bus.calls]
        assert methods == ["LookupNumber", "UpdateTags"]
        assert bus.calls[-1][4].unpack() == ("uid-alice", ["blocked"], [])
        hit = backend.lookup("+15551234567")
        assert hit is not None and hit.blocked and hit.starred
        assert "business" in hit.tags

    def test_blocked_toggle_unknown_number_never_writes(self):
        bus = FakeContactsBus()
        assert GioContactsBackend(bus).set_blocked("+1999", True) is False
        assert [call[3] for call in bus.calls] == ["LookupNumber"]

    def test_changed_subscription_shape(self):
        bus = FakeContactsBus()

        def handler(conn, sender, path, iface, signal, params, user_data):
            pass

        assert GioContactsBackend(bus).subscribe_changed(handler) == 7
        assert bus.last_sub[0:3] == (CONTACTS_BUS_NAME, CONTACTS_IFACE, "Changed")
        assert bus.last_sub[4] is None
        assert len(inspect.signature(handler).parameters) == 7

    def test_real_bus_name_and_path(self):
        assert CONTACTS_BUS_NAME == "io.piercingxx.Contacts"
        assert contacts.CONTACTS_OBJECT_PATH == "/io/piercingxx/Contacts"
        assert CONTACTS_IFACE == "io.piercingxx.Contacts1"

    def test_lookup_reply_is_bare_triple_in_real_gio(self):
        from gi.repository import GLib

        reply = GLib.Variant("(ssas)", ("uid-1", "Alice", ["starred"]))
        uid, name, tags = reply.unpack()
        assert (uid, name, list(tags)) == ("uid-1", "Alice", ["starred"])
        search_reply = GLib.Variant("(a(sss))", ([("uid-1", "Alice", "+1555")],))
        assert search_reply.unpack()[0] == [("uid-1", "Alice", "+1555")]


class DictBackend(contacts.ContactsBackend):
    """In-memory backend for resolver-chain tests."""

    def __init__(self, hits=None, searches=None):
        self.hits = hits or {}
        self.searches = searches or {}
        self.tags_written = {}

    def lookup(self, number):
        return self.hits.get(number)

    def search(self, query, limit=50):
        return list(self.searches.get(query, []))

    def set_tags(self, uid, tags):
        self.tags_written[uid] = list(tags)
        return True


class TestContactsResolver:
    def test_first_backend_wins(self):
        resolver = ContactsResolver([
            DictBackend(hits={"+1": ContactHit("+1", "uid-ann", "Ann", ("starred",))}),
            DictBackend(hits={"+1": ContactHit("+1", "uid-bob", "Bob")}),
        ])
        assert resolver.display_name("+1") == "Ann"
        assert resolver.is_starred("+1") is True

    def test_falls_through_to_bare_number(self):
        resolver = ContactsResolver([DictBackend()])
        assert resolver.display_name("+1999") == "+1999"
        assert resolver.is_starred("+1999") is False
        assert resolver.is_blocked("+1999") is False

    def test_gio_miss_falls_through_to_next_backend(self):
        resolver = ContactsResolver([
            GioContactsBackend(FakeContactsBus()),
            DictBackend(hits={"+1999": ContactHit("+1999", "uid-e", "Eve")}),
        ])
        assert resolver.display_name("+1999") == "Eve"

    def test_eds_fallback_never_raises(self):
        hit = EdsFallbackBackend().lookup("+15551234567")
        assert hit is None or isinstance(hit, ContactHit)
        assert EdsFallbackBackend().search("x") == []
        assert EdsFallbackBackend().set_tags("uid-1", ["starred"]) is False

    def test_known_numbers_unions_locals_and_search(self):
        resolver = ContactsResolver([
            DictBackend(searches={"": [SearchHit("uid-alice", "Alice", "+15551234567")]}),
        ])
        assert resolver.known_numbers({"+1999"}) == {"+1999", "+15551234567"}

    def test_resolver_starred_toggle_keeps_other_tags(self):
        backend = DictBackend(
            hits={"+1": ContactHit("+1", "uid-ann", "Ann", ("starred", "business"))},
        )
        resolver = ContactsResolver([backend])
        assert resolver.set_starred("+1", False) is True
        assert backend.tags_written == {"uid-ann": ["business"]}
        assert resolver.set_starred("+1999", True) is False

    def test_session_resolver_prefers_xx_contacts(self):
        resolver = contacts.session_resolver(FakeContactsBus())
        assert resolver.display_name("+15551234567") == "Alice"


class FakeNotifyBus:
    def __init__(self):
        self.calls = []
        self.next_id = 10
        self.fail = False

    def call_sync(self, bus, path, iface, method, params, *rest):
        self.calls.append((bus, path, iface, method, params))
        if self.fail:
            raise OSError("notif down")
        self.next_id += 1
        return FakeReply((self.next_id,))

    def signal_subscribe(
        self, sender, iface, member, path, arg0, flags, handler, user_data
    ):
        self.last_sub = (sender, iface, member, handler, user_data)
        return 3


def notif_payload(bus):
    assert bus.calls and bus.calls[0][3] == "Notify"
    return bus.calls[0][4].unpack()


class TestNotifyPosture:
    def test_sender_only_default_has_empty_body(self):
        bus = FakeNotifyBus()
        nid = notify.notify_message(bus, "+1555", display_name=lambda s: "Alice")
        assert nid is not None
        _app, _id, _icon, summary, body, actions, hints, _timeout = notif_payload(bus)
        assert summary == "Alice" and body == ""
        assert "reply" in actions and "Reply" in actions

    def test_blank_display_falls_back_to_number(self):
        assert notify.notification_title("+1555", display_name=lambda s: "  ") == "+1555"

    def test_content_posture_carries_preview(self):
        bus = FakeNotifyBus()
        notify.notify_message(bus, "+1555", preview_body="see this")
        assert notif_payload(bus)[4] == "see this"

    def test_sound_hint_only_for_sound_style(self):
        assert "sound-name" in notify.hints_for_style("sound")
        assert "sound-name" not in notify.hints_for_style("silent")
        assert notify.hints_for_style("silent")["suppress-sound"] is True

    def test_never_bubbles(self):
        assert notify.should_bubble() is False

    def test_notif_down_returns_none(self):
        bus = FakeNotifyBus()
        bus.fail = True
        assert notify.notify_message(bus, "+1555") is None

    def test_action_invoked_parses_and_subscribes(self):
        bus = FakeNotifyBus()

        def handler(conn, sender, path, iface, signal, params, user_data):
            pass

        assert notify.subscribe_action_invoked(bus, handler) == 3
        assert bus.last_sub[0:3] == (
            notify.NOTIF_BUS_NAME, notify.NOTIF_IFACE, "ActionInvoked",
        )
        assert bus.last_sub[4] is None
        assert len(inspect.signature(handler).parameters) == 7

        class P:
            def unpack(self):
                return (11, "reply")

        assert notify.parse_action_invoked(P()) == (11, "reply")

        class Bad:
            def unpack(self):
                raise ValueError("x")

        assert notify.parse_action_invoked(Bad()) is None


def test_tag_vocabulary():
    assert set(contacts.tags_for_display()) == {"starred", "blocked"}


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_notify_variant_shape_is_real_gio():
    from gi.repository import GLib

    variant = GLib.Variant(
        "(susssasa{sv}i)", ("TxxT", 0, "", "s", "b", [], {}, -1)
    )
    assert variant.unpack()[3] == "s"
