"""ContactsClient over a fake session bus with the exact bus signatures.

The fake mirrors the real Gio traps: every D-Bus reply unpacks to a
TUPLE of out-args, async callbacks take exactly two args (no user_data
is ever passed), and signal handlers take exactly seven. A client that
cheats on any of these fails here just as it would on the device.
"""
from xx_dialer.contacts_client import (
    BUS_NAME,
    INTERFACE,
    OBJECT_PATH,
    ContactsClient,
    local_tags,
    lookup_number,
    normalize_tags,
    parse_vcard,
)


class FakeVariant:
    def __init__(self, fmt, value):
        self.fmt = fmt
        self.value = value

    def unpack(self):
        return self.value


class FakeGlib:
    Error = type("Error", (Exception,), {})

    def Variant(self, fmt, value):
        return FakeVariant(fmt, value)


class FakeGio:
    BusType = type("BusType", (), {"SESSION": "session"})
    BusNameWatcherFlags = type("Flags", (), {"NONE": 0})
    DBusCallFlags = type("Flags", (), {"NONE": 0})
    DBusSignalFlags = type("Flags", (), {"NONE": 0})

    def __init__(self):
        self.watches = []

    def bus_get_sync(self, *args):
        raise AssertionError("tests inject the bus directly")

    def bus_watch_name(self, bus_type, name, flags, appeared, vanished):
        self.watches.append((bus_type, name, flags, appeared, vanished))
        return len(self.watches)

    def bus_unwatch_name(self, watch_id):
        self.watches[watch_id - 1] = None


class FakeContactsService:
    """In-memory xx-contacts with revision history for List(since)."""

    VALID = {"starred", "business", "blocked"}

    def __init__(self):
        self.contacts = {}
        self.revision = 0
        self.history = []
        self.set_tags_calls = []
        self.update_tags_calls = []

    def put(self, uid, name, numbers, tags):
        self.contacts[uid] = {"name": name, "numbers": list(numbers),
                              "tags": set(tags)}
        self.revision += 1
        self.history.append((self.revision, "changed", uid))

    def delete(self, uid):
        self.contacts.pop(uid, None)
        self.revision += 1
        self.history.append((self.revision, "deleted", uid))

    def vcard(self, uid):
        contact = self.contacts[uid]
        lines = ["BEGIN:VCARD", "VERSION:3.0", "FN:%s" % contact["name"]]
        for number in contact["numbers"]:
            lines.append("TEL;TYPE=CELL:%s" % number)
        return "\n".join(lines) + "\n"

    def dispatch(self, method, params):
        if method == "List":
            (since,) = params
            changed = []
            deleted = []
            for rev, kind, uid in self.history:
                if rev <= since:
                    continue
                if kind == "changed" and uid not in changed:
                    changed.append(uid)
                if kind == "deleted" and uid not in deleted:
                    deleted.append(uid)
            return FakeVariant("(tas)", (self.revision, changed, deleted))
        if method == "Get":
            (uid,) = params
            if uid not in self.contacts:
                raise FakeGlib.Error("unknown uid: %s" % uid)
            return FakeVariant("(s)", (self.vcard(uid),))
        if method == "LookupNumber":
            (number,) = params
            for uid, contact in self.contacts.items():
                if number in contact["numbers"]:
                    return FakeVariant("(sas)", (uid, contact["name"],
                                                sorted(contact["tags"])))
            return FakeVariant("(sas)", ("", "", []))
        if method == "Search":
            query, limit = params
            digits = "".join(c for c in query if c.isdigit())
            rows = []
            for uid, contact in self.contacts.items():
                name_hit = query.casefold() in contact["name"].casefold()
                number_hit = bool(digits) and any(
                    digits in "".join(c for c in n if c.isdigit())
                    for n in contact["numbers"])
                if query and (name_hit or number_hit):
                    first = contact["numbers"][0] \
                        if contact["numbers"] else ""
                    rows.append((uid, contact["name"], first))
            return FakeVariant("(a(sss))", (rows[:limit],))
        if method == "SetTags":
            uid, tags = params
            self.set_tags_calls.append((uid, list(tags)))
            if uid in self.contacts:
                self.contacts[uid]["tags"] = {
                    t for t in tags if t in self.VALID}
                self.revision += 1
                self.history.append((self.revision, "changed", uid))
            return FakeVariant("()", ())
        if method == "UpdateTags":
            uid, add, remove = params
            self.update_tags_calls.append(
                (uid, list(add), list(remove)))
            if uid in self.contacts:
                tags = {t for t in self.contacts[uid]["tags"]
                        if t in self.VALID}
                tags |= {t for t in add if t in self.VALID}
                tags -= {t for t in remove if t in self.VALID}
                self.contacts[uid]["tags"] = tags
                self.revision += 1
                self.history.append((self.revision, "changed", uid))
            return FakeVariant("(t)", (self.revision,))
        raise AssertionError("unexpected method %s" % method)


