"""Differential fuzz: xx_calendar.recurrence vs python-dateutil.

Moved from the lane-root ``rrule_fuzz.py`` attached by the orchestrator;
the rule generator, window, and comparison below are byte-for-byte its
logic so seed-reproducible runs stay comparable. Run from the repo root::

    python3 tools/rrule_fuzz.py [srcpath] [seed] [count]

``dateutil`` is intentionally NOT a dependency (XXWM_RULES.md forbids new
dependencies and the xx-wm gate venv cannot import it), so this script
prints ``SKIP: python-dateutil is not installed`` and exits 0 when the
import fails. The orchestrator runs it in an environment that has it.

Window convention: the harness queries dateutil with ``inc=True``, so an
instance starting exactly at the window end counts as a harness-side
mismatch. That is the harness's inclusive bound, not an engine bug:
:func:`xx_calendar.recurrence.expand_vevent` documents and keeps the
half-open ``[start, end)`` window.
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta  # noqa: F401 (kept for parity)
from zoneinfo import ZoneInfo

try:
    from dateutil.rrule import rrulestr
    HAS_DATEUTIL = True
except ImportError:  # gate venv: skip, the orchestrator runs this instead
    HAS_DATEUTIL = False


def main(argv: list[str]) -> int:
    if not HAS_DATEUTIL:
        print("SKIP: python-dateutil is not installed")
        return 0
    from pathlib import Path

    default_src = str(Path(__file__).resolve().parent.parent / "src")
    sys.path.insert(0, argv[1] if len(argv) > 1 else default_src)
    from xx_calendar import ics, recurrence

    rng = random.Random(int(argv[2]) if len(argv) > 2 else 7)
    DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]

    def rand_rule():
        freq = rng.choice(["DAILY", "WEEKLY", "MONTHLY", "YEARLY"])
        parts = [f"FREQ={freq}"]
        if rng.random() < .5:
            parts.append(f"INTERVAL={rng.randint(1, 4)}")
        if freq in ("WEEKLY",) and rng.random() < .7:
            parts.append("BYDAY=" + ",".join(sorted(rng.sample(DAYS, rng.randint(1, 3)), key=DAYS.index)))
        if freq == "MONTHLY":
            k = rng.random()
            if k < .35:
                parts.append(f"BYMONTHDAY={rng.choice([1, 15, 28, 29, 30, 31, -1, -2])}")
            elif k < .7:
                parts.append(f"BYDAY={rng.choice(['1', '2', '3', '4', '-1', '-2'])}{rng.choice(DAYS)}")
            elif k < .85:
                parts.append("BYDAY=MO,TU,WE,TH,FR")
                parts.append(f"BYSETPOS={rng.choice([1, -1, 2])}")
        if freq == "YEARLY" and rng.random() < .6:
            parts.append(f"BYMONTH={rng.randint(1, 12)}")
            if rng.random() < .5:
                parts.append(f"BYDAY={rng.choice(['1', '2', '-1'])}{rng.choice(DAYS)}")
            else:
                parts.append(f"BYMONTHDAY={rng.choice([1, 10, 29, 31])}")
        if rng.random() < .3:
            parts.append(f"COUNT={rng.randint(1, 20)}")
        elif rng.random() < .3:
            parts.append("UNTIL=20271231T000000Z")
        return ";".join(parts)

    fails = checked = skipped = 0
    for i in range(int(argv[3]) if len(argv) > 3 else 400):
        tzname = rng.choice(["America/New_York", "Europe/London"])
        zone = ZoneInfo(tzname)
        start = datetime(2025, rng.randint(1, 12), rng.randint(1, 28), rng.choice([0, 1, 2, 9, 23]), rng.choice([0, 30]))
        rule = rand_rule()
        text = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:f%d\r\n"
                "DTSTART;TZID=%s:%s\r\nDURATION:PT1H\r\nRRULE:%s\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
                % (i, tzname, start.strftime("%Y%m%dT%H%M%S"), rule))
        ws = datetime(2025, 1, 1, tzinfo=ZoneInfo("UTC"))
        we = datetime(2028, 1, 1, tzinfo=ZoneInfo("UTC"))
        ref = rrulestr(rule, dtstart=start.replace(tzinfo=zone))
        ref_list = ref.between(ws, we, inc=True)
        dt_aware = start.replace(tzinfo=zone)
        if "COUNT=" in rule and (not ref_list or ref_list[0] != dt_aware):
            skipped += 1
            continue  # DTSTART not in the rule set: RFC-undefined COUNT semantics
        want = {int(d.timestamp() * 1000) for d in ref_list} - {int(dt_aware.timestamp() * 1000)}
        ev = ics.parse_events(text)[0]
        got_inst = recurrence.expand_vevent(ev, int(ws.timestamp() * 1000), int(we.timestamp() * 1000))
        got = {x.start_utc for x in got_inst} - {int(dt_aware.timestamp() * 1000)}
        checked += 1
        if got != want:
            fails += 1
            if fails <= 6:
                miss = sorted(want - got)[:3]
                extra = sorted(got - want)[:3]

                def fmt(ms):
                    return datetime.fromtimestamp(ms / 1000, zone).strftime("%Y-%m-%d %H:%M %a")

                print(f"MISMATCH {tzname} start={start} rule={rule}\n  missing={[fmt(m) for m in miss]} extra={[fmt(e) for e in extra]} (want {len(want)} got {len(got)})")
    print(f"checked={checked} skipped={skipped} fails={fails}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
