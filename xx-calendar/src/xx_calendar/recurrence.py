"""Windowed recurrence expansion (Revision 2 §1).

Expands RRULE (FREQ DAILY/WEEKLY/MONTHLY/YEARLY, INTERVAL, COUNT, UNTIL,
BYDAY with ordinals, BYMONTHDAY, BYMONTH, BYSETPOS, WKST) plus RDATE/EXDATE
and RECURRENCE-ID overrides. Iteration runs in the event's wall-clock
TZID via stdlib ``zoneinfo`` so DST boundaries resolve correctly; UTC
instants are compared against the window, and unbounded rules never
generate past the window end.

Implemented on the stdlib (no ``dateutil``): ``python-dateutil`` is not
importable from the xx-wm gate venv, and XXWM_RULES.md forbids new
dependencies, so the engine below is hand-rolled and pinned by the
recurrence test suite instead.

Conventions: DTSTART always counts as the first occurrence (COUNT
includes it; matches dateutil/Google). Gap wall times (spring forward)
resolve with fold=0 (pre-transition offset); ambiguous times (fall back)
take the first occurrence (fold=0). Unparseable rules yield the lone base
instance, never an error.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from xx_calendar import ics as ics_module
from xx_calendar.models import (
    EndCount,
    EndUntil,
    Frequency,
    RRuleModel,
    RuleParsed,
    Weekday,
)

MAX_RULE_ITERATIONS = 100_000

_WEEKDAY_DATES = (Weekday.MO, Weekday.TU, Weekday.WE, Weekday.TH,
                  Weekday.FR, Weekday.SA, Weekday.SU)


@dataclass
class Instance:
    uid: str = ""
    start_utc: int = 0
    end_utc: int = 0
    all_day: bool = False
    recurrence_id: int | None = None
    is_override: bool = False
    status: str = ""


def _zone_or_utc(tzid: str):
    if tzid:
        try:
            return ZoneInfo(tzid)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return timezone.utc


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def _month_last_day(year: int, month: int) -> int:
    if month == 12:
        return (date(year + 1, 1, 1) - timedelta(days=1)).day
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def _resolve_month_day(year: int, month: int, day: int) -> date | None:
    if day > 0:
        if day > _month_last_day(year, month):
            return None
        return date(year, month, day)
    last = _month_last_day(year, month)
    resolved = last + day + 1
    if resolved < 1:
        return None
    return date(year, month, resolved)


def _nth_weekday(year: int, month: int, ordinal: int,
                 weekday: Weekday) -> date | None:
    first = date(year, month, 1)
    offset = (weekday.value - first.weekday()) % 7
    if ordinal > 0:
        day = 1 + offset + (ordinal - 1) * 7
        if day > _month_last_day(year, month):
            return None
        return date(year, month, day)
    last_day = _month_last_day(year, month)
    last = date(year, month, last_day)
    back = (last.weekday() - weekday.value) % 7
    day = last_day - back + (ordinal + 1) * 7
    if day < 1:
        return None
    return date(year, month, day)


def _all_weekdays(year: int, month: int, weekday: Weekday) -> list[date]:
    out: list[date] = []
    cursor = date(year, month, 1)
    while cursor.month == month:
        if cursor.weekday() == weekday.value:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _month_candidates(year: int, month: int, rule: RRuleModel,
                      dtstart_day: int) -> list[date]:
    days: list[date] = []
    if rule.by_month_day:
        for item in rule.by_month_day:
            resolved = _resolve_month_day(year, month, item)
            if resolved is not None:
                days.append(resolved)
    if rule.by_day:
        for entry in rule.by_day:
            if entry.ordinal is not None:
                resolved = _nth_weekday(year, month, entry.ordinal,
                                        entry.weekday)
                if resolved is not None:
                    days.append(resolved)
            else:
                days.extend(_all_weekdays(year, month, entry.weekday))
    if not rule.by_month_day and not rule.by_day:
        resolved = _resolve_month_day(year, month, dtstart_day)
        if resolved is not None:
            days.append(resolved)
    return sorted(set(days))


def _apply_bysetpos(items: list, by_set_pos: tuple) -> list:
    if not by_set_pos or not items:
        return items
    ordered = sorted(items)
    picked: list = []
    for pos in by_set_pos:
        index = pos - 1 if pos > 0 else len(ordered) + pos
        if 0 <= index < len(ordered):
            picked.append(ordered[index])
    return sorted(set(picked))


def _local_to_utc(wall: datetime, zone) -> int:
    return int(wall.replace(tzinfo=zone).timestamp() * 1000)


def _generate_rule_dates(rule: RRuleModel, start_day: date,
                         dtstart_day: int,
                         stop_day: date | None = None,
                         cap: int | None = None) -> list[date]:
    """Generate rule-matching dates (DTSTART excluded).

    *stop_day* bounds UNTIL/unbounded rules: generation stops past it so a
    windowed query never materializes centuries. *cap* bounds COUNT rules
    to the occurrences still needed. Output is ascending in every branch.
    Both caps sit under the MAX_RULE_ITERATIONS hard ceiling.
    """
    out: list[date] = []
    weekend = rule.week_start or Weekday.MO
    iterations = 0

    def limited() -> bool:
        nonlocal iterations
        iterations += 1
        return iterations > MAX_RULE_ITERATIONS

    def capped() -> bool:
        return ((cap is not None and len(out) >= cap)
                or len(out) >= MAX_RULE_ITERATIONS)

    if rule.frequency == Frequency.DAILY:
        step = max(1, rule.interval)
        offset = step
        cursor = start_day + timedelta(days=offset)
        while True:
            if stop_day is not None and cursor > stop_day:
                return out
            if limited():
                return out
            if rule.by_day and not any(
                    cursor.weekday() == d.weekday.value
                    for d in rule.by_day):
                pass
            elif rule.by_month_day and not any(
                    cursor.day == d if d > 0
                    else cursor.day == _month_last_day(cursor.year,
                                                      cursor.month) + d + 1
                    for d in rule.by_month_day):
                pass
            elif rule.by_month and cursor.month not in rule.by_month:
                pass
            else:
                picked = _apply_bysetpos([cursor], rule.by_set_pos)
                out.extend(picked)
            if capped():
                return out
            offset += step
            cursor = start_day + timedelta(days=offset)
            if offset > 366 * 400:
                return out
    elif rule.frequency == Frequency.WEEKLY:
        wanted = [d.weekday for d in rule.by_day] if rule.by_day else [
            _WEEKDAY_DATES[start_day.weekday()]]
        week_monday = start_day - timedelta(
            days=(start_day.weekday() - weekend.value) % 7)
        week_index = 0
        step = max(1, rule.interval)
        while True:
            base = week_monday + timedelta(weeks=week_index)
            if stop_day is not None and base > stop_day:
                return out
            days = sorted(base + timedelta(
                days=(day.value - weekend.value) % 7) for day in wanted)
            if rule.by_month:
                days = [d for d in days if d.month in rule.by_month]
            picked = _apply_bysetpos(days, rule.by_set_pos)
            out.extend(d for d in picked if d > start_day)
            if capped() or limited():
                return out
            week_index += step
            if week_index > 7 * 52000:
                return out
    elif rule.frequency == Frequency.MONTHLY:
        step = max(1, rule.interval)
        index = 0
        while True:
            total = (start_day.year * 12 + start_day.month - 1) + index
            year, month = divmod(total, 12)
            month += 1
            if stop_day is not None and date(year, month, 1) > stop_day:
                return out
            if rule.by_month and month not in rule.by_month:
                pass
            else:
                days = _month_candidates(year, month, rule, dtstart_day)
                picked = _apply_bysetpos(days, rule.by_set_pos)
                out.extend(d for d in picked if d > start_day)
            if capped() or limited():
                return out
            index += step
            if index > 12 * 500:
                return out
    else:
        months = sorted(rule.by_month) if rule.by_month else [start_day.month]
        year = start_day.year
        while True:
            if stop_day is not None and year > stop_day.year:
                return out
            year_days: list[date] = []
            for month in months:
                year_days.extend(_month_candidates(year, month, rule,
                                                   dtstart_day))
            picked = _apply_bysetpos(sorted(year_days), rule.by_set_pos)
            picked = [d for d in picked if d > start_day]
            if stop_day is not None:
                picked = [d for d in picked if d <= stop_day]
            out.extend(picked)
            if capped() or limited():
                return out
            year += max(1, rule.interval)
            if year - start_day.year > 500:
                return out
            if stop_day is not None and year > stop_day.year + 1:
                return out
    return out


def _split_rdate_values(prop) -> list[tuple[str, object]]:
    return [(value.strip(), prop) for value in prop.value.split(",")
            if value.strip()]


def expand_vevent(event: ics_module.VEvent, window_start_utc: int,
                  window_end_utc: int,
                  overrides: list[ics_module.VEvent] | None = None,
                  default_zone: str = "UTC") -> list[Instance]:
    """Expand one VEVENT into instances overlapping the half-open window.

    The window is ``[window_start_utc, window_end_utc)``: an instance
    starting exactly at the window end is excluded, as is one ending
    exactly at the window start. Callers probing a boundary instant
    should widen the window by 1 ms rather than treating the bound as
    inclusive.

    BYSETPOS follows RFC 5545 §3.3.10 evaluation order: positions are
    selected within the full period candidate set (whole week, month,
    or year) and only then are picks on or before DTSTART dropped, so
    the first period never shifts its position.
    """
    start = event.dtstart(default_zone)
    if start is None:
        return []
    dtstart_utc, all_day, tzid = start
    end = event.dtend(default_zone)
    duration = 0
    if event.duration():
        parsed = ics_module.parse_duration_seconds(event.duration())
        if parsed is not None:
            duration = parsed * 1000
            dtend_utc = dtstart_utc + duration
        elif end is not None:
            dtend_utc = end[0]
        else:
            dtend_utc = dtstart_utc
    elif end is not None:
        dtend_utc = end[0]
        duration = dtend_utc - dtstart_utc
    else:
        dtend_utc = (dtstart_utc + 86400000 if all_day else dtstart_utc)
        duration = dtend_utc - dtstart_utc
    uid = event.uid()
    status = event.status()
    base = Instance(uid=uid, start_utc=dtstart_utc, end_utc=dtend_utc,
                    all_day=all_day, status=status)

    rule: RRuleModel | None = None
    count: int | None = None
    until: int | None = None
    until_date_only = False
    raw_rule = event.rrule()
    if raw_rule:
        parsed = RRuleModel.parse(raw_rule)
        if isinstance(parsed, RuleParsed):
            rule = parsed.rule
            if isinstance(rule.end, EndCount):
                count = rule.end.occurrences
            elif isinstance(rule.end, EndUntil):
                until = rule.end.until_millis_utc
                until_date_only = rule.end.date_only

    zone = _zone_or_utc(tzid) if not all_day else timezone.utc
    if all_day:
        start_day = datetime.fromtimestamp(dtstart_utc / 1000,
                                           timezone.utc).date()
        start_time = time(0, 0)
    else:
        local = datetime.fromtimestamp(dtstart_utc / 1000,
                                       timezone.utc).astimezone(zone)
        start_day = local.date()
        start_time = local.time()

    until_date = None
    if until is not None and until_date_only:
        until_date = datetime.fromtimestamp(until / 1000,
                                            timezone.utc).date()

    generated: list[Instance] = [base]
    if rule is not None:
        generated = []
        occurrences = 0

        def accept(start_utc: int) -> bool:
            nonlocal occurrences
            if count is not None and occurrences >= count:
                return False
            if until is not None and start_utc > until:
                return False
            occurrences += 1
            return True

        if accept(dtstart_utc):
            generated.append(base)
        stop_day = None
        if until is not None:
            stop_day = (datetime.fromtimestamp(until / 1000, timezone.utc)
                        + timedelta(days=1)).date()
        elif count is None:
            stop_day = (datetime.fromtimestamp(window_end_utc / 1000,
                                               timezone.utc)
                        + timedelta(days=1)).date()
        need = (count - occurrences) if count is not None else None
        if need is None or need > 0:
            dates = _generate_rule_dates(rule, start_day, start_day.day,
                                         stop_day, need)
            for day in dates:
                if all_day:
                    if until_date is not None and day > until_date:
                        break
                    start_utc = int(datetime(day.year, day.month, day.day,
                                             tzinfo=timezone.utc
                                             ).timestamp() * 1000)
                else:
                    wall = datetime.combine(day, start_time)
                    start_utc = _local_to_utc(wall, zone)
                    if until is not None and start_utc > until:
                        break
                if until is None and count is None \
                        and start_utc >= window_end_utc:
                    break
                if not accept(start_utc):
                    break
                generated.append(Instance(
                    uid=uid, start_utc=start_utc,
                    end_utc=start_utc + duration, all_day=all_day,
                    status=status))

    for prop in event.all("RDATE"):
        value_is_date = any(v.upper() == "DATE" for v in prop.param("VALUE"))
        for raw, _ in _split_rdate_values(prop):
            head = raw.split("/")[0]
            parsed_value = ics_module.parse_ical_datetime(
                head, prop, default_zone)
            if parsed_value is None:
                continue
            moment_utc, is_date, _ = parsed_value
            tail = raw.split("/")[1] if "/" in raw else ""
            if tail:
                tail_parsed = ics_module.parse_ical_datetime(
                    tail, prop, default_zone)
                if tail_parsed is not None:
                    tail_utc, _, _ = tail_parsed
                    span = tail_utc - moment_utc
                else:
                    tail_seconds = ics_module.parse_duration_seconds(tail)
                    span = (tail_seconds * 1000 if tail_seconds is not None
                            else duration)
            else:
                span = duration
            generated.append(Instance(
                uid=uid, start_utc=moment_utc, end_utc=moment_utc + span,
                all_day=(is_date or value_is_date or all_day),
                status=status))

    excluded: set[int] = set()
    for prop in event.all("EXDATE"):
        for raw, _ in _split_rdate_values(prop):
            parsed_value = ics_module.parse_ical_datetime(
                raw.split("/")[0], prop, default_zone)
            if parsed_value is not None:
                excluded.add(parsed_value[0])
    if excluded:
        generated = [g for g in generated if g.start_utc not in excluded]

    for override in overrides or []:
        rid = override.recurrence_id(default_zone)
        if rid is None:
            continue
        original_utc, _, _ = rid
        generated = [g for g in generated
                     if g.start_utc != original_utc]
        if override.status() == "CANCELLED":
            continue
        ostart = override.dtstart(default_zone)
        if ostart is None:
            continue
        ostart_utc, o_all_day, _ = ostart
        oend = override.dtend(default_zone)
        if override.duration():
            o_seconds = ics_module.parse_duration_seconds(
                override.duration())
            oend_utc = (ostart_utc + o_seconds * 1000
                        if o_seconds is not None else ostart_utc + duration)
        elif oend is not None:
            oend_utc = oend[0]
        else:
            oend_utc = ostart_utc + duration
        generated.append(Instance(
            uid=override.uid() or uid, start_utc=ostart_utc,
            end_utc=oend_utc, all_day=o_all_day,
            recurrence_id=original_utc, is_override=True,
            status=override.status()))

    windowed = [g for g in generated
                if g.end_utc > window_start_utc
                and g.start_utc < window_end_utc]
    windowed.sort(key=lambda g: (g.start_utc, g.uid))
    return windowed
