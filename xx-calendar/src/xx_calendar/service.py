"""Session-bus calendar service: ``io.piercingxx.Calendar`` (Revision 2 §2).

Headless entry: ``xx-calendar --service`` owns the bus name, serves
``/io/piercingxx/Calendar`` from the SQLite store, emits ``Changed`` on
writes, and exits after an injectable idle period (default 60 s).
``--idle-timeout`` (or ``XX_CALENDAR_IDLE_TIMEOUT``) shortens it for tests.

The GUI reads SQLite directly but every write goes through these methods,
so this process is the single writer and the only ``Changed`` emitter.
Method dispatch takes the exact Gio shapes (tuple ``GLib.Variant``
parameters, ``invocation.return_value`` / ``invocation.return_dbus_error``)
so the same code runs against the real bus and the pytest fakes.

Store work runs on one worker thread and replies post back with
``GLib.idle_add``, so handlers never block the main loop. The operation
methods (``list_calendars`` etc.) stay synchronous and headless-testable;
only ``handle_method_call`` detours through the worker.
"""

from __future__ import annotations

import os
import queue
import threading
import time

BUS_NAME = "io.piercingxx.Calendar"
OBJECT_PATH = "/io/piercingxx/Calendar"
INTERFACE = "io.piercingxx.Calendar1"

IDLE_EXIT_SECONDS = 60.0
IDLE_ENV_VAR = "XX_CALENDAR_IDLE_TIMEOUT"

BOOTSTRAP_CALENDAR_ID = "local"
BOOTSTRAP_CALENDAR_NAME = "On This Phone"

ERROR_NOT_FOUND = INTERFACE + ".NotFound"
ERROR_INVALID_ARGUMENT = INTERFACE + ".InvalidArgument"

SCOPE_THIS = "this"
SCOPE_FOLLOWING = "this-and-following"
SCOPE_ALL = "all"
VALID_SCOPES = (SCOPE_THIS, SCOPE_FOLLOWING, SCOPE_ALL)

MAX_IMPORT_BYTES = 10 * 1024 * 1024

INTROSPECTION_XML = """\
<node>
  <interface name="io.piercingxx.Calendar1">
    <method name="ListCalendars">
      <arg direction="out" type="aa{sv}" name="calendars"/>
    </method>
    <method name="ListInstances">
      <arg direction="in" type="x" name="start_utc_ms"/>
      <arg direction="in" type="x" name="end_utc_ms"/>
      <arg direction="in" type="as" name="calendar_ids"/>
      <arg direction="out" type="aa{sv}" name="instances"/>
    </method>
    <method name="Get">
      <arg direction="in" type="s" name="uid"/>
      <arg direction="out" type="s" name="ics"/>
    </method>
    <method name="Create">
      <arg direction="in" type="s" name="calendar_id"/>
      <arg direction="in" type="s" name="ics"/>
      <arg direction="out" type="s" name="uid"/>
    </method>
    <method name="Update">
      <arg direction="in" type="s" name="uid"/>
      <arg direction="in" type="s" name="ics"/>
      <arg direction="in" type="s" name="scope"/>
      <arg direction="in" type="x" name="recurrence_id"/>
    </method>
    <method name="Delete">
      <arg direction="in" type="s" name="uid"/>
      <arg direction="in" type="s" name="scope"/>
      <arg direction="in" type="x" name="recurrence_id"/>
    </method>
    <method name="List">
      <arg direction="in" type="t" name="since_revision"/>
      <arg direction="out" type="t" name="revision"/>
      <arg direction="out" type="as" name="changed"/>
      <arg direction="out" type="as" name="deleted"/>
    </method>
    <method name="Import">
      <arg direction="in" type="s" name="calendar_id"/>
      <arg direction="in" type="s" name="ics"/>
      <arg direction="out" type="u" name="created"/>
      <arg direction="out" type="u" name="updated"/>
      <arg direction="out" type="u" name="skipped"/>
    </method>
    <method name="Export">
      <arg direction="in" type="as" name="calendar_ids"/>
      <arg direction="out" type="s" name="ics"/>
    </method>
    <signal name="Changed">
      <arg type="t" name="revision"/>
      <arg type="as" name="uids"/>
    </signal>
  </interface>
</node>
"""


def emit_changed(connection, revision: int,
                 uids: list[str]) -> bool:
    """Emit the ``Changed`` signal on a live connection; fail open.

    ``None`` connections (tests, offline) report False instead of raising.
    """
    if connection is None:
        return False
    try:
        from gi.repository import GLib

        connection.emit_signal(
            None, OBJECT_PATH, INTERFACE, "Changed",
            GLib.Variant("(tas)", (int(revision), list(uids))))
    except Exception:
        return False
    return True


