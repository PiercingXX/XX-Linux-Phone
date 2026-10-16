"""Codec tests: round-trip on hand-written real-world-style fixtures.

Google-, Synology-, and Outlook-style exports with folded lines, X- props,
VTIMEZONEs, VALARMs, and attendees. Untouched properties must re-emit
byte-identical; modeled edits must leave every other line untouched.
Headless: no display, no GTK import.
"""
from xx_calendar import ics
from xx_calendar.ics import (
    ICalProperty,
    VAlarm,
    alarm_for_minutes,
    escape_text,
    fold_line,
    format_trigger_minutes,
    new_event,
    parse_calendar,
    parse_events,
    parse_ical_datetime,
    parse_trigger_minutes,
    unescape_text,
    unfold_lines,
)

GOOGLE_EXPORT = (
    "BEGIN:VCALENDAR\r\n"
    "PRODID:-//Google Inc//Google Calendar 70.9054//EN\r\n"
    "VERSION:2.0\r\n"
    "CALSCALE:GREGORIAN\r\n"
    "METHOD:PUBLISH\r\n"
    "X-WR-CALNAME:Family\r\n"
    "X-WR-TIMEZONE:America/New_York\r\n"
    "BEGIN:VTIMEZONE\r\n"
    "TZID:America/New_York\r\n"
    "X-LIC-LOCATION:America/New_York\r\n"
    "BEGIN:DAYLIGHT\r\n"
    "TZOFFSETFROM:-0500\r\n"
    "TZOFFSETTO:-0400\r\n"
    "TZNAME:EDT\r\n"
    "DTSTART:19700308T020000\r\n"
    "RRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3\r\n"
    "END:DAYLIGHT\r\n"
    "BEGIN:STANDARD\r\n"
    "TZOFFSETFROM:-0400\r\n"
    "TZOFFSETTO:-0500\r\n"
    "TZNAME:EST\r\n"
    "DTSTART:19701101T020000\r\n"
    "RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11\r\n"
    "END:STANDARD\r\n"
    "END:VTIMEZONE\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART;TZID=America/New_York:20260817T140000\r\n"
    "DTEND;TZID=America/New_York:20260817T150000\r\n"
    "RRULE:FREQ=WEEKLY;BYDAY=MO\r\n"
    "DTSTAMP:20260801T120000Z\r\n"
    "UID:abc123@google.com\r\n"
    "CREATED:20260701T120000Z\r\n"
    "DESCRIPTION:Talk about the quarterly plan\\, bring snacks\\nSecond line\r\n"
    "  of the description that was folded by the exporter\r\n"
    "LAST-MODIFIED:20260702T120000Z\r\n"
    "LOCATION:Annex room\r\n"
    "SEQUENCE:0\r\n"
    "STATUS:CONFIRMED\r\n"
    "SUMMARY:Team sync with a very long title that the exporter folded acro\r\n"
    " ss the 75 octet boundary for us\r\n"
    "TRANSP:OPAQUE\r\n"
    "X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij\r\n"
    "BEGIN:VALARM\r\n"
    "TRIGGER:-PT30M\r\n"
    "ACTION:DISPLAY\r\n"
    "DESCRIPTION:This is an event reminder\r\n"
    "END:VALARM\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART;VALUE=DATE:20260820\r\n"
    "DTEND;VALUE=DATE:20260821\r\n"
    "DTSTAMP:20260801T120000Z\r\n"
    "UID:allday-1@google.com\r\n"
    "STATUS:CONFIRMED\r\n"
    "SUMMARY:Beach day\r\n"
    "TRANSP:TRANSPARENT\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

SYNOLOGY_EXPORT = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Synology//Synology Calendar//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:syno-9f8e7d6c-1234@example.com\r\n"
    "DTSTAMP:20260801T080000Z\r\n"
    "DTSTART;TZID=Europe/Berlin:20260818T090000\r\n"
    "DTEND;TZID=Europe/Berlin:20260818T093000\r\n"
    "SUMMARY:Zahnarzt\r\n"
    "DESCRIPTION:Bitte Versichertenkarte mitbringen\\nZweite Zeile\r\n"
    "LOCATION:Praxis Dr. Müller\\, Hauptstraße 12\r\n"
    "STATUS:CONFIRMED\r\n"
    "TRANSP:OPAQUE\r\n"
    "X-SYNOLOGY-CALENDAR-ID:abc123\r\n"
    "BEGIN:VALARM\r\n"
    "ACTION:DISPLAY\r\n"
    "TRIGGER:-PT1H\r\n"
    "DESCRIPTION:Erinnerung\r\n"
    "END:VALARM\r\n"
    "BEGIN:VALARM\r\n"
    "ACTION:DISPLAY\r\n"
    "TRIGGER:-P1D\r\n"
    "DESCRIPTION:Erinnerung\r\n"
    "END:VALARM\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

OUTLOOK_EXPORT = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Microsoft Corporation//Outlook 16.0 MIMEDIR//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:040000008200E00074C5B7101A82E00800000000ABCDEF1234567890ABCDEF12\r\n"
    " 34567890ABCDEF\r\n"
    "DTSTAMP:20260801T080000Z\r\n"
    "DTSTART:20260819T130000Z\r\n"
    "DTEND:20260819T140000Z\r\n"
    "SUMMARY:Quarterly review with a title that Outlook folded mid-UID and m\r\n"
    " id-title alike\r\n"
    "LOCATION:Building 4/2451\r\n"
    "DESCRIPTION:Agenda\\n\\n1. Numbers\\n2. Hiring\r\n"
    "STATUS:CONFIRMED\r\n"
    "TRANSP:OPAQUE\r\n"
    "ORGANIZER;CN=Jane Doe:mailto:jane@example.com\r\n"
    "ATTENDEE;CN=John Smith;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED:\r\n"
    " mailto:john@example.com\r\n"
    "ATTENDEE;CN=Sam Lee;ROLE=OPT-PARTICIPANT;PARTSTAT=TENTATIVE:\r\n"
    " mailto:sam@example.com\r\n"
    "X-MICROSOFT-CDO-BUSYSTATUS:BUSY\r\n"
    "X-MICROSOFT-CDO-IMPORTANCE:1\r\n"
    "BEGIN:VALARM\r\n"
    "TRIGGER:-PT15M\r\n"
    "ACTION:DISPLAY\r\n"
    "DESCRIPTION:Reminder\r\n"
    "END:VALARM\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_unfolding_joins_folded_lines():
    lines = unfold_lines(
        "SUMMARY:abc\r\n def\r\n\tghi\r\nUID:x\r\n")
    assert lines == ["SUMMARY:abcdefghi", "UID:x"]


def test_folding_respects_octet_limit_and_round_trips():
    line = "SUMMARY:" + "ä" * 60
    folded = fold_line(line)
    assert folded.startswith("SUMMARY:")
    assert "\r\n " in folded
    for physical in folded.split("\r\n"):
        assert len(physical.encode("utf-8")) <= 75
    assert unfold_lines(folded) == [line]


def test_escaping_round_trip():
    assert escape_text("a;b,c\\d\ne") == "a\\;b\\,c\\\\d\\ne"
    assert unescape_text("a\\;b\\,c\\\\d\\ne") == "a;b,c\\d\ne"


def test_parameters_with_quoting_and_bare_types():
    prop = ics.parse_property(
        'ATTENDEE;CN="Doe, Jane";ROLE=REQ-PARTICIPANT;X-FOO=a,b:'
        "mailto:j@example.com")
    assert prop is not None
    assert prop.name == "ATTENDEE"
    assert prop.param("CN") == ["Doe, Jane"]
    assert prop.param("ROLE") == ["REQ-PARTICIPANT"]
    assert prop.param("X-FOO") == ["a", "b"]
    assert prop.value == "mailto:j@example.com"
    bare = ics.parse_property("TEL;CELL;VOICE:+123")
    assert bare is not None and bare.param("TYPE") == ["CELL", "VOICE"]


def test_google_fixture_parses_and_round_trips_byte_identical():
    cal = parse_calendar(GOOGLE_EXPORT)
    assert len(cal.events) == 2
    assert len(cal.timezones) == 1
    assert "TZID:America/New_York" in cal.timezones[0]
    assert cal.serialize() == GOOGLE_EXPORT
    first = cal.events[0]
    assert first.uid() == "abc123@google.com"
    assert first.summary() == \
        "Team sync with a very long title that the exporter folded " \
        "across the 75 octet boundary for us"
    assert first.description() == \
        "Talk about the quarterly plan, bring snacks\nSecond line of " \
        "the description that was folded by the exporter"
    assert first.rrule() == "FREQ=WEEKLY;BYDAY=MO"
    assert first.status() == "CONFIRMED"
    assert first.first("X-GOOGLE-CONFERENCE") is not None
    assert first.reminder_minutes() == [30]
    all_day = cal.events[1]
    assert all_day.dtstart() == (1787184000000, True, "")
    assert all_day.transp() == "TRANSPARENT"


def test_synology_fixture_umlauts_and_two_alarms():
    cal = parse_calendar(SYNOLOGY_EXPORT)
    assert cal.serialize() == SYNOLOGY_EXPORT
    event = cal.events[0]
    assert event.summary() == "Zahnarzt"
    assert event.location() == "Praxis Dr. Müller, Hauptstraße 12"
    assert event.description() == \
        "Bitte Versichertenkarte mitbringen\nZweite Zeile"
    assert event.first("X-SYNOLOGY-CALENDAR-ID") is not None
    assert sorted(event.reminder_minutes()) == [60, 1440]
    start = event.dtstart()
    assert start is not None and start[2] == "Europe/Berlin"


def test_outlook_fixture_folded_uid_and_attendees():
    cal = parse_calendar(OUTLOOK_EXPORT)
    assert cal.serialize() == OUTLOOK_EXPORT
    event = cal.events[0]
    assert event.uid() == (
        "040000008200E00074C5B7101A82E00800000000ABCDEF1234567890ABCDEF12"
        "34567890ABCDEF")
    organizer = event.organizer()
    assert organizer is not None and organizer.email == "jane@example.com"
    assert organizer.cn == "Jane Doe"
    attendees = event.attendees()
    assert [(a.email, a.role, a.partstat) for a in attendees] == [
        ("john@example.com", "REQ-PARTICIPANT", "ACCEPTED"),
        ("sam@example.com", "OPT-PARTICIPANT", "TENTATIVE")]
    assert event.first("X-MICROSOFT-CDO-BUSYSTATUS") is not None
    assert event.reminder_minutes() == [15]


def test_modeled_edit_leaves_other_lines_untouched():
    cal = parse_calendar(GOOGLE_EXPORT)
    event = cal.events[0]
    event_text = event.serialize()
    before = [line for line in event_text.split("\r\n")
              if not line.startswith("SUMMARY")
              and not line.startswith(" ss ")]
    event.set_text("SUMMARY", "Short")
    out = event.serialize()
    for line in before:
        assert line in out.split("\r\n")
    assert "SUMMARY:Short" in out
    reparsed = parse_events(out)[0]
    assert reparsed.summary() == "Short"
    assert reparsed.description() == event.description()
    full = cal.serialize()
    assert "SUMMARY:Short" in full
    assert full.count("BEGIN:VEVENT") == 2


def test_valarm_round_trip_and_builder():
    alarm = VAlarm(props=[
        ICalProperty(name="ACTION", value="DISPLAY", raw="ACTION:DISPLAY"),
        ICalProperty(name="TRIGGER", value="-PT30M", raw="TRIGGER:-PT30M"),
    ])
    assert alarm.trigger_minutes() == 30
    assert alarm.serialize() == "BEGIN:VALARM\r\nACTION:DISPLAY\r\n" \
        "TRIGGER:-PT30M\r\nEND:VALARM"
    built = alarm_for_minutes(10)
    assert built.trigger_minutes() == 10
    assert "TRIGGER:-PT10M" in built.serialize()


def test_trigger_minutes_table():
    assert parse_trigger_minutes("-PT30M") == 30
    assert parse_trigger_minutes("PT0S") == 0
    assert parse_trigger_minutes("-P1D") == 1440
    assert parse_trigger_minutes("-PT1H") == 60
    assert parse_trigger_minutes("PT15M") == -15
    assert parse_trigger_minutes("20260819T130000Z") is None
    assert format_trigger_minutes(30) == "-PT30M"
    assert format_trigger_minutes(0) == "PT0S"
    assert format_trigger_minutes(-5) == "PT5M"


def test_datetime_parsing_forms():
    assert parse_ical_datetime("20260820", None) == \
        (1787184000000, True, "")
    prop = ics.parse_property("DTSTART;VALUE=DATE:20260820")
    assert prop is not None
    assert parse_ical_datetime(prop.value, prop) == \
        (1787184000000, True, "")
    utc = parse_ical_datetime("20260819T130000Z", None)
    assert utc is not None and utc[1] is False and utc[2] == ""
    tzid_prop = ics.parse_property(
        "DTSTART;TZID=America/New_York:20260817T140000")
    assert tzid_prop is not None
    parsed = parse_ical_datetime(tzid_prop.value, tzid_prop)
    assert parsed == (1786989600000, False, "America/New_York")
    assert parse_ical_datetime("not-a-date", None) is None
    assert parse_ical_datetime("", None) is None


def test_unknown_tzid_falls_back_open():
    prop = ics.parse_property("DTSTART;TZID=Made/Up:20260817T140000")
    assert prop is not None
    parsed = parse_ical_datetime(prop.value, prop)
    assert parsed is not None
    assert parsed[0] == parse_ical_datetime(
        "20260817T140000", None)[0]


def test_new_event_builds_folded_parseable_vevent():
    event = new_event(uid="new-1@x", summary="Lunch",
                      start_millis=1786989600000,
                      end_millis=1786993200000,
                      tzid="America/New_York")
    text = event.serialize()
    reparsed = parse_events(text)[0]
    assert reparsed.uid() == "new-1@x"
    assert reparsed.summary() == "Lunch"
    assert reparsed.dtstart() == (1786989600000, False, "America/New_York")
    all_day = new_event(uid="ad-1@x", start_millis=1787184000000,
                        end_millis=1787270400000, all_day=True)
    assert all_day.dtstart() == (1787184000000, True, "")


def test_other_components_pass_through_verbatim():
    text = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//X//Y//EN\r\n"
            "BEGIN:VTODO\r\nUID:todo-1\r\nSUMMARY:Buy milk\r\nEND:VTODO\r\n"
            "BEGIN:VEVENT\r\nUID:e1\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260819T130000Z\r\nDTEND:20260819T140000Z\r\n"
            "SUMMARY:Real\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    cal = parse_calendar(text)
    assert len(cal.events) == 1
    assert cal.serialize() == text


def test_recurrence_id_and_sequence_survive():
    text = ("BEGIN:VEVENT\r\nUID:e1\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260819T130000Z\r\nDTEND:20260819T140000Z\r\n"
            "RECURRENCE-ID:20260826T130000Z\r\nSEQUENCE:2\r\n"
            "STATUS:CONFIRMED\r\nSUMMARY:Moved\r\nEND:VEVENT")
    event = parse_events(text)[0]
    assert event.recurrence_id() == (1787749200000, False, "")
    assert event.sequence() == 2
    assert event.serialize().split("\r\n") == text.split("\r\n")