class FakeBus:
    def __init__(self, service):
        self.service = service
        self.calls = []
        self.pending = []
        self._results = []
        self.subs = []
        self.unsubs = []
        self._next_id = 1

    def call(self, bus_name, path, iface, method, params=None,
             reply_type=None, flags=None, timeout=-1, cancellable=None,
             callback=None, *user_data):
        value = params.unpack() if params is not None else None
        self.calls.append((bus_name, path, iface, method, value,
                           user_data))
        self.pending.append((method, value, callback, user_data))

    def call_finish(self, _result):
        payload = self._results.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload

    def signal_subscribe(self, sender, iface, member, path, arg0,
                         flags, callback, user_data):
        self.subs.append({"sender": sender, "iface": iface,
                          "member": member, "path": path,
                          "callback": callback, "user_data": user_data})
        sid = self._next_id
        self._next_id += 1
        return sid

    def signal_unsubscribe(self, sid):
        self.unsubs.append(sid)

    def drain(self):
        while self.pending:
            method, value, callback, user_data = self.pending.pop(0)
            try:
                reply = self.service.dispatch(method, value)
            except Exception as err:
                self._results.append(err)
            else:
                self._results.append(reply)
            callback(self, object(), *user_data)

    def emit_changed(self, revision, uids):
        sub = next(s for s in self.subs if s["member"] == "Changed")
        params = FakeVariant("(tas)", (revision, list(uids)))
        sub["callback"](self, BUS_NAME, OBJECT_PATH, INTERFACE,
                        "Changed", params, sub["user_data"])


def _service_with_ada_bo():
    service = FakeContactsService()
    service.put("uid-ada", "Ada Lovelace", ["+14155550100"],
                {"starred", "business"})
    service.put("uid-bo", "Bo Dahl", ["+14155550200"], {"blocked"})
    return service


def _wired(store, service):
    bus = FakeBus(service)
    gio = FakeGio()
    client = ContactsClient(store)
    assert client.start(bus=bus, gio=gio, glib=FakeGlib())
    assert (bus, gio) == (client._bus, client._gio)
    return client, bus, gio


def _appear_and_drain(client, bus, gio, owner=":1.7"):
    _, name, _, appeared, _ = gio.watches[-1]
    assert name == BUS_NAME
    appeared(bus, BUS_NAME, owner)
    bus.drain()
    return bus


def _set_tags_for(bus, uid):
    return [tags for called_uid, tags in bus.service.set_tags_calls
            if called_uid == uid]


def _update_tags_for(bus, uid):
    return [(add, remove)
            for called_uid, add, remove in bus.service.update_tags_calls
            if called_uid == uid]


def test_start_watches_name_and_changed_signal(store):
    service = _service_with_ada_bo()
    client, bus, gio = _wired(store, service)
    assert not client.is_present
    assert gio.watches[-1][1] == BUS_NAME
    changed = [s for s in bus.subs if s["member"] == "Changed"]
    assert len(changed) == 1
    assert (changed[0]["sender"], changed[0]["iface"],
            changed[0]["path"]) == (BUS_NAME, INTERFACE, OBJECT_PATH)
    assert store.mirror_count() == 0


def test_warm_from_list_and_get(store):
    service = _service_with_ada_bo()
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    assert client.is_present
    assert client.revision == service.revision == 2
    assert bus.calls[0][3] == "List" and bus.calls[0][4] == (0,)
    assert lookup_number(store, "+14155550100") == (
        "Ada Lovelace", "starred")
    assert local_tags(store, "uid-ada") == {"starred", "business"}
    assert local_tags(store, "uid-bo") == {"blocked"}
    assert store.is_blocked("+14155550200")
    assert not store.is_blocked("+14155550100")
    assert service.set_tags_calls == []