def _parse_scope(scope: str):
    from xx_calendar.models import RecurrenceScope

    if scope == SCOPE_THIS:
        return RecurrenceScope.THIS_INSTANCE
    if scope == SCOPE_FOLLOWING:
        return RecurrenceScope.THIS_AND_FOLLOWING
    if scope == SCOPE_ALL:
        return RecurrenceScope.ALL_EVENTS
    raise ValueError(
        f"unknown scope {scope!r}: want this, this-and-following, or all")


def _event_record_from_vevent(uid: str, recurrence_id: int, calendar_id: str,
                              vevent, ics_text: str):
    from xx_calendar import ics as ics_module
    from xx_calendar.store import EventRecord

    start = vevent.dtstart()
    if start is None:
        raise ValueError("VEVENT carries no DTSTART")
    dtstart_utc, all_day, tzid = start
    end = vevent.dtend()
    duration_raw = vevent.duration()
    if duration_raw:
        seconds = ics_module.parse_duration_seconds(duration_raw)
        if seconds is not None:
            dtend_utc = dtstart_utc + seconds * 1000
        elif end is not None:
            dtend_utc = end[0]
        else:
            dtend_utc = dtstart_utc
    elif end is not None:
        dtend_utc = end[0]
    else:
        dtend_utc = dtstart_utc + (86400000 if all_day else 0)
    tzid_end = end[2] if end is not None else ""
    return EventRecord(uid=uid, recurrence_id=int(recurrence_id),
                       calendar_id=calendar_id, ics=ics_text,
                       dtstart=int(dtstart_utc), dtend=int(dtend_utc),
                       tzid=tzid, tzid_end=tzid_end, all_day=bool(all_day),
                       rrule=vevent.rrule(), status=vevent.status())


def _set_uid(vevent, uid: str) -> None:
    from xx_calendar.ics import ICalProperty

    prop = vevent.first("UID")
    if prop is None:
        vevent.props.append(ICalProperty(name="UID", value=uid, modified=True))
    else:
        prop.set_value(uid)


