"""GUI-free calendar models: ports of the Android pure-JVM core.

Ports ``core/RRuleModel.kt``, ``core/ScopeResolver.kt``, ``core/TimeMath.kt``,
``core/AgendaGrouping.kt``, ``core/SigilAssigner.kt`` (collapsed to four
perceptual tiers per todo S1), ``calendar/Models.kt`` draft types (with TEXT
UIDs per Revision 2), and the ``alarm/`` planner math (``ReminderPlanner``,
``AlarmKeys``, ``HeartbeatClock``).

No GTK, no D-Bus, no I/O: safe headless and in tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# ---------------------------------------------------------------- RRuleModel


class Frequency(Enum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    YEARLY = "YEARLY"


class Weekday(Enum):
    MO = 0
    TU = 1
    WE = 2
    TH = 3
    FR = 4
    SA = 5
    SU = 6


@dataclass(frozen=True)
class ByDay:
    ordinal: int | None
    weekday: Weekday

    def token(self) -> str:
        if self.ordinal is None:
            return self.weekday.name
        return f"{self.ordinal:d}{self.weekday.name}"


@dataclass(frozen=True)
class EndNever:
    pass


@dataclass(frozen=True)
class EndUntil:
    until_millis_utc: int
    date_only: bool = False


@dataclass(frozen=True)
class EndCount:
    occurrences: int


@dataclass(frozen=True)
class RuleParsed:
    rule: RRuleModel


@dataclass(frozen=True)
class RuleRefused:
    reason: str


def _parse_byday_token(token: str) -> ByDay | None:
    if len(token) < 2:
        return None
    try:
        weekday = Weekday[token[-2:].upper()]
    except KeyError:
        return None
    number = token[:-2]
    if not number:
        return ByDay(None, weekday)
    try:
        ordinal = int(number)
    except ValueError:
        return None
    if ordinal == 0:
        return None
    return ByDay(ordinal, weekday)


def _parse_until(value: str) -> tuple[int, bool] | None:
    text = value.strip().upper()
    try:
        if len(text) == 8:
            date = datetime.strptime(text, "%Y%m%d").replace(
                tzinfo=timezone.utc)
            return int(date.timestamp() * 1000), True
        moment = datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc)
        return int(moment.timestamp() * 1000), False
    except ValueError:
        return None


def _format_until(millis: int) -> str:
    return (datetime.fromtimestamp(millis / 1000, timezone.utc)
            .strftime("%Y%m%dT%H%M%SZ"))


def _format_until_date(millis: int) -> str:
    return (datetime.fromtimestamp(millis / 1000, timezone.utc)
            .strftime("%Y%m%d"))


@dataclass(frozen=True)
class RRuleModel:
    frequency: Frequency
    interval: int = 1
    by_day: tuple = ()
    by_month_day: tuple = ()
    by_month: tuple = ()
    by_set_pos: tuple = ()
    week_start: Weekday | None = None
    end: object = field(default_factory=EndNever)

    def serialize(self) -> str:
        parts = [f"FREQ={self.frequency.value}"]
        if self.interval > 1:
            parts.append(f"INTERVAL={self.interval:d}")
        if self.by_day:
            parts.append("BYDAY=" + ",".join(d.token() for d in self.by_day))
        if self.by_month_day:
            parts.append("BYMONTHDAY=" + ",".join(str(v) for v in self.by_month_day))
        if self.by_month:
            parts.append("BYMONTH=" + ",".join(str(v) for v in self.by_month))
        if self.by_set_pos:
            parts.append("BYSETPOS=" + ",".join(str(v) for v in self.by_set_pos))
        if self.week_start is not None:
            parts.append(f"WKST={self.week_start.name}")
        if isinstance(self.end, EndUntil):
            if self.end.date_only:
                parts.append("UNTIL=" + _format_until_date(self.end.until_millis_utc))
            else:
                parts.append("UNTIL=" + _format_until(self.end.until_millis_utc))
        elif isinstance(self.end, EndCount):
            parts.append(f"COUNT={self.end.occurrences:d}")
        return ";".join(parts)

    @staticmethod
    def daily() -> RRuleModel:
        return RRuleModel(Frequency.DAILY)

    @staticmethod
    def weekly(by_days: list[Weekday]) -> RRuleModel:
        return RRuleModel(Frequency.WEEKLY,
                          by_day=tuple(ByDay(None, d) for d in by_days))

    @staticmethod
    def monthly_by_date(month_day: int) -> RRuleModel:
        return RRuleModel(Frequency.MONTHLY, by_month_day=(month_day,))

    @staticmethod
    def monthly_by_nth_weekday(entry: ByDay) -> RRuleModel:
        return RRuleModel(Frequency.MONTHLY, by_day=(entry,))

    @staticmethod
    def yearly() -> RRuleModel:
        return RRuleModel(Frequency.YEARLY)

    @staticmethod
    def weekdays() -> RRuleModel:
        return RRuleModel.weekly(
            [Weekday.MO, Weekday.TU, Weekday.WE, Weekday.TH, Weekday.FR])

    @staticmethod
    def parse(raw: str) -> RuleParsed | RuleRefused:
        try:
            return _parse_rule_parts(raw)
        except Exception as exc:  # total parser: never throws
            return RuleRefused(f"unparseable rule: {exc}")


def _parse_rule_parts(raw: str) -> RuleParsed | RuleRefused:
    trimmed = raw.strip()
    if trimmed[:6].upper() == "RRULE:":
        trimmed = trimmed[6:].strip()
    if not trimmed:
        return RuleRefused("rule is empty")
    frequency: Frequency | None = None
    interval: int | None = None
    until_millis: int | None = None
    until_date_only = False
    count: int | None = None
    week_start: Weekday | None = None
    by_day: list[ByDay] = []
    by_month_day: list[int] = []
    by_month: list[int] = []
    by_set_pos: list[int] = []
    for raw_part in trimmed.split(";"):
        part = raw_part.strip()
        eq = part.find("=")
        if eq <= 0 or eq == len(part) - 1:
            return RuleRefused(
                f'malformed rule part "{part}": expected KEY=VALUE')
        key = part[:eq].strip().upper()
        value = part[eq + 1:].strip()
        if key == "FREQ":
            upper = value.upper()
            if upper in ("SECONDLY", "MINUTELY", "HOURLY"):
                return RuleRefused(
                    f"unsupported frequency {value}: "
                    "sub-daily recurrence is out of scope")
            try:
                frequency = Frequency[upper]
            except KeyError:
                return RuleRefused(f'unknown frequency "{value}"')
        elif key == "INTERVAL":
            try:
                parsed = int(value)
            except ValueError:
                return RuleRefused(
                    f'INTERVAL must be an integer, was "{value}"')
            if parsed < 1:
                return RuleRefused(f"INTERVAL must be >= 1, was {parsed:d}")
            interval = parsed
        elif key == "BYDAY":
            for token in value.split(","):
                entry = _parse_byday_token(token.strip())
                if entry is None:
                    return RuleRefused(f'malformed BYDAY entry "{token}"')
                by_day.append(entry)
        elif key == "BYMONTHDAY":
            for token in value.split(","):
                try:
                    day = int(token.strip())
                except ValueError:
                    return RuleRefused(
                        f'BYMONTHDAY must be an integer, was "{token}"')
                if day == 0 or not -31 <= day <= 31:
                    return RuleRefused(
                        "BYMONTHDAY must be 1..31 or -1..-31, "
                        f"was {day:d}")
                by_month_day.append(day)
        elif key == "BYMONTH":
            for token in value.split(","):
                try:
                    month = int(token.strip())
                except ValueError:
                    return RuleRefused(
                        f'BYMONTH must be an integer, was "{token}"')
                if not 1 <= month <= 12:
                    return RuleRefused(
                        f"BYMONTH must be 1..12, was {month:d}")
                by_month.append(month)
        elif key == "BYSETPOS":
            for token in value.split(","):
                try:
                    pos = int(token.strip())
                except ValueError:
                    return RuleRefused(
                        f'BYSETPOS must be an integer, was "{token}"')
                if pos == 0 or not -366 <= pos <= 366:
                    return RuleRefused(
                        "BYSETPOS must be 1..366 or -1..-366, "
                        f"was {pos:d}")
                by_set_pos.append(pos)
        elif key == "WKST":
            try:
                week_start = Weekday[value.upper()]
            except KeyError:
                return RuleRefused(
                    f'WKST must be a weekday, was "{value}"')
        elif key == "UNTIL":
            parsed = _parse_until(value)
            if parsed is None:
                return RuleRefused(
                    f'malformed UNTIL "{value}": expected yyyyMMdd or '
                    "yyyyMMdd'T'HHmmss'Z'")
            until_millis, until_date_only = parsed
        elif key == "COUNT":
            try:
                parsed_count = int(value)
            except ValueError:
                return RuleRefused(
                    f'COUNT must be an integer, was "{value}"')
            if parsed_count < 1:
                return RuleRefused(
                    f"COUNT must be >= 1, was {parsed_count:d}")
            count = parsed_count
        else:
            return RuleRefused(f"unsupported rule part: {key}")
    if frequency is None:
        return RuleRefused("missing FREQ")
    if until_millis is not None and count is not None:
        return RuleRefused("UNTIL and COUNT cannot be combined in one rule")
    for entry in by_day:
        if (entry.ordinal is not None
                and frequency not in (Frequency.MONTHLY, Frequency.YEARLY)):
            return RuleRefused(
                f"BYDAY ordinal {entry.ordinal:d}{entry.weekday.name} "
                "is only legal in MONTHLY or YEARLY")
    if until_millis is not None:
        end: object = EndUntil(until_millis, until_date_only)
    elif count is not None:
        end = EndCount(count)
    else:
        end = EndNever()
    return RuleParsed(RRuleModel(
        frequency=frequency, interval=interval or 1,
        by_day=tuple(by_day), by_month_day=tuple(by_month_day),
        by_month=tuple(by_month), by_set_pos=tuple(by_set_pos),
        week_start=week_start, end=end))


# ------------------------------------------------------------ draft types


@dataclass
class CalendarSummary:
    id: str
    display_name: str
    account_name: str = ""
    color: str = ""
    is_visible: bool = True
    is_writable: bool = True


@dataclass
class CalendarInstance:
    uid: str
    calendar_id: str
    start_millis: int
    end_millis: int
    title: str | None = None
    location: str | None = None
    description: str | None = None
    all_day: bool = False
    event_timezone: str | None = None
    event_end_timezone: str | None = None
    rrule: str | None = None
    duration: str | None = None
    availability: int = 0
    status: str | None = None
    recurrence_id: int | None = None
    self_attendee_status: int = 0


AVAILABILITY_BUSY = 0
AVAILABILITY_FREE = 1
AVAILABILITY_TENTATIVE = 2


@dataclass
class EventDraft:
    calendar_id: str
    start_millis: int
    end_millis: int | None
    event_timezone: str = "UTC"
    uid: str | None = None
    title: str | None = None
    location: str | None = None
    description: str | None = None
    duration: str | None = None
    all_day: bool = False
    event_end_timezone: str | None = None
    rrule: str | None = None
    rdate: str | None = None
    exdate: str | None = None
    availability: int = AVAILABILITY_BUSY
    status: str | None = None
    recurrence_id: int | None = None


@dataclass
class LoadedEvent:
    draft: EventDraft
    opaque: list = field(default_factory=list)

    @property
    def uid(self) -> str:
        if self.draft.uid is None:
            raise ValueError("LoadedEvent always carries a uid")
        return self.draft.uid


# ------------------------------------------------------------ scope resolve


@dataclass(frozen=True)
class RecurringEventContext:
    parent_uid: str
    rule: RRuleModel | None
    start_millis: int
    all_day: bool


@dataclass(frozen=True)
class InstanceRef:
    parent_uid: str
    instance_start_millis: int


class RecurrenceScope(Enum):
    THIS_INSTANCE = "this"
    THIS_AND_FOLLOWING = "this-and-following"
    ALL_EVENTS = "all"


@dataclass
class EventFieldEdits:
    title: str | None = None
    location: str | None = None
    description: str | None = None
    start_millis: int | None = None
    end_millis: int | None = None
    duration: str | None = None
    all_day: bool | None = None
    event_timezone: str | None = None
    event_end_timezone: str | None = None
    availability: int | None = None
    calendar_id: str | None = None
    clear_title: bool = False
    clear_location: bool = False
    clear_description: bool = False
    clear_end_millis: bool = False
    clear_duration: bool = False
    clear_event_end_timezone: bool = False


@dataclass(frozen=True)
class UpdateParentRow:
    parent_uid: str
    edits: EventFieldEdits


@dataclass(frozen=True)
class InsertExceptionRow:
    parent_uid: str
    original_instance_time_millis: int
    new_row_edits: EventFieldEdits


@dataclass(frozen=True)
class SplitParent:
    parent_uid: str
    new_until: EndUntil
    new_row_start_millis: int
    new_row_edits: EventFieldEdits
    remaining_rule: RRuleModel


@dataclass(frozen=True)
class DeleteParentRow:
    parent_uid: str


@dataclass(frozen=True)
class SetUntil:
    parent_uid: str
    until: EndUntil


@dataclass(frozen=True)
class DeleteInstanceUri:
    parent_uid: str
    instance_start_millis: int


@dataclass(frozen=True)
class Refusal:
    reason: str


COUNT_REFUSAL = "cannot split a COUNT-bounded series without expanding it"


class ScopeResolver:
    @staticmethod
    def resolve_edit(context: RecurringEventContext,
                     scope: RecurrenceScope, instance: InstanceRef,
                     edits: EventFieldEdits):
        if context.rule is None:
            return UpdateParentRow(context.parent_uid, edits)
        if scope in (RecurrenceScope.THIS_INSTANCE,
                     RecurrenceScope.THIS_AND_FOLLOWING):
            if instance.instance_start_millis <= 0:
                return Refusal("invalid recurrence_id for scoped edit")
        if scope == RecurrenceScope.THIS_INSTANCE:
            return InsertExceptionRow(
                context.parent_uid, instance.instance_start_millis, edits)
        if scope == RecurrenceScope.THIS_AND_FOLLOWING:
            if isinstance(context.rule.end, EndCount):
                return Refusal(COUNT_REFUSAL)
            return SplitParent(
                context.parent_uid,
                EndUntil(instance.instance_start_millis - 1,
                         date_only=context.all_day),
                instance.instance_start_millis, edits, context.rule)
        return UpdateParentRow(context.parent_uid, edits)

    @staticmethod
    def resolve_delete(context: RecurringEventContext,
                       scope: RecurrenceScope, instance: InstanceRef):
        if context.rule is None:
            return DeleteParentRow(context.parent_uid)
        if scope in (RecurrenceScope.THIS_INSTANCE,
                     RecurrenceScope.THIS_AND_FOLLOWING):
            if instance.instance_start_millis <= 0:
                return Refusal("invalid recurrence_id for scoped delete")
        if scope == RecurrenceScope.THIS_INSTANCE:
            return DeleteInstanceUri(context.parent_uid,
                                     instance.instance_start_millis)
        if scope == RecurrenceScope.THIS_AND_FOLLOWING:
            if isinstance(context.rule.end, EndCount):
                return Refusal(COUNT_REFUSAL)
            return SetUntil(
                context.parent_uid,
                EndUntil(instance.instance_start_millis - 1,
                         date_only=context.all_day))
        return DeleteParentRow(context.parent_uid)


# ---------------------------------------------------------------- TimeMath

MILLIS_PER_DAY = 86_400_000

_SAMPLE_INSTANTS = (
    datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
    datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc),
    datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
    datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc),
)


def _offsets_or_none(zone_id: str) -> tuple | None:
    try:
        zone = ZoneInfo(zone_id)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    try:
        return tuple(moment.astimezone(zone).utcoffset()
                     for moment in _SAMPLE_INSTANTS)
    except Exception:
        return None


class TimeMath:
    @staticmethod
    def all_day_date_to_storage(date) -> int:
        midnight = datetime(date.year, date.month, date.day,
                            tzinfo=timezone.utc)
        return int(midnight.timestamp() * 1000)

    @staticmethod
    def storage_to_all_day_date(storage_millis: int):
        return (datetime.fromtimestamp(storage_millis / 1000, timezone.utc)
                .date())

    @staticmethod
    def local_day_start(date, zone) -> int:
        if isinstance(zone, str):
            zone = ZoneInfo(zone)
        midnight = datetime(date.year, date.month, date.day,
                            tzinfo=zone)
        return int(midnight.timestamp() * 1000)

    @staticmethod
    def local_date_of(epoch_millis: int, zone):
        if isinstance(zone, str):
            zone = ZoneInfo(zone)
        return (datetime.fromtimestamp(epoch_millis / 1000, timezone.utc)
                .astimezone(zone).date())

    @staticmethod
    def should_render_timezone(event_zone: str, device_zone: str) -> bool:
        event = _offsets_or_none(event_zone)
        device = _offsets_or_none(device_zone)
        if event is None and device is None:
            return False
        if event is None or device is None:
            return True
        return event != device


# ------------------------------------------------------------- agenda group


@dataclass(frozen=True)
class InstanceSpan:
    uid: str
    start_millis: int
    end_millis: int
    all_day: bool = False


@dataclass
class DayBucket:
    date: object
    all_day: list = field(default_factory=list)
    timed: list = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.all_day and not self.timed


MAX_COVERED_DAYS = 366


def _covered_dates(span: InstanceSpan, zone, window) -> list:
    zone_for_dates = timezone.utc if span.all_day else zone
    if isinstance(zone_for_dates, str):
        zone_for_dates = ZoneInfo(zone_for_dates)
    last_instant = (span.end_millis - 1 if span.end_millis > span.start_millis
                    else span.start_millis)
    first = TimeMath.local_date_of(span.start_millis, zone_for_dates)
    last = TimeMath.local_date_of(last_instant, zone_for_dates)
    cursor, stop = first, last
    if window is not None:
        window_start, window_end = window
        if window_end < first or window_start > last:
            return []
        cursor = max(cursor, window_start)
        stop = min(stop, window_end)
    dates = []
    while cursor <= stop and len(dates) < MAX_COVERED_DAYS:
        dates.append(cursor)
        cursor = cursor + timedelta(days=1)
    return dates


class AgendaGrouping:
    @staticmethod
    def group(instances: list[InstanceSpan], zone, window=None
              ) -> list[DayBucket]:
        if isinstance(zone, str):
            zone = ZoneInfo(zone)
        by_date: dict = {}
        for span in instances:
            for date in _covered_dates(span, zone, window):
                by_date.setdefault(date, []).append(span)
        buckets = []
        for date in sorted(by_date):
            spans = by_date[date]
            all_day = sorted((s for s in spans if s.all_day),
                             key=lambda s: (s.start_millis, s.uid))
            timed = sorted((s for s in spans if not s.all_day),
                           key=lambda s: (s.start_millis, s.uid))
            buckets.append(DayBucket(date=date, all_day=all_day, timed=timed))
        return buckets


# ----------------------------------------------------------------- sigils


class SigilTier(Enum):
    TIER_1 = ("▌", "text")
    TIER_2 = ("▏", "strong")
    TIER_3 = ("░", "muted")
    TIER_4 = ("▒", "shade")

    def __init__(self, glyph: str, ramp: str) -> None:
        self.glyph = glyph
        self.ramp_name = ramp


@dataclass(frozen=True)
class CalendarKey:
    calendar_id: str
    account_name: str = ""


@dataclass(frozen=True)
class SigilAssignment:
    assignments: dict
    newly_assigned: list


class SigilAssigner:
    @staticmethod
    def assign(existing: dict, candidates: list,
               overrides: dict | None = None) -> SigilAssignment:
        overrides = overrides or {}
        effective = {k: v for k, v in existing.items() if k not in overrides}
        effective.update(overrides)
        claimed = set(effective.values())
        pool = [t for t in SigilTier if t not in claimed]
        result = dict(effective)
        fresh: list = []
        pool_index = 0
        for key in candidates:
            if key in result:
                continue
            tier = (pool[pool_index] if pool_index < len(pool)
                    else SigilTier.TIER_1)
            pool_index += 1
            result[key] = tier
            fresh.append(key)
        return SigilAssignment(result, fresh)


# ------------------------------------------------------- reminder planner


@dataclass(frozen=True)
class AlarmKey:
    event_uid: str
    instance_start_millis: int
    trigger_at_millis: int

    def __lt__(self, other) -> bool:
        if not isinstance(other, AlarmKey):
            return NotImplemented
        return ((self.event_uid, self.instance_start_millis,
                 self.trigger_at_millis)
                < (other.event_uid, other.instance_start_millis,
                   other.trigger_at_millis))


@dataclass
class PlannerInstance:
    event_uid: str
    start_millis: int
    all_day: bool = False
    declined: bool = False
    reminder_minutes: list = field(default_factory=list)


@dataclass(frozen=True)
class Plan:
    to_set: tuple = ()
    to_cancel: tuple = ()


@dataclass(frozen=True)
class AllDayNotification:
    hour_of_day: int = 18
    days_before: int = 1


ALL_DAY_LEAD_MILLIS = 18 * 60 * 60 * 1000
_MINUTE_MILLIS = 60_000


def _all_day_trigger(start_millis: int, policy: AllDayNotification,
                     zone) -> int:
    event_date = TimeMath.storage_to_all_day_date(start_millis)
    target = event_date - timedelta(
        days=max(0, min(365, policy.days_before)))
    hour = max(0, min(23, policy.hour_of_day))
    if isinstance(zone, str):
        zone = ZoneInfo(zone)
    moment = datetime(target.year, target.month, target.day, hour, 0,
                      tzinfo=zone)
    return int(moment.timestamp() * 1000)


class ReminderPlanner:
    ALL_DAY_LEAD_MILLIS = ALL_DAY_LEAD_MILLIS

    @staticmethod
    def plan(instances: list[PlannerInstance], now_millis: int,
             policy: AllDayNotification | None = None, zone=None) -> set:
        if zone is None:
            zone = timezone.utc
        out: set[AlarmKey] = set()
        for instance in instances:
            if instance.declined:
                continue
            if not instance.reminder_minutes:
                continue
            if instance.all_day:
                if policy is not None:
                    trigger = _all_day_trigger(instance.start_millis,
                                               policy, zone)
                else:
                    trigger = (instance.start_millis - ALL_DAY_LEAD_MILLIS)
                if trigger > now_millis:
                    out.add(AlarmKey(instance.event_uid,
                                     instance.start_millis, trigger))
            else:
                for minutes in instance.reminder_minutes:
                    trigger = (instance.start_millis
                               - int(minutes) * _MINUTE_MILLIS)
                    if trigger > now_millis:
                        out.add(AlarmKey(instance.event_uid,
                                         instance.start_millis, trigger))
        return out

    @staticmethod
    def compute_plan(desired: set, scheduled: set) -> Plan:
        return Plan(to_set=tuple(sorted(desired - scheduled)),
                    to_cancel=tuple(sorted(scheduled - desired)))


def _java_string_hash(text: str) -> int:
    h = 0
    for char in text:
        h = ((h * 31) + ord(char)) & 0xFFFFFFFF
    return int(h if h < 0x80000000 else h - 0x100000000)


def _java_long_hash(value: int) -> int:
    masked = int(value) & 0xFFFFFFFFFFFFFFFF
    h = (masked ^ (masked >> 32)) & 0xFFFFFFFF
    return int(h if h < 0x80000000 else h - 0x100000000)


class AlarmKeys:
    SEP = ":"

    @staticmethod
    def stable_request_code(key: AlarmKey) -> int:
        h = _java_string_hash(key.event_uid)
        h = ((h * 31) + _java_long_hash(key.instance_start_millis)
             ) & 0xFFFFFFFF
        h = ((h * 31) + _java_long_hash(key.trigger_at_millis)
             ) & 0xFFFFFFFF
        return int(h if h < 0x80000000 else h - 0x100000000)

    @staticmethod
    def encode(key: AlarmKey) -> str:
        return (f"{key.event_uid}{AlarmKeys.SEP}"
                f"{key.instance_start_millis:d}{AlarmKeys.SEP}"
                f"{key.trigger_at_millis:d}")

    @staticmethod
    def decode(line: str) -> AlarmKey | None:
        parts = line.split(AlarmKeys.SEP)
        if len(parts) != 3 or not parts[0]:
            return None
        try:
            return AlarmKey(parts[0], int(parts[1]), int(parts[2]))
        except ValueError:
            return None

    @staticmethod
    def encode_all(keys: set) -> str:
        return "\n".join(sorted(AlarmKeys.encode(k) for k in keys))

    @staticmethod
    def decode_all(blob: str) -> set:
        out: set[AlarmKey] = set()
        for line in blob.splitlines():
            if not line.strip():
                continue
            key = AlarmKeys.decode(line.strip())
            if key is not None:
                out.add(key)
        return out


class HeartbeatClock:
    HOUR_OF_DAY = 3

    @staticmethod
    def next_after(now_millis: int, zone) -> int:
        if isinstance(zone, str):
            zone = ZoneInfo(zone)
        now = datetime.fromtimestamp(now_millis / 1000, timezone.utc
                                     ).astimezone(zone)
        candidate = now.replace(hour=HeartbeatClock.HOUR_OF_DAY, minute=0,
                                second=0, microsecond=0)
        if candidate <= now:
            candidate = candidate + timedelta(days=1)
        return int(candidate.timestamp() * 1000)
