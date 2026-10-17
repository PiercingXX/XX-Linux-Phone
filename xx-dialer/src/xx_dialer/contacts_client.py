"""xx-contacts session-bus client: suite contacts <-> local cache tables.

xx-contacts owns the contacts and exposes ``io.piercingxx.Contacts`` /
``/io/piercingxx/Contacts`` / ``io.piercingxx.Contacts1`` (PORT_SPEC
review note 5, which supersedes the sidecar-file design). This module is
the dialer's consumer: it watches the bus name, warms the dialer's
``star_overlay`` / ``tier_member`` (biz + Blocked) / ``blocked_number``
cache tables from ``List(0)`` + ``Get`` (+ ``LookupNumber`` per number
to learn the tags), keeps them warm via ``Changed`` +
``List(since_revision)``, and writes dialer edits through with the
atomic ``UpdateTags(uid, add, remove)``. When xx-contacts is present
it is the single source of truth: every refresh overwrites the local
cache from ``List``/``Get`` (tags removed upstream disappear locally,
including the star overlay), and dialer edits send single-tag
add/remove deltas — never a read-modify-write of the full set, so two
clients toggling different tags cannot clobber each other. When
there is no bus the local tables stay authoritative; edits made while
the service merely owns no name still go out directly (an idle-exited
service auto-starts on the call) and only a real failure records the
toggle's delta in ``pending_tag_edit`` for replay as ``UpdateTags``
once the service answers again.

The ``LookupNumber`` dataset for the dialer's own Policy1 service
(``lookup_number``) answers from the warm SQLite tables only, never
from D-Bus, so it stays on the microsecond path the screener needs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from xx_dialer import policy, t9
from xx_dialer.store import BIZ_TIER, BLOCKED_GROUP, MirrorRow, Store

BUS_NAME = "io.piercingxx.Contacts"
OBJECT_PATH = "/io/piercingxx/Contacts"
INTERFACE = "io.piercingxx.Contacts1"

VALID_TAGS = frozenset({"starred", "business", "blocked"})

TIER_STARRED = "starred"
TIER_BUSINESS = "business"
TIER_SAVED = "saved"
TIER_UNKNOWN = "unknown"

_CALL_TIMEOUT_MS = 5000


def normalize_tags(tags) -> list[str]:
    """Tolerant tag parse: case/whitespace/duplicates collapse, unknowns drop."""
    out = set()
    for tag in tags or []:
        name = str(tag).strip().lower()
        if name in VALID_TAGS:
            out.add(name)
    return sorted(out)


def _unfold_lines(text: str | None) -> list[str]:
    """Physical lines to logical lines: a leading space/tab continues."""
    logical: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n") \
            .split("\n"):
        if raw[:1] in (" ", "\t") and logical:
            logical[-1] += raw[1:]
        elif raw.strip():
            logical.append(raw)
    return logical


def _unescape_value(value: str) -> str:
    """Inverse of xx-contacts' escape_value (\\n/\\,/\\;/\\\\)."""
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _prop_name(line: str) -> str:
    """Upper-cased property name with params and group prefix stripped."""
    head, _, _ = line.partition(":")
    head = head.split(";")[0].strip()
    group, dot, rest = head.partition(".")
    if dot and group.replace("-", "").isalnum() and rest:
        head = rest
    return head.upper()


def parse_vcard(text: str | None,
                default_region: str = "US") -> tuple[str, list[str]]:
    """First FN value plus TEL numbers in card order, E.164-normalized.

    Garbage yields ("", []), never raises; unparseable TEL lines drop.
    xx-contacts writes labeled phones as grouped ``item{n}.TEL`` lines,
    folds long lines and escapes FN text, so unfold, strip the group
    prefix and unescape before matching. Numbers canonicalize with the
    dial region so national and E.164 forms share one cache identity.
    """
    name = ""
    numbers: list[str] = []
    for line in _unfold_lines(text):
        stripped = line.strip()
        if not stripped:
            continue
        prop = _prop_name(stripped)
        _, _, value = stripped.partition(":")
        if prop == "FN":
            if not name and value.strip():
                name = _unescape_value(value.strip())
        elif prop == "TEL":
            normalized = policy.canonical_number(
                _unescape_value(value.strip()), default_region)
            if normalized is not None and normalized not in numbers:
                numbers.append(normalized)
    return (name, numbers)


