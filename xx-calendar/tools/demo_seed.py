"""Seed a HOME dir with realistic calendar data via the real store APIs."""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
SRC = TOOL_DIR.parent / "src"
sys.path.insert(0, str(SRC))
COMMON = TOOL_DIR.parent.parent / "common"
sys.path.insert(0, str(COMMON))

UTC = dt.timezone.utc


def _ms(moment: dt.datetime) -> int:
    return int(moment.timestamp() * 1000)


def _vevent(uid: str, summary: str, start: dt.datetime,
            end: dt.datetime | None = None, all_day: bool = False,
            rrule: str = "", location: str = "",
            description: str = "", reminder: int | None = None,
            status: str = "") -> str:
    from xx_calendar import ics as ics_module

    lines = ["BEGIN:VEVENT", f"UID:{uid}", "DTSTAMP:20260101T000000Z"]
    if all_day:
        lines.append(f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}")
        stop = end or (start + dt.timedelta(days=1))
        lines.append(f"DTEND;VALUE=DATE:{stop.strftime('%Y%m%d')}")
    else:
        lines.append(f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}")
        if end is not None and end > start:
            lines.append(f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}")
    lines.append(f"SUMMARY:{summary}")
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        lines.append(f"DESCRIPTION:{description}")
    if rrule:
        lines.append(f"RRULE:{rrule}")
    if status:
        lines.append(f"STATUS:{status}")
    text = "\r\n".join(lines + ["END:VEVENT"])
    if reminder:
        alarm = ics_module.alarm_for_minutes(reminder).serialize()
        text = text.replace("END:VEVENT", alarm + "\r\nEND:VEVENT")
    return text


def seed_home(home: str) -> str:
    from xx_calendar.service import CalendarService
    from xx_calendar.store import (
        CalendarRecord,
        CalendarStore,
        default_db_path,
        ensure_store_dir,
    )

    db_path = default_db_path(home)
    ensure_store_dir(db_path)
    try:
        os.unlink(db_path)
    except OSError:
        pass
    store = CalendarStore(db_path)
    service = CalendarService(store, start_worker=False)
    try:
        for record in (CalendarRecord(id="local", display_name="On This Phone",
                                      account_name="device"),
                       CalendarRecord(id="work", display_name="Work",
                                      account_name="office"),
                       CalendarRecord(id="family", display_name="Family",
                                      account_name="home")):
            try:
                store.create_calendar(record)
            except Exception:
                pass
        events = [
            ("standup-1", "work", "Daily standup",
             dt.datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
             dt.datetime(2026, 8, 3, 9, 15, tzinfo=UTC),
             {"rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "reminder": 10}),
            ("dentist-1", "local", "Ridgeline Dental cleaning with a very long title that must ellipsize on a 360 pixel phone screen",
             dt.datetime(2026, 8, 4, 10, 30, tzinfo=UTC),
             dt.datetime(2026, 8, 4, 11, 30, tzinfo=UTC),
             {"location": "Ridgeline Dental", "reminder": 60}),
            ("lake-trip", "family", "Family trip to the lake",
             dt.datetime(2026, 8, 5, tzinfo=UTC),
             dt.datetime(2026, 8, 8, tzinfo=UTC),
             {"all_day": True}),
            ("lunch-1", "work", "Team lunch with Mara Voss and Dez Okafor at the downtown bistro",
             dt.datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
             dt.datetime(2026, 8, 10, 13, 0, tzinfo=UTC),
             {"location": "Downtown bistro", "reminder": 30}),
            ("birthday-1", "family", "Mom's birthday dinner",
             dt.datetime(2026, 8, 15, 19, 30, tzinfo=UTC),
             dt.datetime(2026, 8, 15, 21, 0, tzinfo=UTC),
             {"description": "Bring cake. Conferencing https://meet.example.com/birthday",
              "reminder": 120}),
            ("dayoff-1", "local", "Day off",
             dt.datetime(2026, 8, 21, tzinfo=UTC),
             dt.datetime(2026, 8, 22, tzinfo=UTC),
             {"all_day": True}),
            ("review-1", "work", "Sprint review and planning",
             dt.datetime(2026, 10, 16, 14, 0, tzinfo=UTC),
             dt.datetime(2026, 10, 16, 15, 0, tzinfo=UTC),
             {"location": "Room 3B", "reminder": 15}),
            ("call-1", "local", "Call Ada Lovelace about the trip",
             dt.datetime(2026, 10, 17, 9, 0, tzinfo=UTC),
             dt.datetime(2026, 10, 17, 9, 30, tzinfo=UTC),
             {"reminder": 10}),
        ]
        for uid, cal, title, start, end, extra in events:
            text = _vevent(uid, title, start, end,
                           all_day=bool(extra.get("all_day")),
                           rrule=str(extra.get("rrule", "")),
                           location=str(extra.get("location", "")),
                           description=str(extra.get("description", "")),
                           reminder=extra.get("reminder"))
            try:
                service.create(cal, text)
            except Exception as exc:
                print(f"seed: {uid} skipped: {exc}", file=sys.stderr)
        settings_dir = Path(home) / ".config" / "xx-calendar"
        settings_dir.mkdir(parents=True, exist_ok=True)
    finally:
        service.stop()
        store.close()
    return db_path


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        print("usage: demo_seed.py HOME_DIR", file=sys.stderr)
        return 2
    path = seed_home(args[0])
    print(f"seeded {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