def test_every_async_call_passes_no_user_data(store):
    service = _service_with_ada_bo()
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    client.set_business("uid-ada", False)
    bus.drain()
    assert bus.calls
    for call in bus.calls:
        assert call[5] == ()


def test_changed_signal_applies_incremental(store):
    service = _service_with_ada_bo()
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    store.add_blocked("+19999999999")
    service.put("uid-cara", "Cara M", ["+14155550300"], {"business"})
    service.delete("uid-bo")
    bus.emit_changed(service.revision, ["uid-cara", "uid-bo"])
    bus.drain()
    assert client.revision == service.revision
    assert lookup_number(store, "+14155550300") == ("Cara M", "business")
    assert lookup_number(store, "+14155550200") == ("", "unknown")
    assert local_tags(store, "uid-bo") == set()
    assert not store.is_blocked("+14155550200")
    assert store.is_blocked("+19999999999")


def test_edits_write_through_update_tags(store):
    """Toggles go out as atomic UpdateTags deltas, never full sets."""
    service = _service_with_ada_bo()
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    client.set_business("uid-ada", False)
    client.set_blocked("uid-ada", True)
    bus.drain()
    assert local_tags(store, "uid-ada") == {"starred", "blocked"}
    assert store.is_blocked("+14155550100")
    pushed = _update_tags_for(bus, "uid-ada")
    assert pushed == [([], ["business"]), (["blocked"], [])]
    assert bus.service.set_tags_calls == []
    assert service.contacts["uid-ada"]["tags"] == {"starred", "blocked"}
    client.set_star("uid-ada", False)
    bus.drain()
    assert _update_tags_for(bus, "uid-ada")[-1] == ([], ["starred"])


def test_concurrent_toggles_cannot_clobber(store):
    """Two toggles on different tags both survive: each write carries
    only its own delta, so an upstream change landing between them is
    kept instead of being overwritten by a stale full set."""
    service = _service_with_ada_bo()
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    client.set_business("uid-bo", True)
    service.put("uid-bo", "Bo Dahl", ["+14155550200"],
                {"blocked", "starred"})
    bus.emit_changed(service.revision, ["uid-bo"])
    client.set_blocked("uid-bo", False)
    bus.drain()
    assert service.contacts["uid-bo"]["tags"] == {"business", "starred"}
    assert _update_tags_for(bus, "uid-bo") == [
        (["business"], []), ([], ["blocked"])]


def test_update_tags_drops_unknown_tags(store):
    service = _service_with_ada_bo()
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    done = []
    client.update_tags_async("uid-ada", ["starred", "vip"],
                             ["business", "nope"],
                             on_done=lambda: done.append(True))
    bus.drain()
    assert done
    assert _update_tags_for(bus, "uid-ada") == [
        (["starred"], ["business"])]


def test_offline_edits_flush_on_reappear(store):
    service = FakeContactsService()
    service.put("uid-ada", "Ada Lovelace", ["+14155550100"],
                {"business"})
    service.put("uid-bo", "Bo Dahl", ["+14155550200"], set())
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    assert local_tags(store, "uid-ada") == {"business"}
    client.handle_vanished(bus, BUS_NAME)
    assert not client.is_present
    client.set_star("uid-ada", True)
    assert bus.service.set_tags_calls == []
    assert local_tags(store, "uid-ada") == {"business", "starred"}
    assert [uid for uid, _tags, _ts in store.pending_edits()] == ["uid-ada"]
    _, _, _, appeared, _ = gio.watches[-1]
    appeared(bus, BUS_NAME, ":1.9")
    bus.drain()
    assert client.is_present
    assert local_tags(store, "uid-ada") == {"business", "starred"}
    pushed = _set_tags_for(bus, "uid-ada")
    assert pushed and pushed[-1] == ["business", "starred"]
    assert _set_tags_for(bus, "uid-bo") == []
    assert store.pending_edits() == []
    bus.emit_changed(service.revision, ["uid-ada"])
    bus.drain()
    assert _set_tags_for(bus, "uid-ada") == pushed
    assert store.pending_edits() == []


