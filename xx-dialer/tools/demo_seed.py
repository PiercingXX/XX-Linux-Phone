#!/usr/bin/env python3
"""Seed a fake HOME with realistic xx-dialer data for screenshots/tests.

Usage: demo_seed.py <homedir>

Fills <homedir> through the app's real store/config APIs: contact
mirror rows, tiers, stars, call-log rows with Android-style
millisecond timestamps, screening-log rows, pattern + blocklist rows
and app config. Mirrors the Android reference shots: Mara Voss,
Ridgeline Dental, Mom, unknown/silenced numbers, a blocked repeat
caller and a pattern-silenced row.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                               "common"))

from xx_dialer import config as app_config  # noqa: E402
from xx_dialer import applog  # noqa: E402
from xx_dialer.service import ensure_dirs  # noqa: E402
from xx_dialer.store import (  # noqa: E402
    BIZ_TIER,
    BLOCKED_GROUP,
    CALL_TYPE_INCOMING,
    CALL_TYPE_MISSED,
    CALL_TYPE_OUTGOING,
    CallRow,
    MirrorRow,
    PatternRow,
    ScreenRow,
    Store,
)

MARA = "+16145550199"
RIDGE = "+16145550177"
MOM = "+16145550155"
DEZ = "+16145550148"
ADA = "+16145550142"
UNKNOWN_VM = "+16145550109"
PACKAGE = "+13805550110"
PRIYA = "+16145550163"
BLOCKED = "+16145550133"
PATTERN_HIT = "+16145550137"

MIN = 60_000


def seed(home: str) -> None:
    from pathlib import Path

    base = Path(home)
    ensure_dirs(base)
    now = int(time.time() * 1000)

    settings = app_config.load(base)
    settings.update({
        "enforcement_mode": "enforcing",
        "observe_week_end": float(now - 8 * 24 * 60 * MIN),
        "unknown_window": {"start": 540, "end": 1140, "days": 127},
        "business_window": {"start": 540, "end": 1140, "days": 62},
        "repeat_caller_enabled": True,
        "group_recents": True,
        "voicemail_number": "+16145550199",
    })
    app_config.save(settings, base)

    db = Store(base / ".local" / "share" / "xx-dialer" / "calls.db")
    try:
        db.upsert_mirror([
            MirrorRow("uid-mara", MARA, "Mara Voss", True, None, False,
                      now - MIN),
            MirrorRow("uid-ridge", RIDGE, "Ridgeline Dental", False, None,
                      False, now - MIN),
            MirrorRow("uid-mom", MOM, "Mom", False, None, False,
                      now - MIN),
            MirrorRow("uid-dez", DEZ, "Dez Okafor", False, None, False,
                      now - MIN),
            MirrorRow("uid-ada", ADA, "Ada Lovelace", False, None, False,
                      now - MIN),
            MirrorRow("uid-zed", BLOCKED, "Zed Spam", False, None, False,
                      now - MIN),
        ])
        db.add_star("uid-mara")
        db.upsert_tier("uid-ridge", BIZ_TIER, now - MIN)
        db.upsert_tier("uid-zed", BLOCKED_GROUP, now - MIN)
        db.add_blocked(BLOCKED)
        db.add_blocked("+16145550134")
        db.add_pattern(PatternRow("1614555013", 1, "silence",
                                 created_at=now - 60 * MIN))
        db.add_pattern(PatternRow("1425555", 4, "block",
                                 created_at=now - 60 * MIN))

        calls = [
            (PATTERN_HIT, CALL_TYPE_INCOMING, 0, None, 1),
            (PATTERN_HIT, CALL_TYPE_INCOMING, 0, None, 6),
            (BLOCKED, CALL_TYPE_INCOMING, 0, None, 11),
            (BLOCKED, CALL_TYPE_INCOMING, 0, None, 14),
            (BLOCKED, CALL_TYPE_INCOMING, 0, None, 17),
            (MARA, CALL_TYPE_INCOMING, 161, "Mara Voss", 21),
            (MARA, CALL_TYPE_OUTGOING, 252, "Mara Voss", 48),
            (MOM, CALL_TYPE_MISSED, 0, "Mom", 69),
            (RIDGE, CALL_TYPE_INCOMING, 32, "Ridgeline Dental", 98),
            (DEZ, CALL_TYPE_OUTGOING, 252, "Dez Okafor", 132),
            (ADA, CALL_TYPE_INCOMING, 41, "Ada Lovelace", 180),
            (UNKNOWN_VM, CALL_TYPE_INCOMING, 18, None, 190),
            (PACKAGE, CALL_TYPE_INCOMING, 0, None, 220),
            (PRIYA, CALL_TYPE_INCOMING, 0, None, 255),
        ]
        for number, kind, duration, name, ago in calls:
            db.add_call(CallRow(date=now - ago * MIN, number=number,
                                call_type=kind, duration=duration,
                                cached_name=name))

        screens = [
            (PATTERN_HIT, "Silence", "pattern 161-455-5013X", "enforcing",
             False, 2),
            (BLOCKED, "Block", "blocked number", "enforcing", False, 12),
            (MARA, "Ring", "starred contact", "enforcing", False, 22),
            (UNKNOWN_VM, "Silence", "unknown caller, after 19:00",
             "enforcing", False, 191),
            (PACKAGE, "Silence", "unknown caller", "observed", False, 221),
            (RIDGE, "Ring", "business, inside hours", "enforcing",
             True, 99),
        ]
        for e164, verdict, reason, mode, answered, ago in screens:
            db.log_screen(ScreenRow(at=now - ago * MIN, verdict=verdict,
                                    reason=reason, e164=e164, mode=mode,
                                    answered=answered))
        db.set_recents_viewed(now - 300 * MIN)
    finally:
        db.close()

    applog.write("calls", "seeded demo data at %d" % now, base)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: demo_seed.py <homedir>", file=sys.stderr)
        return 2
    seed(argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
