"""Lossless iCalendar codec (RFC 5545 subset, Revision 2 §1).

Every VEVENT keeps its original properties and parameters, including
unknown X- props: untouched properties re-emit byte-identical (after CRLF
normalization), VTIMEZONE blocks and non-VEVENT components pass through
verbatim and are never parsed. The store indexes the modeled fields (UID,
DTSTART/DTEND, TZID, RRULE, STATUS, VALARM, attendees) while ``ics`` holds
the raw VEVENT text, the same own-store + lossless-codec split xx-contacts
uses for vCard.

Line endings normalize to CRLF on parse, so byte-identical means identical
for the CRLF files every real exporter writes. Pure Python, no GTK/D-Bus.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FOLD_OCTETS = 75
PRODID = "-//PiercingXX//XX-Calendar//EN"


def unfold_spans(text: str) -> list[tuple[str, str]]:
    """Unfold physical lines into ``(logical, raw_block)`` pairs.

    A line starting with a space or tab continues the previous line (the
    one folding character is dropped). Blank lines are skipped. The raw
    block is the original physical lines joined with CRLF, kept so
    untouched properties serialize byte-identical.
    """
    spans: list[tuple[str, str]] = []
    current: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and current:
            current.append(raw)
        elif raw == "":
            continue
        else:
            if current:
                spans.append((_join_folded(current), "\r\n".join(current)))
            current = [raw]
    if current:
        spans.append((_join_folded(current), "\r\n".join(current)))
    return spans


def _join_folded(physical: list[str]) -> str:
    first, *rest = physical
    return first + "".join(line[1:] for line in rest)


def unfold_lines(text: str) -> list[str]:
    """Unfold physical lines into logical lines (fold marker dropped)."""
    return [logical for logical, _ in unfold_spans(text)]


def fold_line(line: str, limit: int = FOLD_OCTETS) -> str:
    """Fold one logical line to CRLF + space continuations (75 octets)."""
    encoded = line.encode("utf-8")
    if len(encoded) <= limit:
        return line
    chunks: list[str] = []
    current = bytearray()
    first_budget = limit
    for char in line:
        raw = char.encode("utf-8")
        budget = first_budget if not chunks else limit - 1
        if len(current) + len(raw) > budget and current:
            chunks.append(bytes(current).decode("utf-8"))
            current = bytearray()
        current += raw
    if current:
        chunks.append(bytes(current).decode("utf-8"))
    return "\r\n".join([chunks[0]] + [" " + chunk for chunk in chunks[1:]])


def escape_text(value: str) -> str:
    return (value.replace("\\", "\\\\").replace("\n", "\\n")
                 .replace(";", "\\;").replace(",", "\\,"))


def unescape_text(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _find_value_colon(line: str) -> int:
    in_quote = False
    for i, char in enumerate(line):
        if char == '"':
            in_quote = not in_quote
        elif char == ":" and not in_quote:
            return i
    return -1


def _split_params(segment: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_quote = False
    for char in segment:
        if char == '"':
            in_quote = not in_quote
            current.append(char)
        elif char == ";" and not in_quote:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def _split_param_values(raw: str) -> list[str]:
    values: list[str] = []
    current: list[str] = []
    in_quote = False
    for char in raw:
        if char == '"':
            in_quote = not in_quote
        elif char == "," and not in_quote:
            values.append("".join(current))
            current = []
        else:
            current.append(char)
    values.append("".join(current))
    return [v for v in values if v != ""]


@dataclass
class ICalProperty:
    """One content line: parsed head plus the verbatim source block."""
    name: str = ""
    params: list[tuple[str, list[str]]] = field(default_factory=list)
    value: str = ""
    raw: str = ""
    modified: bool = False

    def param(self, key: str) -> list[str]:
        wanted = key.upper()
        found: list[str] = []
        for pname, values in self.params:
            if pname.upper() == wanted:
                found.extend(values)
        return found

    def set_value(self, value: str) -> None:
        self.value = value
        self.modified = True

    def serialize(self) -> str:
        if not self.modified and self.raw:
            return self.raw
        head = self.name.upper()
        for key, values in self.params:
            head += ";" + key.upper() + "=" + ",".join(values)
        return fold_line(f"{head}:{self.value}")


def parse_property(line: str, raw: str = "") -> ICalProperty | None:
    colon = _find_value_colon(line)
    if colon <= 0:
        return None
    head = line[:colon]
    value = line[colon + 1:]
    segments = _split_params(head)
    name = segments[0].strip().upper()
    if not name or " " in name:
        return None
    params: list[tuple[str, list[str]]] = []
    for token in segments[1:]:
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            key, _, raw_values = token.partition("=")
            params.append((key.strip().upper(),
                           _split_param_values(raw_values.strip())))
        else:
            params.append(("TYPE", [token.upper()]))
    return ICalProperty(name=name, params=params, value=value,
                        raw=raw or line)


def _props_from_spans(spans: list[tuple[str, str]]) -> list[ICalProperty]:
    props: list[ICalProperty] = []
    for logical, raw in spans:
        prop = parse_property(logical, raw)
        if prop is not None:
            props.append(prop)
    return props


# ------------------------------------------------------------ typed values


def _zone_or_none(tzid: str):
    try:
        return ZoneInfo(tzid)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def parse_ical_datetime(value: str, params: ICalProperty | dict | None = None,
                        default_zone: str = "UTC"
                        ) -> tuple[int, bool, str] | None:
    """Parse a DATE or DATE-TIME value into ``(millis, all_day, tzid)``.

    DATE (``VALUE=DATE`` or the 8-digit form) reads as UTC midnight. A
    ``Z``-suffixed DATE-TIME reads as UTC. A TZID DATE-TIME resolves in
    that zone (unknown ids fall back to *default_zone*, fail-open for
    reading). Floating times resolve in *default_zone* (``"UTC"``) with an
    empty TZID. Returns None when the value is not a date at all.
    """
    text = (value or "").strip()
    if not text:
        return None
    if isinstance(params, ICalProperty):
        value_param = params.param("VALUE")
        tzid_param = params.param("TZID")
    elif isinstance(params, dict):
        value_param = [str(params.get("VALUE", ""))] if params.get("VALUE") \
            else []
        tzid_param = [str(params.get("TZID", ""))] if params.get("TZID") \
            else []
    else:
        value_param, tzid_param = [], []
    is_date = any(v.upper() == "DATE" for v in value_param)
    if is_date or (len(text) == 8 and text.isdigit()):
        try:
            moment = datetime.strptime(text[:8], "%Y%m%d").replace(
                tzinfo=timezone.utc)
        except ValueError:
            return None
        return int(moment.timestamp() * 1000), True, ""
    normalized = text.replace(" ", "T")
    is_utc = normalized.upper().endswith("Z")
    body = normalized[:-1] if is_utc else normalized
    moment = None
    for pattern in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            moment = datetime.strptime(body, pattern)
            break
        except ValueError:
            continue
    if moment is None:
        return None
    if is_utc:
        return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000), \
            False, ""
    tzid = tzid_param[0] if tzid_param else ""
    zone = _zone_or_none(tzid) if tzid else None
    if zone is None:
        zone = _zone_or_none(default_zone) or timezone.utc
        tzid = "" if not tzid_param else tzid
        if not tzid_param:
            tzid = ""
    aware = moment.replace(tzinfo=zone)
    return int(aware.timestamp() * 1000), False, tzid


def format_ical_datetime(millis: int, tzid: str = "",
                         all_day: bool = False) -> tuple[str, str]:
    """Format UTC millis as an iCalendar value, returning ``(head, value)``.

    All-day renders ``DATE``; UTC renders ``Z``; TZID renders local time.
    """
    if all_day:
        text = (datetime.fromtimestamp(millis / 1000, timezone.utc)
                .strftime("%Y%m%d"))
        return "DTSTART;VALUE=DATE", text
    zone = _zone_or_none(tzid) if tzid else None
    if zone is None:
        text = (datetime.fromtimestamp(millis / 1000, timezone.utc)
                .strftime("%Y%m%dT%H%M%SZ"))
        return "DTSTART", text
    text = (datetime.fromtimestamp(millis / 1000, timezone.utc)
            .astimezone(zone).strftime("%Y%m%dT%H%M%S"))
    return f"DTSTART;TZID={tzid}", text


_DURATION_RE = re.compile(
    r"^([+-]?)P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_duration_seconds(raw: str) -> int | None:
    match = _DURATION_RE.match((raw or "").strip().upper())
    if match is None:
        return None
    sign = -1 if match.group(1) == "-" else 1

    def part(index: int) -> int:
        return int(match.group(index)) if match.group(index) else 0

    return sign * (part(2) * 604800 + part(3) * 86400 + part(4) * 3600
                   + part(5) * 60 + part(6))


def parse_trigger_minutes(raw: str) -> int | None:
    """Parse a VALARM TRIGGER into reminder minutes (positive = before).

    Absolute date-time triggers yield None. Sub-minute remainders round
    toward sooner, matching the Android IcsCodec contract.
    """
    seconds = parse_duration_seconds(raw)
    if seconds is None:
        return None
    clamped = max(-1262304000, min(1262304000, seconds))
    magnitude = (abs(clamped) + 59) // 60
    return magnitude if clamped < 0 else -magnitude


def format_trigger_minutes(minutes: int) -> str:
    if minutes > 0:
        return f"-PT{minutes:d}M"
    if minutes == 0:
        return "PT0S"
    return f"PT{-minutes:d}M"


def make_uid() -> str:
    return f"{uuid.uuid4()}@xx-calendar"


# ---------------------------------------------------------------- alarms


@dataclass
class VAlarm:
    props: list[ICalProperty] = field(default_factory=list)

    def first(self, name: str) -> ICalProperty | None:
        wanted = name.upper()
        for prop in self.props:
            if prop.name == wanted:
                return prop
        return None

    def action(self) -> str:
        prop = self.first("ACTION")
        return prop.value.strip().upper() if prop else ""

    def trigger_raw(self) -> str:
        prop = self.first("TRIGGER")
        return prop.value.strip() if prop else ""

    def trigger_minutes(self) -> int | None:
        return parse_trigger_minutes(self.trigger_raw())

    def serialize(self) -> str:
        lines = ["BEGIN:VALARM"]
        lines.extend(prop.serialize() for prop in self.props)
        lines.append("END:VALARM")
        return "\r\n".join(lines)


def alarm_for_minutes(minutes: int) -> VAlarm:
    return VAlarm(props=[
        ICalProperty(name="ACTION", value="DISPLAY", modified=True),
        ICalProperty(name="DESCRIPTION", value="reminder", modified=True),
        ICalProperty(name="TRIGGER", value=format_trigger_minutes(minutes),
                     modified=True),
    ])


@dataclass
class Attendee:
    email: str = ""
    cn: str = ""
    role: str = ""
    partstat: str = ""
    cutype: str = ""


def _mailto_email(value: str) -> str:
    text = (value or "").strip()
    if text[:7].upper() == "MAILTO:":
        return text[7:]
    return text


# ---------------------------------------------------------------- events


@dataclass
class VEvent:
    props: list[ICalProperty] = field(default_factory=list)
    alarms: list[VAlarm] = field(default_factory=list)

    def all(self, name: str) -> list[ICalProperty]:
        wanted = name.upper()
        return [p for p in self.props if p.name == wanted]

    def first(self, name: str) -> ICalProperty | None:
        found = self.all(name)
        return found[0] if found else None

    def uid(self) -> str:
        prop = self.first("UID")
        return prop.value.strip() if prop else ""

    def _text(self, name: str) -> str | None:
        prop = self.first(name)
        if prop is None:
            return None
        return unescape_text(prop.value)

    def summary(self) -> str | None:
        return self._text("SUMMARY")

    def location(self) -> str | None:
        return self._text("LOCATION")

    def description(self) -> str | None:
        return self._text("DESCRIPTION")

    def status(self) -> str:
        prop = self.first("STATUS")
        return prop.value.strip().upper() if prop else ""

    def transp(self) -> str:
        prop = self.first("TRANSP")
        return prop.value.strip().upper() if prop else ""

    def rrule(self) -> str:
        prop = self.first("RRULE")
        return prop.value.strip() if prop else ""

    def rdates(self) -> list[str]:
        return [p.value.strip() for p in self.all("RDATE")
                if p.value.strip()]

    def exdates(self) -> list[str]:
        return [p.value.strip() for p in self.all("EXDATE")
                if p.value.strip()]

    def duration(self) -> str:
        prop = self.first("DURATION")
        return prop.value.strip().upper() if prop else ""

    def sequence(self) -> int:
        prop = self.first("SEQUENCE")
        if prop is None:
            return 0
        try:
            return int(prop.value.strip())
        except ValueError:
            return 0

    def dtstart(self, default_zone: str = "UTC"):
        prop = self.first("DTSTART")
        if prop is None:
            return None
        return parse_ical_datetime(prop.value, prop, default_zone)

    def dtend(self, default_zone: str = "UTC"):
        prop = self.first("DTEND")
        if prop is None:
            return None
        return parse_ical_datetime(prop.value, prop, default_zone)

    def recurrence_id(self, default_zone: str = "UTC"):
        prop = self.first("RECURRENCE-ID")
        if prop is None:
            return None
        return parse_ical_datetime(prop.value, prop, default_zone)

    def organizer(self) -> Attendee | None:
        prop = self.first("ORGANIZER")
        if prop is None:
            return None
        cn = prop.param("CN")
        return Attendee(email=_mailto_email(prop.value),
                        cn=unescape_text(cn[0]) if cn else "")

    def attendees(self) -> list[Attendee]:
        out: list[Attendee] = []
        for prop in self.all("ATTENDEE"):
            cn = prop.param("CN")
            role = prop.param("ROLE")
            partstat = prop.param("PARTSTAT")
            cutype = prop.param("CUTYPE")
            out.append(Attendee(
                email=_mailto_email(prop.value),
                cn=unescape_text(cn[0]) if cn else "",
                role=role[0].upper() if role else "",
                partstat=partstat[0].upper() if partstat else "",
                cutype=cutype[0].upper() if cutype else ""))
        return out

    def reminder_minutes(self) -> list[int]:
        minutes: list[int] = []
        for alarm in self.alarms:
            if alarm.action() != "DISPLAY":
                continue
            parsed = alarm.trigger_minutes()
            if parsed is not None:
                minutes.append(parsed)
        return minutes

    def set_text(self, name: str, text: str) -> None:
        prop = self.first(name)
        if prop is None:
            self.props.append(ICalProperty(name=name.upper(),
                                           value=escape_text(text),
                                           modified=True))
        else:
            prop.set_value(escape_text(text))

    def serialize(self) -> str:
        lines = ["BEGIN:VEVENT"]
        lines.extend(prop.serialize() for prop in self.props)
        lines.extend(alarm.serialize() for alarm in self.alarms)
        lines.append("END:VEVENT")
        return "\r\n".join(lines)


def _alarm_from_spans(spans: list[tuple[str, str]]) -> VAlarm:
    return VAlarm(props=_props_from_spans(spans))


@dataclass
class VCalendar:
    top_props: list[ICalProperty] = field(default_factory=list)
    blocks: list[tuple[str, object]] = field(default_factory=list)

    @property
    def events(self) -> list[VEvent]:
        return [item for kind, item in self.blocks if kind == "event"]

    @property
    def timezones(self) -> list[str]:
        return [item for kind, item in self.blocks if kind == "timezone"]

    @property
    def other_blocks(self) -> list[str]:
        return [item for kind, item in self.blocks if kind == "other"]

    def serialize(self) -> str:
        lines = ["BEGIN:VCALENDAR"]
        lines.extend(prop.serialize() for prop in self.top_props)
        for kind, item in self.blocks:
            lines.append(item.serialize() if kind == "event" else item)
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines) + "\r\n"


def _block_name(logical: str) -> str:
    upper = logical.strip().upper()
    if upper.startswith("BEGIN:"):
        return upper[6:]
    if upper.startswith("END:"):
        return upper[4:]
    return ""


def parse_calendar(text: str) -> VCalendar:
    """Parse one VCALENDAR; unknown components pass through verbatim."""
    cal = VCalendar()
    mode: str | None = None
    bucket: list[tuple[str, str]] = []
    alarm_bucket: list[tuple[str, str]] | None = None
    event_props: list[tuple[str, str]] = []
    event_alarms: list[VAlarm] = []
    active_event = False
    for logical, raw in unfold_spans(text):
        name = _block_name(logical)
        if name == "VCALENDAR" and logical.strip().upper().startswith(
                "BEGIN:"):
            continue
        if name == "VCALENDAR":
            break
        if logical.strip().upper() == "BEGIN:VEVENT":
            active_event = True
            event_props = []
            event_alarms = []
            continue
        if logical.strip().upper() == "END:VEVENT":
            event = VEvent(props=_props_from_spans(event_props),
                           alarms=list(event_alarms))
            cal.blocks.append(("event", event))
            active_event = False
            continue
        if not active_event and logical.strip().upper() in (
                "BEGIN:VTIMEZONE", "BEGIN:VTODO", "BEGIN:VJOURNAL",
                "BEGIN:VFREEBUSY", "BEGIN:VAVAILABILITY"):
            mode = logical.strip().upper()[6:]
            bucket = [(logical, raw)]
            continue
        if mode is not None:
            bucket.append((logical, raw))
            if logical.strip().upper() == f"END:{mode}":
                block = "\r\n".join(raw_line for _, raw_line in bucket)
                if mode == "VTIMEZONE":
                    cal.blocks.append(("timezone", block))
                else:
                    cal.blocks.append(("other", block))
                mode = None
                bucket = []
            continue
        if active_event and logical.strip().upper() == "BEGIN:VALARM":
            alarm_bucket = []
            continue
        if alarm_bucket is not None:
            if logical.strip().upper() == "END:VALARM":
                event_alarms.append(_alarm_from_spans(alarm_bucket))
                alarm_bucket = None
            else:
                alarm_bucket.append((logical, raw))
            continue
        if active_event:
            event_props.append((logical, raw))
        else:
            prop = parse_property(logical, raw)
            if prop is not None:
                cal.top_props.append(prop)
    return cal


def parse_events(text: str) -> list[VEvent]:
    """Parse every VEVENT in *text*, tolerating a missing VCALENDAR shell."""
    cal = parse_calendar(text)
    if cal.events:
        return cal.events
    out: list[VEvent] = []
    active = False
    props: list[tuple[str, str]] = []
    alarms: list[VAlarm] = []
    alarm_bucket: list[tuple[str, str]] | None = None
    for logical, raw in unfold_spans(text):
        upper = logical.strip().upper()
        if upper == "BEGIN:VEVENT":
            active = True
            props = []
            alarms = []
            continue
        if upper == "END:VEVENT":
            out.append(VEvent(props=_props_from_spans(props),
                              alarms=list(alarms)))
            active = False
            continue
        if not active:
            continue
        if upper == "BEGIN:VALARM":
            alarm_bucket = []
            continue
        if alarm_bucket is not None:
            if upper == "END:VALARM":
                alarms.append(_alarm_from_spans(alarm_bucket))
                alarm_bucket = None
            else:
                alarm_bucket.append((logical, raw))
            continue
        props.append((logical, raw))
    return out


def new_event(uid: str = "", summary: str = "", start_millis: int = 0,
              end_millis: int = 0, tzid: str = "", all_day: bool = False,
              ) -> VEvent:
    """Build a minimal VEVENT; modeled fields stay editable, the rest raw."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    props = [
        ICalProperty(name="UID", value=(uid or make_uid()), modified=True),
        ICalProperty(name="DTSTAMP", value=stamp, modified=True),
    ]
    if all_day:
        start_text = (datetime.fromtimestamp(start_millis / 1000,
                                             timezone.utc)
                      .strftime("%Y%m%d"))
        end_text = (datetime.fromtimestamp(end_millis / 1000, timezone.utc)
                    .strftime("%Y%m%d"))
        props.append(ICalProperty(name="DTSTART",
                                  params=[("VALUE", ["DATE"])],
                                  value=start_text, modified=True))
        props.append(ICalProperty(name="DTEND",
                                  params=[("VALUE", ["DATE"])],
                                  value=end_text, modified=True))
    elif tzid:
        zone = _zone_or_none(tzid) or timezone.utc
        start_text = (datetime.fromtimestamp(start_millis / 1000,
                                             timezone.utc)
                      .astimezone(zone).strftime("%Y%m%dT%H%M%S"))
        end_text = (datetime.fromtimestamp(end_millis / 1000, timezone.utc)
                    .astimezone(zone).strftime("%Y%m%dT%H%M%S"))
        props.append(ICalProperty(name="DTSTART",
                                  params=[("TZID", [tzid])],
                                  value=start_text, modified=True))
        props.append(ICalProperty(name="DTEND",
                                  params=[("TZID", [tzid])],
                                  value=end_text, modified=True))
    else:
        start_text = (datetime.fromtimestamp(start_millis / 1000,
                                             timezone.utc)
                      .strftime("%Y%m%dT%H%M%SZ"))
        end_text = (datetime.fromtimestamp(end_millis / 1000, timezone.utc)
                    .strftime("%Y%m%dT%H%M%SZ"))
        props.append(ICalProperty(name="DTSTART", value=start_text,
                                  modified=True))
        props.append(ICalProperty(name="DTEND", value=end_text,
                                  modified=True))
    if summary:
        props.append(ICalProperty(name="SUMMARY", value=escape_text(summary),
                                  modified=True))
    return VEvent(props=props)