def _set_recurrence_id(vevent, rid_ms: int, all_day: bool) -> None:
    from datetime import datetime, timezone

    from xx_calendar.ics import ICalProperty

    if all_day:
        value: str = datetime.fromtimestamp(rid_ms / 1000,
                                            timezone.utc).strftime("%Y%m%d")
        params = [("VALUE", ["DATE"])]
    else:
        value = datetime.fromtimestamp(rid_ms / 1000,
                                       timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        params = []
    prop = vevent.first("RECURRENCE-ID")
    if prop is None:
        vevent.props.append(ICalProperty(name="RECURRENCE-ID", params=params,
                                         value=value, modified=True))
    else:
        prop.params = list(params)
        prop.set_value(value)


def _rewrite_rrule(vevent, serialized: str) -> None:
    from xx_calendar.ics import ICalProperty

    prop = vevent.first("RRULE")
    if prop is None:
        vevent.props.append(ICalProperty(name="RRULE", value=serialized,
                                         modified=True))
    else:
        prop.set_value(serialized)


def _partition_date_props(vevent, split_ms: int) -> tuple[object, object]:
    """Split RDATE/EXDATE entries at *split_ms*; unparseable stays on parent.

    Returns ``(head, tail)`` VEVENTs carrying the parent-kept and
    continuation entries. Only membership moves, never spelling.
    """
    from copy import deepcopy

    from xx_calendar import ics as ics_module

    head = deepcopy(vevent)
    tail = deepcopy(vevent)
    for name in ("RDATE", "EXDATE"):
        kept: list[str] = []
        moved: list[str] = []
        for prop in vevent.all(name):
            head_tokens: list[str] = []
            tail_tokens: list[str] = []
            for token in prop.value.split(","):
                token = token.strip()
                if not token:
                    continue
                parsed = ics_module.parse_ical_datetime(token.split("/")[0],
                                                       prop)
                if parsed is None or parsed[0] < split_ms:
                    head_tokens.append(token)
                else:
                    tail_tokens.append(token)
            if head_tokens:
                kept.append((prop, head_tokens))
            if tail_tokens:
                moved.append((prop, tail_tokens))
        head.props = [p for p in head.props if p.name != name]
        tail.props = [p for p in tail.props if p.name != name]
        for prop, tokens in kept:
            clone = deepcopy(prop)
            clone.set_value(",".join(tokens))
            head.props.append(clone)
        for prop, tokens in moved:
            clone = deepcopy(prop)
            clone.set_value(",".join(tokens))
            tail.props.append(clone)
    return head, tail


def iter_vevent_blocks(text: str):
    """Yield raw VEVENT blocks one at a time with bounded memory.

    Only the input string plus one block is held: lines stream through
    ``io.StringIO`` instead of ``splitlines``, so a 2,000-event file
    imports in one go without a per-event list.
    """
    import io

    stream = io.StringIO(text)
    buf: list[str] | None = None
    for raw_line in stream:
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            buf = [line]
            continue
        if buf is None:
            continue
        buf.append(line)
        if upper == "END:VEVENT":
            yield "\r\n".join(buf)
            buf = None
    if buf:
        yield "\r\n".join(buf)


def read_bounded_text(path: object,
                      max_bytes: int = MAX_IMPORT_BYTES) -> str:
    """Read a picked ``.ics`` file in fixed chunks, aborting past the cap."""
    name = str(path)
    total = 0
    chunks: list[bytes] = []
    with open(name, "rb") as handle:
        while True:
            piece = handle.read(64 * 1024)
            if not piece:
                break
            total += len(piece)
            if total > max_bytes:
                raise ValueError("file too large")
            chunks.append(piece)
    raw = b"".join(chunks)
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def build_calendar_shell(vevent_texts: list[str]) -> str:
    """Wrap stored VEVENT blocks in a VCALENDAR shell via the codec."""
    from xx_calendar import ics as ics_module

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:" + ics_module.PRODID, "CALSCALE:GREGORIAN"]
    lines.extend(vevent_texts)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def export_store(store, calendar_ids=None) -> str:
    """Export stored rows losslessly; None/empty *calendar_ids* means all."""
    if calendar_ids is not None and len(list(calendar_ids)) == 0:
        calendar_ids = None
    rows = store.list_all_events(calendar_ids)
    return build_calendar_shell([row.ics for row in rows])


class CalendarService:
    """D-Bus method dispatch over a CalendarStore."""

    def __init__(self, store, idle_timeout: float = IDLE_EXIT_SECONDS,
                 start_worker: bool = True) -> None:
        self._store = store
        self.idle_timeout = float(idle_timeout)
        self._connection = None
        self.last_activity = time.monotonic()
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._ensure_bootstrap_calendar()
        if start_worker:
            self._worker = threading.Thread(target=self._worker_loop,
                                            name="calendar-dbus",
                                            daemon=True)
            self._worker.start()

    def _ensure_bootstrap_calendar(self) -> None:
        from xx_calendar.store import CalendarRecord, NotFoundError

        try:
            self._store.get_calendar(BOOTSTRAP_CALENDAR_ID)
            return
        except NotFoundError:
            pass
        self._store.create_calendar(CalendarRecord(
            id=BOOTSTRAP_CALENDAR_ID, display_name=BOOTSTRAP_CALENDAR_NAME))

    def stop(self) -> None:
        """Shut the worker down; in-process tests call this on teardown."""
        self._queue.put(None)
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.join(timeout=5)

    def _touch(self) -> None:
        self.last_activity = time.monotonic()

    def _emit_changed(self, revision: int, uids: list[str]) -> None:
        emit_changed(self._connection, revision, uids)

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_activity

    # ------------------------------------------------------------ operations

    def list_calendars(self) -> list[dict]:
        from gi.repository import GLib

        out = []
        for record in self._store.list_calendars():
            out.append({
                "id": GLib.Variant("s", record.id),
                "display_name": GLib.Variant("s", record.display_name),
                "account_name": GLib.Variant("s", record.account_name),
                "color": GLib.Variant("s", record.color),
                "is_visible": GLib.Variant("b", bool(record.is_visible)),
                "is_writable": GLib.Variant("b", bool(record.is_writable)),
                "sigil_tier": GLib.Variant("i", int(record.sigil_tier)),
            })
        return out

    def list_instances(self, start_utc_ms: int, end_utc_ms: int,
                       calendar_ids: list[str]) -> list[dict]:
        from gi.repository import GLib

        from xx_calendar import ics as ics_module
        from xx_calendar import recurrence as recurrence_module

        start_utc_ms, end_utc_ms = int(start_utc_ms), int(end_utc_ms)
        if end_utc_ms <= start_utc_ms:
            raise ValueError("window end must be after window start")
        wanted = list(calendar_ids) if calendar_ids is not None else []
        rows = self._store.events_in_window(start_utc_ms, end_utc_ms, wanted)
        parents = [r for r in rows if r.recurrence_id == 0]
        overrides: dict[str, list] = {}
        for row in rows:
            if row.recurrence_id != 0:
                overrides.setdefault(row.uid, []).append(row)
        out: list[dict] = []
        for parent in parents:
            events = ics_module.parse_events(parent.ics)
            if not events:
                continue
            event = events[0]
            override_events = []
            for row in overrides.get(parent.uid, []):
                parsed = ics_module.parse_events(row.ics)
                if parsed:
                    override_events.append(parsed[0])
            summaries = {parent.uid: event.summary() or ""}
            for override in override_events:
                rid = override.recurrence_id()
                if rid is not None and override.summary():
                    summaries[rid[0]] = override.summary()
            for instance in recurrence_module.expand_vevent(
                    event, start_utc_ms, end_utc_ms,
                    overrides=override_events):
                title = summaries.get(instance.recurrence_id or "",
                                      summaries[parent.uid])
                out.append({
                    "uid": GLib.Variant("s", parent.uid),
                    "calendar_id": GLib.Variant("s", parent.calendar_id),
                    "start": GLib.Variant("x", int(instance.start_utc)),
                    "end": GLib.Variant("x", int(instance.end_utc)),
                    "all_day": GLib.Variant("b", bool(instance.all_day)),
                    "recurrence_id": GLib.Variant(
                        "x", int(instance.recurrence_id or 0)),
                    "is_override": GLib.Variant("b",
                                                bool(instance.is_override)),
                    "status": GLib.Variant("s", instance.status or ""),
                    "title": GLib.Variant("s", title or ""),
                })
        out.sort(key=lambda item: (item["start"].get_int64(),
                                   item["uid"].get_string()))
        return out

    def get(self, uid: str) -> str:
        from xx_calendar.store import NotFoundError

        try:
            return self._store.get_event(uid).ics
        except NotFoundError as exc:
            raise LookupError(str(exc)) from exc

    def list_changes(self, since_revision: int
                     ) -> tuple[int, list[str], list[str]]:
        return self._store.list_changes(int(since_revision))

    def import_ics(self, calendar_id: str,
                   ics_text: str) -> tuple[int, int, int]:
        """Batch-import one ``.ics`` document in a single transaction.

        UID dedupe covers both the store and repeats inside the file;
        ``CANCELLED`` occurrences and rows without ``DTSTART`` are
        skipped. Exactly one ``Changed`` signal fires for the whole
        batch, and blocks stream one at a time so a 2,000-event file
        imports in one go with bounded memory.
        """
        from xx_calendar import ics as ics_module
        from xx_calendar.store import NotFoundError

        try:
            self._store.get_calendar(calendar_id)
        except NotFoundError as exc:
            raise LookupError(str(exc)) from exc
        if len(ics_text.encode("utf-8")) > MAX_IMPORT_BYTES:
            raise ValueError("file too large")
        existing = self._store.all_keys()
        seen: set[tuple[str, int]] = set()
        records: list = []
        created = updated = skipped = 0
        for block in iter_vevent_blocks(ics_text):
            parsed = ics_module.parse_events(block)
            if not parsed:
                skipped += 1
                continue
            vevent = parsed[0]
            uid = vevent.uid().strip() or ics_module.make_uid()
            if not vevent.uid().strip():
                _set_uid(vevent, uid)
            status = vevent.status().strip().upper()
            if status in ("CANCELLED", "CANCELED"):
                skipped += 1
                continue
            if vevent.dtstart() is None:
                skipped += 1
                continue
            rid = vevent.recurrence_id()
            rid_ms = int(rid[0]) if rid is not None else 0
            key = (uid, rid_ms)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            if vevent.first("DTSTAMP") is None:
                from xx_calendar.ics import ICalProperty
                from xx_calendar.store import utcnow

                stamp = utcnow().replace("-", "").replace(":", "")
                vevent.props.append(ICalProperty(name="DTSTAMP", value=stamp,
                                                 modified=True))
            try:
                record = _event_record_from_vevent(
                    uid, rid_ms, calendar_id, vevent, vevent.serialize())
            except ValueError:
                skipped += 1
                seen.discard(key)
                continue
            records.append(record)
            if key in existing:
                updated += 1
            else:
                created += 1
        if records:
            revision = self._store.bulk_put_events(records)
            self._emit_changed(revision,
                              sorted({record.uid for record in records}))
        return (int(created), int(updated), int(skipped))

    def export_ics(self, calendar_ids=None) -> str:
        """Export one calendar, several, or all (None/empty) losslessly."""
        if calendar_ids is not None:
            calendar_ids = list(calendar_ids)
            if not calendar_ids:
                calendar_ids = None
        return export_store(self._store, calendar_ids)

    def create(self, calendar_id: str, ics_text: str) -> str:
        from xx_calendar import ics as ics_module
        from xx_calendar.store import NotFoundError

        try:
            self._store.get_calendar(calendar_id)
        except NotFoundError as exc:
            raise LookupError(str(exc)) from exc
        events = ics_module.parse_events(ics_text)
        if not events:
            raise ValueError("no VEVENT block found")
        vevent = events[0]
        uid = vevent.uid() or ics_module.make_uid()
        _set_uid(vevent, uid)
        if vevent.first("DTSTAMP") is None:
            from xx_calendar.ics import ICalProperty

            from xx_calendar.store import utcnow

            stamp = utcnow().replace("-", "").replace(":", "")
            vevent.props.append(ICalProperty(name="DTSTAMP", value=stamp,
                                             modified=True))
        record = _event_record_from_vevent(uid, 0, calendar_id, vevent,
                                           vevent.serialize())
        revision = self._store.put_event(record)
        self._emit_changed(revision, [uid])
        return uid

    def _context_for(self, uid: str):
        from xx_calendar import ics as ics_module
        from xx_calendar.models import (
            RecurringEventContext,
            RRuleModel,
            RuleParsed,
        )
        from xx_calendar.store import NotFoundError

        try:
            record = self._store.get_event(uid)
        except NotFoundError as exc:
            raise LookupError(str(exc)) from exc
        events = ics_module.parse_events(record.ics)
        if not events:
            raise ValueError(f"stored event {uid!r} has no VEVENT block")
        vevent = events[0]
        start = vevent.dtstart()
        if start is None:
            raise ValueError(f"stored event {uid!r} carries no DTSTART")
        dtstart_utc, all_day, _tzid = start
        rule = None
        refused_raw = None
        raw_rule = vevent.rrule()
        if raw_rule:
            parsed = RRuleModel.parse(raw_rule)
            if isinstance(parsed, RuleParsed):
                rule = parsed.rule
            else:
                refused_raw = raw_rule
        context = RecurringEventContext(parent_uid=uid, rule=rule,
                                        start_millis=int(dtstart_utc),
                                        all_day=bool(all_day))
        return record, vevent, context, refused_raw

    def update(self, uid: str, ics_text: str, scope: str,
               recurrence_id: int) -> None:
        from xx_calendar import ics as ics_module
        from xx_calendar.models import (
            InsertExceptionRow,
            InstanceRef,
            Refusal,
            SplitParent,
            UpdateParentRow,
        )
        from xx_calendar.models import ScopeResolver

        wanted = _parse_scope(scope)
        record, vevent, context, refused_raw = self._context_for(uid)
        if refused_raw is not None and wanted != _parse_scope(SCOPE_ALL):
            raise ValueError(f"unsupported recurrence rule: {refused_raw}")
        events = ics_module.parse_events(ics_text)
        if not events:
            raise ValueError("no VEVENT block found")
        incoming = events[0]
        resolution = ScopeResolver.resolve_edit(
            context, wanted, InstanceRef(uid, int(recurrence_id)),
            self._edits_from_vevent(incoming))
        if isinstance(resolution, Refusal):
            raise ValueError(resolution.reason)
        if isinstance(resolution, UpdateParentRow):
            _set_uid(incoming, uid)
            fresh = _event_record_from_vevent(
                uid, 0, record.calendar_id, incoming, incoming.serialize())
            revision = self._store.put_event(fresh)
            self._emit_changed(revision, [uid])
        elif isinstance(resolution, InsertExceptionRow):
            self._insert_override(uid, record, vevent, incoming,
                                  resolution.original_instance_time_millis)
        elif isinstance(resolution, SplitParent):
            self._split_series(uid, record, vevent, incoming,
                               resolution, int(recurrence_id))

    def _edits_from_vevent(self, vevent):
        from xx_calendar.models import EventFieldEdits

        start = vevent.dtstart()
        end = vevent.dtend()
        return EventFieldEdits(
            title=vevent.summary(), location=vevent.location(),
            description=vevent.description(),
            start_millis=int(start[0]) if start else None,
            end_millis=int(end[0]) if end else None,
            all_day=bool(start[1]) if start else None)

    def _insert_override(self, uid: str, record, parent_vevent, incoming,
                         original_ms: int) -> None:
        start = incoming.dtstart()
        all_day = parent_vevent.dtstart()[1] \
            if parent_vevent.dtstart() else False
        if start is None:
            from datetime import datetime, timezone

            from xx_calendar.ics import ICalProperty

            moment = datetime.fromtimestamp(original_ms / 1000,
                                            timezone.utc)
            if all_day:
                incoming.props.append(ICalProperty(
                    name="DTSTART", params=[("VALUE", ["DATE"])],
                    value=moment.strftime("%Y%m%d"), modified=True))
            else:
                incoming.props.append(ICalProperty(
                    name="DTSTART",
                    value=moment.strftime("%Y%m%dT%H%M%SZ"), modified=True))
        _set_uid(incoming, uid)
        _set_recurrence_id(incoming, original_ms, bool(all_day))
        fresh = _event_record_from_vevent(uid, original_ms,
                                          record.calendar_id, incoming,
                                          incoming.serialize())
        revision = self._store.put_event(fresh)
        self._emit_changed(revision, [uid])

    def _split_series(self, uid: str, record, parent_vevent, incoming,
                      resolution, split_ms: int) -> str:
        from xx_calendar import ics as ics_module
        from xx_calendar.models import EndUntil, RRuleModel
        from xx_calendar.models import RuleParsed

        until = EndUntil(split_ms - 1, date_only=bool(
            parent_vevent.dtstart()[1] if parent_vevent.dtstart() else False))
        parsed = RRuleModel.parse(parent_vevent.rrule())
        if not isinstance(parsed, RuleParsed):
            raise ValueError("cannot split the series: "
                             "its recurrence rule is not recognised")
        from dataclasses import replace

        truncated = replace(parsed.rule, end=until)
        head, tail = _partition_date_props(parent_vevent, split_ms)
        _rewrite_rrule(head, truncated.serialize())
        head_record = _event_record_from_vevent(
            uid, 0, record.calendar_id, head, head.serialize())
        self._store.put_event(head_record)
        new_uid = ics_module.make_uid()
        incoming_start = incoming.dtstart()
        parent_start = parent_vevent.dtstart()
        if incoming_start is None and parent_start is not None:
            duration = record.dtend - record.dtstart
            from datetime import datetime, timezone

            from xx_calendar.ics import ICalProperty

            moment = datetime.fromtimestamp(split_ms / 1000, timezone.utc)
            tzid = parent_start[2]
            if parent_start[1]:
                incoming.props.append(ICalProperty(
                    name="DTSTART", params=[("VALUE", ["DATE"])],
                    value=moment.strftime("%Y%m%d"), modified=True))
            elif tzid:
                from zoneinfo import ZoneInfo

                try:
                    zone = ZoneInfo(tzid)
                except Exception:
                    zone = timezone.utc
                local = moment.astimezone(zone)
                incoming.props.append(ICalProperty(
                    name="DTSTART", params=[("TZID", [tzid])],
                    value=local.strftime("%Y%m%dT%H%M%S"), modified=True))
            else:
                incoming.props.append(ICalProperty(
                    name="DTSTART",
                    value=moment.strftime("%Y%m%dT%H%M%SZ"), modified=True))
            if duration and incoming.dtend() is None \
                    and not incoming.duration():
                incoming.props.append(ICalProperty(
                    name="DTEND",
                    value=datetime.fromtimestamp(
                        (split_ms + duration) / 1000,
                        timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                    modified=True))
        _set_uid(incoming, new_uid)
        if not incoming.rrule():
            _rewrite_rrule(incoming, parsed.rule.serialize())
        for prop in tail.all("RDATE") + tail.all("EXDATE"):
            incoming.props.append(prop)
        fresh = _event_record_from_vevent(new_uid, 0, record.calendar_id,
                                          incoming, incoming.serialize())
        self._store.put_event(fresh)
        for override in self._store.overrides_for(uid):
            if override.recurrence_id >= split_ms:
                parsed_override = ics_module.parse_events(override.ics)
                if parsed_override:
                    _set_uid(parsed_override[0], new_uid)
                    moved = parsed_override[0]
                    moved_ics = moved.serialize()
                else:
                    moved, moved_ics = None, override.ics
                if moved is None:
                    from xx_calendar.store import EventRecord

                    moved_record = EventRecord(
                        uid=new_uid,
                        recurrence_id=override.recurrence_id,
                        calendar_id=override.calendar_id, ics=moved_ics,
                        dtstart=override.dtstart, dtend=override.dtend,
                        tzid=override.tzid, tzid_end=override.tzid_end,
                        all_day=override.all_day, rrule=override.rrule,
                        status=override.status)
                else:
                    moved_record = _event_record_from_vevent(
                        new_uid, override.recurrence_id,
                        override.calendar_id, moved, moved_ics)
                self._store.put_event(moved_record)
                self._store.delete_event(uid, override.recurrence_id)
        revision = self._store.get_revision()
        self._emit_changed(revision, [uid, new_uid])
        return new_uid

    def delete(self, uid: str, scope: str, recurrence_id: int) -> None:
        from xx_calendar.models import (
            DeleteInstanceUri,
            DeleteParentRow,
            InstanceRef,
            Refusal,
            SetUntil,
        )
        from xx_calendar.models import ScopeResolver

        wanted = _parse_scope(scope)
        _record, vevent, context, refused_raw = self._context_for(uid)
        if refused_raw is not None and wanted != _parse_scope(SCOPE_ALL):
            raise ValueError(f"unsupported recurrence rule: {refused_raw}")
        resolution = ScopeResolver.resolve_delete(
            context, wanted, InstanceRef(uid, int(recurrence_id)))
        if isinstance(resolution, Refusal):
            raise ValueError(resolution.reason)
        if isinstance(resolution, DeleteParentRow):
            revision = self._store.delete_series(uid)
            self._emit_changed(revision, [uid])
        elif isinstance(resolution, DeleteInstanceUri):
            self._cancel_instance(uid, int(recurrence_id))
        elif isinstance(resolution, SetUntil):
            self._truncate_series(uid, resolution.until)

    def _cancel_instance(self, uid: str, original_ms: int) -> None:
        from xx_calendar import ics as ics_module
        from xx_calendar.store import NotFoundError

        try:
            record = self._store.get_event(uid)
        except NotFoundError as exc:
            raise LookupError(str(exc)) from exc
        events = ics_module.parse_events(record.ics)
        all_day = bool(events and events[0].dtstart()
                       and events[0].dtstart()[1])
        cancelled = ics_module.VEvent()
        _set_uid(cancelled, uid)
        _set_recurrence_id(cancelled, original_ms, all_day)
        from datetime import datetime, timezone

        from xx_calendar.ics import ICalProperty

        moment = datetime.fromtimestamp(original_ms / 1000, timezone.utc)
        if all_day:
            cancelled.props.append(ICalProperty(
                name="DTSTART", params=[("VALUE", ["DATE"])],
                value=moment.strftime("%Y%m%d"), modified=True))
        else:
            cancelled.props.append(ICalProperty(
                name="DTSTART", value=moment.strftime("%Y%m%dT%H%M%SZ"),
                modified=True))
        cancelled.props.append(ICalProperty(name="STATUS", value="CANCELLED",
                                            modified=True))
        cancelled.props.append(ICalProperty(
            name="DTSTAMP",
            value=moment.strftime("%Y%m%dT%H%M%SZ"), modified=True))
        fresh = _event_record_from_vevent(uid, original_ms,
                                          record.calendar_id, cancelled,
                                          cancelled.serialize())
        revision = self._store.put_event(fresh)
        self._emit_changed(revision, [uid])

    def _truncate_series(self, uid: str, until) -> None:
        from xx_calendar import ics as ics_module
        from xx_calendar.models import RRuleModel
        from xx_calendar.models import RuleParsed
        from xx_calendar.store import NotFoundError

        try:
            record = self._store.get_event(uid)
        except NotFoundError as exc:
            raise LookupError(str(exc)) from exc
        events = ics_module.parse_events(record.ics)
        if not events:
            raise ValueError(f"stored event {uid!r} has no VEVENT block")
        vevent = events[0]
        parsed = RRuleModel.parse(vevent.rrule())
        if not isinstance(parsed, RuleParsed):
            raise ValueError("cannot shorten the series: "
                             "its recurrence rule is not recognised")
        from dataclasses import replace

        _rewrite_rrule(vevent, replace(parsed.rule, end=until).serialize())
        head, _tail = _partition_date_props(vevent, until.until_millis_utc + 1)
        fresh = _event_record_from_vevent(uid, 0, record.calendar_id, head,
                                          head.serialize())
        self._store.put_event(fresh)
        for override in self._store.overrides_for(uid):
            if override.recurrence_id > until.until_millis_utc:
                self._store.delete_event(uid, override.recurrence_id)
        revision = self._store.get_revision()
        self._emit_changed(revision, [uid])

    # ------------------------------------------------------- Gio dispatch

    def _dispatch(self, method_name: str, args: tuple):
        from gi.repository import GLib

        if method_name == "ListCalendars":
            return GLib.Variant("(aa{sv})", (self.list_calendars(),))
        if method_name == "ListInstances":
            return GLib.Variant("(aa{sv})",
                                (self.list_instances(*args),))
        if method_name == "Get":
            return GLib.Variant("(s)", (self.get(*args),))
        if method_name == "Create":
            return GLib.Variant("(s)", (self.create(*args),))
        if method_name == "Update":
            self.update(*args)
            return GLib.Variant("()", ())
        if method_name == "Delete":
            self.delete(*args)
            return GLib.Variant("()", ())
        if method_name == "List":
            revision, changed, deleted = self.list_changes(*args)
            return GLib.Variant("(tasas)",
                                (revision, changed, deleted))
        if method_name == "Import":
            created, updated, skipped = self.import_ics(*args)
            return GLib.Variant("(uuu)",
                                (int(created), int(updated), int(skipped)))
        if method_name == "Export":
            return GLib.Variant("(s)", (self.export_ics(*args),))
        raise ValueError(f"unknown method: {method_name}")

    @staticmethod
    def _reply_ok(invocation, variant) -> bool:
        invocation.return_value(variant)
        return False

    @staticmethod
    def _reply_error(invocation, name: str, message: str) -> bool:
        invocation.return_dbus_error(name, message)
        return False

    def handle_method_call(self, connection, sender, object_path,
                           interface_name, method_name, parameters,
                           invocation) -> None:
        """Gio method-call closure: queue the work, reply from idle."""
        self._touch()
        try:
            args = parameters.unpack() if parameters is not None else ()
        except Exception as exc:
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT, f"cannot unpack args: {exc}")
            return
        self._queue.put((method_name, tuple(args), invocation))

    def _worker_loop(self) -> None:
        from gi.repository import GLib

        while True:
            task = self._queue.get()
            try:
                if task is None:
                    return
                method_name, args, invocation = task
                try:
                    variant = self._dispatch(method_name, args)
                except LookupError as exc:
                    GLib.idle_add(self._reply_error, invocation,
                                  ERROR_NOT_FOUND, str(exc))
                except (ValueError, TypeError) as exc:
                    GLib.idle_add(self._reply_error, invocation,
                                  ERROR_INVALID_ARGUMENT, str(exc))
                except Exception as exc:  # never strand a method call
                    GLib.idle_add(self._reply_error, invocation,
                                  ERROR_INVALID_ARGUMENT, f"failed: {exc}")
                else:
                    GLib.idle_add(self._reply_ok, invocation, variant)
            finally:
                self._queue.task_done()

    def register_on(self, connection) -> int:
        """Register the object on a live Gio.DBusConnection; keep it."""
        from gi.repository import Gio

        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        iface = node.interfaces[0]
        registration_id = connection.register_object(
            OBJECT_PATH, iface, self.handle_method_call)
        self._connection = connection
        return registration_id


def resolve_idle_timeout(explicit: float | None = None) -> float:
    """CLI value wins, then the env var, then the 60 s default."""
    if explicit is not None:
        return float(explicit)
    raw = os.environ.get(IDLE_ENV_VAR, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return IDLE_EXIT_SECONDS


def build_service(db_path: str,
                  idle_timeout: float = IDLE_EXIT_SECONDS) -> CalendarService:
    from xx_calendar.store import CalendarStore

    return CalendarService(CalendarStore(db_path), idle_timeout)


def run_service(db_path: str,
                idle_timeout: float = IDLE_EXIT_SECONDS) -> int:
    """Own the bus name and serve until idle. Headless: no GTK."""
    from gi.repository import Gio, GLib

    service = build_service(db_path, idle_timeout)
    loop = GLib.MainLoop()
    state: dict[str, object] = {}

    def on_bus_acquired(connection: Gio.DBusConnection, _name: str) -> None:
        service.register_on(connection)

    def on_name_acquired(_connection: Gio.DBusConnection, _name: str) -> None:
        state["owned"] = True

    def on_name_lost(_connection: Gio.DBusConnection | None,
                     _name: str) -> None:
        loop.quit()

    def on_idle_check() -> bool:
        if service.idle_seconds() >= service.idle_timeout:
            loop.quit()
            return False
        return True

    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
                     Gio.BusNameOwnerFlags.NONE,
                     on_bus_acquired, on_name_acquired, on_name_lost)
    GLib.timeout_add_seconds(1, on_idle_check)
    try:
        loop.run()
    finally:
        service.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="xx-calendar --service")
    parser.add_argument("--service", action="store_true")
    parser.add_argument("--db", default=None)
    parser.add_argument("--idle-timeout", type=float, default=None)
    args = parser.parse_args(argv if argv is not None else [])
    home = os.path.expanduser("~")
    from xx_calendar.store import default_db_path

    db_path = args.db or default_db_path(home)
    return run_service(db_path, resolve_idle_timeout(args.idle_timeout))