def new_contact_vcard(number: str, display_name: str | None = None) -> str:
    """Minimal vCard 3.0 carrying a prefilled number for Create.

    xx-contacts exposes no editor-prefill path (its Pick returns
    NotImplemented and its app takes no number argument), so adding a
    number from Recents/Keypad means Create with the TEL prefilled.
    The FN falls back to the number itself when no name is known.
    """
    from xx_dialer.policy import normalize_number

    digits = (number or "").strip()
    e164 = normalize_number(digits) or digits
    name = (display_name or "").strip() or e164
    safe = name.replace("\\", "\\\\").replace("\n", " ")
    tel = e164.replace("\\", "\\\\").replace("\n", "")
    return ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:%s\r\n"
            "TEL;TYPE=CELL:%s\r\nEND:VCARD\r\n" % (safe, tel))


def local_tags(store: Store, uid: str) -> set[str]:
    """Tag set the local cache tables currently record for uid."""
    tags = set()
    if store.is_starred(uid):
        tags.add("starred")
    else:
        for number in store.mirror_numbers_for_key(uid):
            row = store.find_mirror(number)
            if row is not None and row.lookup_key == uid and row.starred:
                tags.add("starred")
                break
    tiers = {tier for key, tier, _ in store.all_tiers() if key == uid}
    if BIZ_TIER in tiers:
        tags.add("business")
    if BLOCKED_GROUP in tiers:
        tags.add("blocked")
    return tags


def lookup_number(store: Store, e164: str | None) -> tuple[str, str]:
    """Cache-backed (display_name, tier) for the Policy1 LookupNumber feed.

    Warm SQLite reads only — no D-Bus, so the shell's per-call consult
    stays in microseconds. Starred beats business; tier has no blocked
    value (blocking is a verdict input, not a caller-ID tier), so a
    blocked contact reports its other tier, else saved.
    """
    if not e164:
        return ("", TIER_UNKNOWN)
    row = store.find_mirror(e164)
    if row is None:
        return ("", TIER_UNKNOWN)
    name = row.display_name or ""
    if row.starred or store.is_starred(row.lookup_key):
        return (name, TIER_STARRED)
    if row.biz_tier:
        return (name, TIER_BUSINESS)
    return (name, TIER_SAVED)


def keypad_search(store: Store, query: str,
                  limit: int = 10) -> list[tuple[str, str, str, object]]:
    """Match-as-you-type over the warm mirror: (uid, e164, name, hit)."""
    entries = [(row.lookup_key, row.e164, row.display_name)
               for row in store.all_mirror()]
    return t9.search(entries, query, limit)


def _blocked_elsewhere(store: Store, uid: str, number: str) -> bool:
    for key, tier, _ in store.all_tiers():
        if tier == BLOCKED_GROUP and key != uid \
                and number in store.mirror_numbers_for_key(key):
            return True
    return False


@dataclass
class SyncReport:
    applied: int = 0
    deleted: int = 0
    revision: int = 0


