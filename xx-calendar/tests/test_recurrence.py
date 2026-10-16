"""Recurrence tests: DST boundaries, monthly edges, BYSETPOS, exceptions.

Headless: zoneinfo only, no display, no GTK import.
"""
from datetime import datetime, timezone

from xx_calendar import ics
from xx_calendar.recurrence import expand_vevent

NY = "America/New_York"
LONDON = "Europe/London"


def utc(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00"))
               .timestamp() * 1000)


def zulu(millis):
    return datetime.fromtimestamp(millis / 1000, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def event(uid="e1", dtstart="DTSTART:20260302T090000Z",
          dtend="DTEND:20260302T100000Z", rrule="", extra=""):
    lines = ["BEGIN:VEVENT", "UID:" + uid,
             "DTSTAMP:20260101T000000Z", dtstart]
    if dtend:
        lines.append(dtend)
    if rrule:
        lines.append("RRULE:" + rrule)
    if extra:
        lines.append(extra)
    lines.append("END:VEVENT")
    return ics.parse_events("\r\n".join(lines))[0]


def expand(uid="e1", dtstart="DTSTART:20260302T090000Z",
           dtend="DTEND:20260302T100000Z", rrule="", extra="",
           window=("2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z"),
           overrides=None):
    return expand_vevent(event(uid, dtstart, dtend, rrule, extra),
                         utc(window[0]), utc(window[1]),
                         overrides=overrides)


def starts(instances):
    return [zulu(i.start_utc) for i in instances]


def test_single_event_inside_window():
    found = expand(window=("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"))
    assert starts(found) == ["2026-03-02T09:00:00Z"]
    assert found[0].end_utc - found[0].start_utc == 3600000


def test_single_event_outside_window_is_empty():
    assert expand(window=("2026-04-01T00:00:00Z",
                          "2026-05-01T00:00:00Z")) == []


def test_daily_count_and_interval():
    found = expand(rrule="FREQ=DAILY;COUNT=3")
    assert starts(found) == ["2026-03-02T09:00:00Z",
                             "2026-03-03T09:00:00Z",
                             "2026-03-04T09:00:00Z"]
    biweekly = expand(rrule="FREQ=DAILY;INTERVAL=2;COUNT=3")
    assert starts(biweekly) == ["2026-03-02T09:00:00Z",
                                "2026-03-04T09:00:00Z",
                                "2026-03-06T09:00:00Z"]


def test_daily_until_is_inclusive():
    found = expand(rrule="FREQ=DAILY;UNTIL=20260304T090000Z")
    assert starts(found) == ["2026-03-02T09:00:00Z",
                             "2026-03-03T09:00:00Z",
                             "2026-03-04T09:00:00Z"]


def test_weekly_byday_defaults_to_dtstart_weekday():
    found = expand(
        dtstart="DTSTART:20260302T090000Z",
        dtend="DTEND:20260302T100000Z",
        rrule="FREQ=WEEKLY;COUNT=3")
    assert starts(found) == ["2026-03-02T09:00:00Z",
                             "2026-03-09T09:00:00Z",
                             "2026-03-16T09:00:00Z"]


def test_weekly_byday_mo_we_fr():
    found = expand(
        dtstart="DTSTART:20260302T090000Z",
        dtend="DTEND:20260302T100000Z",
        rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=5")
    assert starts(found) == ["2026-03-02T09:00:00Z",
                             "2026-03-04T09:00:00Z",
                             "2026-03-06T09:00:00Z",
                             "2026-03-09T09:00:00Z",
                             "2026-03-11T09:00:00Z"]


def test_weekly_wkst_sunday_starts_weeks_on_sunday():
    found = expand(
        dtstart="DTSTART:20260302T090000Z",
        dtend="DTEND:20260302T100000Z",
        rrule="FREQ=WEEKLY;INTERVAL=2;BYDAY=SU;WKST=SU;COUNT=2")
    assert starts(found) == ["2026-03-02T09:00:00Z",
                             "2026-03-15T09:00:00Z"]


def test_dst_spring_forward_new_york_keeps_wall_time():
    found = expand(
        dtstart="DTSTART;TZID=America/New_York:20260301T090000",
        dtend="DTEND;TZID=America/New_York:20260301T100000",
        rrule="FREQ=DAILY;COUNT=10",
        window=("2026-03-01T00:00:00Z", "2026-03-15T00:00:00Z"))
    assert starts(found)[:7] == [
        "2026-03-01T14:00:00Z", "2026-03-02T14:00:00Z",
        "2026-03-03T14:00:00Z", "2026-03-04T14:00:00Z",
        "2026-03-05T14:00:00Z", "2026-03-06T14:00:00Z",
        "2026-03-07T14:00:00Z"]
    assert starts(found)[7:] == ["2026-03-08T13:00:00Z",
                                 "2026-03-09T13:00:00Z",
                                 "2026-03-10T13:00:00Z"]


def test_dst_fall_back_new_york_keeps_wall_time():
    found = expand(
        dtstart="DTSTART;TZID=America/New_York:20261030T090000",
        dtend="DTEND;TZID=America/New_York:20261030T100000",
        rrule="FREQ=DAILY;COUNT=5",
        window=("2026-10-29T00:00:00Z", "2026-11-10T00:00:00Z"))
    assert starts(found) == ["2026-10-30T13:00:00Z",
                             "2026-10-31T13:00:00Z",
                             "2026-11-01T14:00:00Z",
                             "2026-11-02T14:00:00Z",
                             "2026-11-03T14:00:00Z"]


def test_dst_london_weekly_across_autumn_change():
    found = expand(
        dtstart="DTSTART;TZID=Europe/London:20261020T090000",
        dtend="DTEND;TZID=Europe/London:20261020T100000",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=3",
        window=("2026-10-19T00:00:00Z", "2026-11-10T00:00:00Z"))
    assert starts(found) == ["2026-10-20T08:00:00Z",
                             "2026-10-27T09:00:00Z",
                             "2026-11-03T09:00:00Z"]


def test_monthly_31st_skips_short_months():
    found = expand(
        dtstart="DTSTART:20260131T090000Z",
        dtend="DTEND:20260131T100000Z",
        rrule="FREQ=MONTHLY;COUNT=3",
        window=("2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z"))
    assert starts(found) == ["2026-01-31T09:00:00Z",
                             "2026-03-31T09:00:00Z",
                             "2026-05-31T09:00:00Z"]


def test_monthly_ordinal_byday():
    found = expand(
        dtstart="DTSTART:20260101T090000Z",
        dtend="DTEND:20260101T100000Z",
        rrule="FREQ=MONTHLY;BYDAY=2MO;COUNT=3",
        window=("2026-01-01T00:00:00Z", "2026-05-01T00:00:00Z"))
    assert starts(found) == ["2026-01-01T09:00:00Z",
                             "2026-01-12T09:00:00Z",
                             "2026-02-09T09:00:00Z"]
    last_friday = expand(
        dtstart="DTSTART:20260101T090000Z",
        dtend="DTEND:20260101T100000Z",
        rrule="FREQ=MONTHLY;BYDAY=-1FR;COUNT=2",
        window=("2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"))
    assert starts(last_friday) == ["2026-01-01T09:00:00Z",
                                   "2026-01-30T09:00:00Z"]


def test_monthly_negative_bymonthday():
    found = expand(
        dtstart="DTSTART:20260131T090000Z",
        dtend="DTEND:20260131T100000Z",
        rrule="FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=3",
        window=("2026-01-01T00:00:00Z", "2026-05-01T00:00:00Z"))
    assert starts(found) == ["2026-01-31T09:00:00Z",
                             "2026-02-28T09:00:00Z",
                             "2026-03-31T09:00:00Z"]


def test_bysetpos_last_weekday_of_month():
    found = expand(
        dtstart="DTSTART:20260101T090000Z",
        dtend="DTEND:20260101T100000Z",
        rrule="FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=3",
        window=("2026-01-01T00:00:00Z", "2026-05-01T00:00:00Z"))
    assert starts(found) == ["2026-01-01T09:00:00Z",
                             "2026-01-30T09:00:00Z",
                             "2026-02-27T09:00:00Z"]


def test_yearly_bymonth():
    found = expand(
        dtstart="DTSTART:20260115T090000Z",
        dtend="DTEND:20260115T100000Z",
        rrule="FREQ=YEARLY;BYMONTH=1,7;BYMONTHDAY=15;COUNT=3",
        window=("2026-01-01T00:00:00Z", "2028-01-01T00:00:00Z"))
    assert starts(found) == ["2026-01-15T09:00:00Z",
                             "2026-07-15T09:00:00Z",
                             "2027-01-15T09:00:00Z"]


def test_exdate_suppresses_instances():
    found = expand(rrule="FREQ=DAILY;COUNT=4",
                   extra="EXDATE:20260303T090000Z")
    assert starts(found) == ["2026-03-02T09:00:00Z",
                             "2026-03-04T09:00:00Z",
                             "2026-03-05T09:00:00Z"]


def test_rdate_adds_extra_instances():
    found = expand(rrule="FREQ=DAILY;COUNT=2",
                   extra="RDATE:20260401T090000Z")
    assert starts(found) == ["2026-03-02T09:00:00Z",
                             "2026-03-03T09:00:00Z",
                             "2026-04-01T09:00:00Z"]


def _override(recurrence_id, dtstart, dtend=None, status=""):
    lines = ["BEGIN:VEVENT", "UID:e1", "DTSTAMP:20260101T000000Z",
             "RECURRENCE-ID:" + recurrence_id, "DTSTART:" + dtstart]
    if dtend:
        lines.append("DTEND:" + dtend)
    if status:
        lines.append("STATUS:" + status)
    lines.append("SUMMARY:Moved")
    lines.append("END:VEVENT")
    return ics.parse_events("\r\n".join(lines))[0]


def test_recurrence_id_override_moves_instance():
    moved = _override("20260303T090000Z", "20260303T110000Z",
                      "20260303T120000Z")
    found = expand(rrule="FREQ=DAILY;COUNT=3", overrides=[moved])
    assert starts(found) == ["2026-03-02T09:00:00Z",
                             "2026-03-03T11:00:00Z",
                             "2026-03-04T09:00:00Z"]
    assert found[1].is_override is True
    assert found[1].recurrence_id == utc("2026-03-03T09:00:00Z")


def test_cancelled_override_suppresses_instance():
    cancelled = _override("20260303T090000Z", "20260303T090000Z",
                          "20260303T100000Z", status="CANCELLED")
    found = expand(rrule="FREQ=DAILY;COUNT=3", overrides=[cancelled])
    assert starts(found) == ["2026-03-02T09:00:00Z",
                             "2026-03-04T09:00:00Z"]


def test_unparseable_rule_yields_base_instance_only():
    found = expand(rrule="FREQ=HOURLY;INTERVAL=1",
                   window=("2026-01-01T00:00:00Z",
                           "2027-01-01T00:00:00Z"))
    assert starts(found) == ["2026-03-02T09:00:00Z"]


def test_unbounded_rule_stops_at_window_end():
    found = expand(
        dtstart="DTSTART:20200101T090000Z",
        dtend="DTEND:20200101T100000Z",
        rrule="FREQ=DAILY",
        window=("2026-03-01T00:00:00Z", "2026-03-08T00:00:00Z"))
    assert starts(found) == [
        "2026-03-01T09:00:00Z", "2026-03-02T09:00:00Z",
        "2026-03-03T09:00:00Z", "2026-03-04T09:00:00Z",
        "2026-03-05T09:00:00Z", "2026-03-06T09:00:00Z",
        "2026-03-07T09:00:00Z"]


def test_all_day_series_expands_on_dates():
    found = expand(
        dtstart="DTSTART;VALUE=DATE:20260302",
        dtend="DTEND;VALUE=DATE:20260303",
        rrule="FREQ=WEEKLY;COUNT=2",
        window=("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"))
    assert starts(found) == ["2026-03-02T00:00:00Z",
                             "2026-03-09T00:00:00Z"]
    assert all(i.all_day for i in found)
    assert all(i.end_utc - i.start_utc == 86400000 for i in found)


def test_bysetpos_selects_full_month_before_dtstart_drop_ny():
    found = expand(
        dtstart="DTSTART;TZID=America/New_York:20250823T093000",
        dtend="DTEND;TZID=America/New_York:20250823T103000",
        rrule="FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=1;"
        "UNTIL=20271231T000000Z",
        window=("2025-08-01T00:00:00Z", "2025-11-01T00:00:00Z"))
    assert starts(found) == ["2025-08-23T13:30:00Z",
                             "2025-09-01T13:30:00Z",
                             "2025-10-01T13:30:00Z"]


def test_bysetpos_selects_full_month_before_dtstart_drop_london():
    found = expand(
        dtstart="DTSTART;TZID=Europe/London:20250323T090000",
        dtend="DTEND;TZID=Europe/London:20250323T100000",
        rrule="FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=2",
        window=("2025-03-01T00:00:00Z", "2025-07-01T00:00:00Z"))
    assert starts(found) == ["2025-03-23T09:00:00Z",
                             "2025-04-02T08:00:00Z",
                             "2025-05-02T08:00:00Z",
                             "2025-06-03T08:00:00Z"]


def test_window_end_is_exclusive():
    found = expand(
        dtstart="DTSTART:20260302T090000Z",
        dtend="DTEND:20260302T100000Z",
        rrule="FREQ=DAILY;COUNT=5",
        window=("2026-03-01T00:00:00Z", "2026-03-04T09:00:00Z"))
    assert starts(found) == ["2026-03-02T09:00:00Z",
                             "2026-03-03T09:00:00Z"]


def test_duration_property_sets_extent():
    found = expand(
        dtstart="DTSTART:20260302T090000Z",
        dtend="",
        rrule="",
        extra="DURATION:PT1H30M",
        window=("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"))
    assert len(found) == 1
    assert found[0].end_utc - found[0].start_utc == 5400000