def test_number_collision_overwrites_without_pushback(store):
    from xx_dialer.store import MirrorRow
    store.upsert_mirror([MirrorRow("local-9", "+14155550100", "Ada",
                                   False, None, False, 1)])
    store.add_star("local-9")
    store.upsert_tier("local-9", "biz")
    service = FakeContactsService()
    service.put("svc-ada", "Ada L.", ["+14155550100"], set())
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    assert _set_tags_for(bus, "svc-ada") == []
    assert service.contacts["svc-ada"]["tags"] == set()
    assert local_tags(store, "svc-ada") == set()
    assert lookup_number(store, "+14155550100") == ("Ada L.", "saved")
    assert [r.lookup_key for r in store.all_mirror()
            if r.e164 == "+14155550100"] == ["svc-ada"]


def test_upstream_retag_removes_star_without_pushback(store):
    service = FakeContactsService()
    service.put("uid-ada", "Ada Lovelace", ["+14155550100"], {"starred"})
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    assert lookup_number(store, "+14155550100") == (
        "Ada Lovelace", "starred")
    service.put("uid-ada", "Ada Lovelace", ["+14155550100"], {"business"})
    bus.emit_changed(service.revision, ["uid-ada"])
    bus.drain()
    assert lookup_number(store, "+14155550100") == (
        "Ada Lovelace", "business")
    assert local_tags(store, "uid-ada") == {"business"}
    assert not store.is_starred("uid-ada")
    assert bus.service.set_tags_calls == []


def test_dialer_write_sends_exact_set(store):
    service = FakeContactsService()
    service.put("uid-ada", "Ada Lovelace", ["+14155550100"], {"business"})
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    done = []
    client.set_tags_async("uid-ada", ["starred", "blocked"],
                          on_done=lambda: done.append(True))
    bus.drain()
    assert done
    pushed = _set_tags_for(bus, "uid-ada")
    assert pushed and pushed[-1] == ["blocked", "starred"]
    assert service.contacts["uid-ada"]["tags"] == {"starred", "blocked"}
    bus.emit_changed(service.revision, ["uid-ada"])
    bus.drain()
    assert local_tags(store, "uid-ada") == {"blocked", "starred"}
    assert lookup_number(store, "+14155550100") == (
        "Ada Lovelace", "starred")


def test_get_failure_keeps_revision(store):
    service = _service_with_ada_bo()
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    revision = client.revision
    service.delete("uid-bo")
    service.history.append((service.revision + 1, "changed", "uid-ghost"))
    bus.emit_changed(service.revision + 1, ["uid-ghost"])
    bus.drain()
    assert client.revision == revision
    assert lookup_number(store, "+14155550100") == (
        "Ada Lovelace", "starred")


def test_malformed_changed_signal_is_ignored(store):
    service = _service_with_ada_bo()
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    calls = len(bus.calls)
    sub = next(s for s in bus.subs if s["member"] == "Changed")

    class _BadParams:
        def unpack(self):
            raise ValueError("short read")

    sub["callback"](bus, BUS_NAME, OBJECT_PATH, INTERFACE, "Changed",
                    _BadParams(), sub["user_data"])
    bus.drain()
    assert len(bus.calls) == calls


def test_search_async_triples(store):
    service = _service_with_ada_bo()
    client, bus, gio = _wired(store, service)
    _appear_and_drain(client, bus, gio)
    got = []
    client.search_async("ada", 5, got.append)
    bus.drain()
    assert got == [[("uid-ada", "Ada Lovelace", "+14155550100")]]
    got = []
    client.search_async("415555020", 5, got.append)
    bus.drain()
    assert got == [[("uid-bo", "Bo Dahl", "+14155550200")]]


def test_vcard_parsing_tolerant():
    name, numbers = parse_vcard("BEGIN:VCARD\nFN: Ada L. \n"
                                "TEL;TYPE=CELL:+1 (415) 555-0100\n"
                                "TEL:+14155550100\n"
                                "TEL:nonsense\n"
                                "X-CUSTOM:kept-out\n")
    assert (name, numbers) == ("Ada L.", ["+14155550100"])
    assert parse_vcard("") == ("", [])
    assert parse_vcard(None) == ("", [])
    assert parse_vcard("BEGIN:VCARD\n") == ("", [])


def test_tag_normalization_drops_unknowns():
    assert normalize_tags([" Starred ", "BUSINESS", "vip", "blocked",
                           "starred"]) == ["blocked", "business", "starred"]
    assert normalize_tags(None) == []