class ContactsClient:
    """Async Gio consumer of io.piercingxx.Contacts1 with a warm cache."""

    def __init__(self, store: Store, on_sync=None,
                 default_region: str = "US") -> None:
        self._store = store
        self._on_sync = on_sync
        self._default_region = default_region or "US"
        self._bus = None
        self._gio = None
        self._glib = None
        self._watch_id = None
        self._signal_id = None
        self._present = False
        self._revision = 0
        self._refresh_inflight = False
        self._refresh_queued = False
        self._needs_reconcile = False

    @property
    def is_present(self) -> bool:
        return self._present

    @property
    def revision(self) -> int:
        return self._revision

    def start(self, bus=None, gio=None, glib=None) -> bool:
        """Subscribe + watch the service; False leaves offline mode."""
        if gio is None or glib is None:
            try:
                from gi.repository import Gio as _gio, GLib as _glib
            except ImportError:
                return False
            gio = _gio if gio is None else gio
            glib = _glib if glib is None else glib
        if bus is None:
            try:
                bus = gio.bus_get_sync(gio.BusType.SESSION, None)
            except Exception:
                return False
        if bus is None:
            return False
        self._bus = bus
        self._gio = gio
        self._glib = glib
        try:
            self._signal_id = bus.signal_subscribe(
                BUS_NAME, INTERFACE, "Changed", OBJECT_PATH, None,
                gio.DBusSignalFlags.NONE, self._on_changed_signal, None)
            self._watch_id = gio.bus_watch_name(
                gio.BusType.SESSION, BUS_NAME,
                gio.BusNameWatcherFlags.NONE,
                self.handle_appeared, self.handle_vanished)
        except Exception:
            return False
        return True

    def stop(self) -> None:
        bus, gio = self._bus, self._gio
        if bus is None or gio is None:
            return
        try:
            if self._signal_id is not None:
                bus.signal_unsubscribe(self._signal_id)
        except Exception:
            pass
        try:
            if self._watch_id is not None:
                gio.bus_unwatch_name(self._watch_id)
        except Exception:
            pass
        self._signal_id = None
        self._watch_id = None
        self._present = False

    def handle_appeared(self, conn, name, owner) -> None:
        if conn is not None:
            self._bus = conn
        self._present = True
        self._needs_reconcile = True
        self.refresh()

    def handle_vanished(self, conn, name) -> None:
        self._present = False

    def refresh(self) -> None:
        if self._bus is None or not self._present:
            return
        if self._refresh_inflight:
            self._refresh_queued = True
            return
        self._refresh_inflight = True
        self.list_async(self._revision, self._on_list_reply,
                        self._on_list_error)

    # ---- raw async API -------------------------------------------------

    def _call(self, method: str, params, on_reply,
              on_error=None) -> None:
        def _done(conn, result) -> None:
            try:
                reply = conn.call_finish(result)
            except Exception as err:
                if on_error is not None:
                    on_error(err)
                return
            on_reply(reply)

        self._bus.call(BUS_NAME, OBJECT_PATH, INTERFACE, method, params,
                       None, self._gio.DBusCallFlags.NONE,
                       _CALL_TIMEOUT_MS, None, _done)

    def lookup_async(self, number: str, on_done,
                     on_error=None) -> None:
        params = self._glib.Variant("(s)", (number,))

        def _reply(reply) -> None:
            try:
                uid, display_name, tags = reply.unpack()
            except Exception as err:
                if on_error is not None:
                    on_error(err)
                return
            on_done(str(uid), str(display_name), normalize_tags(tags))

        self._call("LookupNumber", params, _reply, on_error)

    def search_async(self, query: str, limit: int, on_done,
                     on_error=None) -> None:
        params = self._glib.Variant("(su)", (query, limit))

        def _reply(reply) -> None:
            try:
                (rows,) = reply.unpack()
            except Exception as err:
                if on_error is not None:
                    on_error(err)
                return
            on_done([(str(uid), str(name), str(number))
                     for uid, name, number in rows])

        self._call("Search", params, _reply, on_error)

    def get_async(self, uid: str, on_done, on_error=None) -> None:
        params = self._glib.Variant("(s)", (uid,))

        def _reply(reply) -> None:
            try:
                (vcard,) = reply.unpack()
            except Exception as err:
                if on_error is not None:
                    on_error(err)
                return
            on_done(str(vcard))

        self._call("Get", params, _reply, on_error)

    def create_async(self, vcard: str, on_done,
                     on_error=None) -> None:
        if self._bus is None:
            if on_error is not None:
                on_error(RuntimeError("contacts service is not present"))
            return
        params = self._glib.Variant("(s)", (vcard,))

        def _reply(reply) -> None:
            try:
                (uid,) = reply.unpack()
            except Exception as err:
                if on_error is not None:
                    on_error(err)
                return
            on_done(str(uid))

        self._call("Create", params, _reply, on_error)

    def list_async(self, since: int, on_done, on_error=None) -> None:
        params = self._glib.Variant("(t)", (since,))

        def _reply(reply) -> None:
            try:
                revision, changed, deleted = reply.unpack()
            except Exception as err:
                if on_error is not None:
                    on_error(err)
                return
            on_done(int(revision), [str(u) for u in changed],
                    [str(u) for u in deleted])

        self._call("List", params, _reply, on_error)

    def update_tags_async(self, uid: str, add, remove, on_done=None,
                          on_error=None) -> None:
        """Atomic single-transaction add/remove; unknown tags drop.

        The toggle paths (star/business/blocked switches) use this so a
        concurrent edit elsewhere never gets clobbered by a stale full
        set. With no bus, or when a call fails while the service owns no
        name, it folds the delta into the local cache and queues the
        delta for UpdateTags replay.
        """
        adding = normalize_tags(add)
        removing = [tag for tag in normalize_tags(remove)
                    if tag not in adding]
        if self._bus is None:
            self._apply_offline(uid, (local_tags(self._store, uid)
                                      | set(adding)) - set(removing),
                                adding, removing)
            if on_done is not None:
                on_done()
            return
        params = self._glib.Variant("(sasas)", (uid, adding, removing))

        def _reply(reply) -> None:
            try:
                reply.unpack()
            except Exception as err:
                if on_error is not None:
                    on_error(err)
                return
            if on_done is not None:
                on_done()

        def _failed(err) -> None:
            if self._present:
                if on_error is not None:
                    on_error(err)
                return
            self._apply_offline(uid, (local_tags(self._store, uid)
                                      | set(adding)) - set(removing),
                                adding, removing)
            if on_error is not None:
                on_error(err)
            elif on_done is not None:
                on_done()

        self._call("UpdateTags", params, _reply, _failed)

    def _apply_offline(self, uid: str, tags: set, add: list,
                       remove: list) -> None:
        """Fold a delta into the local cache and queue it for replay.

        Used when there is no bus, and when a bus call fails while the
        service is not present (idle-exited and not activatable): the
        edit is already visible locally and replays as an UpdateTags
        delta once the service answers again.
        """
        self._apply_tags_locally(uid, set(tags))
        try:
            self._store.record_pending_edit(
                uid, sorted(local_tags(self._store, uid)),
                int(time.time() * 1000), add=list(add),
                remove=list(remove))
        except Exception:
            pass

    def set_tags_async(self, uid: str, tags, on_done=None,
                       on_error=None) -> None:
        """Exact-set replace for explicit whole-set writes.

        Toggle paths must use ``update_tags_async`` instead: sending the
        full set there is a read-modify-write that can clobber a
        concurrent change.
        """
        exact = normalize_tags(tags)
        if self._bus is None:
            current = local_tags(self._store, uid)
            self._apply_offline(uid, set(exact),
                                [t for t in exact if t not in current],
                                [t for t in current if t not in exact])
            if on_done is not None:
                on_done()
            return
        params = self._glib.Variant("(sas)", (uid, exact))

        def _reply(reply) -> None:
            try:
                reply.unpack()
            except Exception as err:
                if on_error is not None:
                    on_error(err)
                return
            if on_done is not None:
                on_done()

        def _failed(err) -> None:
            if self._present:
                if on_error is not None:
                    on_error(err)
                return
            current = local_tags(self._store, uid)
            self._apply_offline(uid, set(exact),
                                [t for t in exact if t not in current],
                                [t for t in current if t not in exact])
            if on_error is not None:
                on_error(err)
            elif on_done is not None:
                on_done()

        self._call("SetTags", params, _reply, _failed)

    def _on_list_reply(self, revision: int, changed: list,
                       deleted: list) -> None:
        if not changed:
            self._apply_batch({}, deleted, revision)
            return
        finished: dict = {}
        failed = []

        def _maybe_done() -> None:
            if len(finished) + len(failed) != len(changed):
                return
            self._apply_batch(dict(finished), deleted,
                              None if failed else revision)

        for uid in changed:
            self.get_async(uid,
                           lambda vcard, u=uid: self._look_up_tags(
                               u, vcard, finished, _maybe_done, failed),
                           lambda _err, u=uid: (failed.append(u),
                                                _maybe_done()))

    def _look_up_tags(self, uid: str, vcard: str, finished: dict,
                      maybe_done, failed: list) -> None:
        name, numbers = parse_vcard(vcard, self._default_region)
        if not numbers:
            finished[uid] = (name, [], set())
            maybe_done()
            return

        def _tagged(_uid, _name, tags) -> None:
            finished[uid] = (name or _name, numbers, set(tags))
            maybe_done()

        self.lookup_async(numbers[0],
                          lambda u, n, t: _tagged(u, n, t),
                          lambda _err: (failed.append(uid), maybe_done()))

    def _apply_batch(self, fetched: dict, deleted: list,
                     revision: int | None) -> None:
        report = SyncReport()
        for uid in deleted:
            self._apply_deleted(uid)
            report.deleted += 1
        for uid, (display_name, numbers, service_tags) in fetched.items():
            self._apply_changed(uid, display_name, numbers, service_tags)
            report.applied += 1
        if revision is not None:
            self._revision = revision
        report.revision = self._revision
        if self._present and self._needs_reconcile:
            self._needs_reconcile = False
            try:
                deltas = self._store.pending_deltas()
            except Exception:
                deltas = []
            for uid, add, remove, _updated_at in deltas:
                self.update_tags_async(
                    uid, list(add), list(remove),
                    on_done=lambda u=uid: self._clear_queued(u),
                    on_error=lambda _err: None)
        self._finish_refresh(report)

    def _clear_queued(self, uid: str) -> None:
        try:
            self._store.clear_pending_edit(uid)
        except Exception:
            pass

    def _finish_refresh(self, report: SyncReport) -> None:
        self._refresh_inflight = False
        if self._refresh_queued:
            self._refresh_queued = False
            self.refresh()
        elif self._on_sync is not None:
            self._on_sync(report)

    def _on_list_error(self, _err) -> None:
        self._refresh_inflight = False
        self._refresh_queued = False

    def _apply_deleted(self, uid: str) -> None:
        was_blocked = "blocked" in local_tags(self._store, uid)
        numbers = self._store.mirror_numbers_for_key(uid)
        self._store.delete_mirror_for_key(uid)
        self._store.remove_tiers_for_key(uid)
        self._store.remove_star(uid)
        try:
            self._store.clear_pending_edit(uid)
        except Exception:
            pass
        if was_blocked:
            for number in numbers:
                if self._store.find_mirror(number) is None:
                    self._store.remove_blocked(number)

    def _apply_changed(self, uid: str, display_name: str,
                       numbers: list, service_tags: set) -> None:
        tags = set(service_tags)
        store = self._store
        store.delete_mirror_for_key(uid)
        for number in numbers:
            row = store.find_mirror(number)
            if row is not None and row.lookup_key != uid:
                store.delete_mirror_row(row.lookup_key, number)
        now_ms = int(time.time() * 1000)
        if numbers:
            store.upsert_mirror([MirrorRow(uid, number, display_name,
                                          "starred" in tags, None, False,
                                          now_ms) for number in numbers])
        if "starred" in tags:
            store.add_star(uid)
        else:
            store.remove_star(uid)
        if "business" in tags:
            store.upsert_tier(uid, BIZ_TIER)
        else:
            store.remove_tier(uid, BIZ_TIER)
        if "blocked" in tags:
            store.upsert_tier(uid, BLOCKED_GROUP)
            for number in numbers:
                store.add_blocked(number)
        else:
            store.remove_tier(uid, BLOCKED_GROUP)
            for number in numbers:
                if not _blocked_elsewhere(store, uid, number):
                    store.remove_blocked(number)

    def _apply_tags_locally(self, uid: str, tags: set) -> None:
        numbers = self._store.mirror_numbers_for_key(uid)
        store = self._store
        for number in numbers:
            row = store.find_mirror(number)
            if row is not None and row.lookup_key == uid \
                    and row.starred != ("starred" in tags):
                store.upsert_mirror([MirrorRow(
                    row.lookup_key, row.e164, row.display_name,
                    "starred" in tags, row.custom_ringtone,
                    row.send_to_voicemail, row.refreshed_at)])
        if "starred" in tags:
            store.add_star(uid)
        else:
            store.remove_star(uid)
        if "business" in tags:
            store.upsert_tier(uid, BIZ_TIER)
        else:
            store.remove_tier(uid, BIZ_TIER)
        if "blocked" in tags:
            store.upsert_tier(uid, BLOCKED_GROUP)
            for number in numbers:
                store.add_blocked(number)
        else:
            store.remove_tier(uid, BLOCKED_GROUP)
            for number in numbers:
                if not _blocked_elsewhere(store, uid, number):
                    store.remove_blocked(number)

    # ---- local edits (write through when the service is present) --------

    def set_star(self, uid: str, starred: bool) -> None:
        if starred:
            self._store.add_star(uid)
        else:
            self._store.remove_star(uid)
        for number in self._store.mirror_numbers_for_key(uid):
            row = self._store.find_mirror(number)
            if row is not None and row.lookup_key == uid \
                    and row.starred != starred:
                self._store.upsert_mirror([MirrorRow(
                    row.lookup_key, row.e164, row.display_name,
                    starred, row.custom_ringtone,
                    row.send_to_voicemail, row.refreshed_at)])
        self._push_delta(uid, "starred", starred)

    def set_business(self, uid: str, business: bool) -> None:
        if business:
            self._store.upsert_tier(uid, BIZ_TIER)
        else:
            self._store.remove_tier(uid, BIZ_TIER)
        self._push_delta(uid, "business", business)

    def set_blocked(self, uid: str, blocked: bool) -> list[str]:
        numbers = self._store.mirror_numbers_for_key(uid)
        if blocked:
            self._store.upsert_tier(uid, BLOCKED_GROUP)
            for number in numbers:
                self._store.add_blocked(number)
        else:
            self._store.remove_tier(uid, BLOCKED_GROUP)
            for number in numbers:
                if not _blocked_elsewhere(self._store, uid, number):
                    self._store.remove_blocked(number)
        self._push_delta(uid, "blocked", blocked)
        return numbers

    def _push_delta(self, uid: str, tag: str, on: bool) -> None:
        """Push one toggle as an atomic delta, never a full-set rewrite.

        The call goes out even when the service is not currently present:
        an idle-exited service auto-starts on the method call. The toggle
        is queued first, synchronously, so an offline edit is never lost
        while the bus round trip is in flight; a successful push clears
        the queue entry again.
        """
        add = [tag] if on else []
        remove = [] if on else [tag]
        if self._bus is None or not self._present:
            self._record_queued(uid, add, remove)
            if self._bus is None:
                return
            self.update_tags_async(
                uid, add, remove,
                on_done=lambda u=uid: self._clear_queued(u),
                on_error=lambda _err: None)
            return
        self.update_tags_async(uid, add, remove)

    def _record_queued(self, uid: str, add: list, remove: list) -> None:
        try:
            self._store.record_pending_edit(
                uid, sorted(local_tags(self._store, uid)),
                int(time.time() * 1000), add=list(add),
                remove=list(remove))
        except Exception:
            pass

    def _on_changed_signal(self, conn, sender, path, iface, signal,
                           params, user_data) -> None:
        try:
            params.unpack()
        except Exception:
            return
        self.refresh()