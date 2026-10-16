"""Port of DayBucketingTest: UTC instant -> local day in the sample zone."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from xx_vitals.core.bucketing import local_day

NEW_YORK = ZoneInfo("America/New_York")


def _instant(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def test_morning_utc_same_local_day():
    assert local_day(_instant("2026-03-05T09:00:00Z"), NEW_YORK).isoformat() == "2026-03-05"


def test_late_night_utc_previous_local_day():
    assert local_day(_instant("2026-03-05T03:00:00Z"), NEW_YORK).isoformat() == "2026-03-04"


def test_midnight_local_is_day_start():
    assert local_day(_instant("2026-03-05T05:00:00Z"), NEW_YORK).isoformat() == "2026-03-05"


def test_same_instant_differs_by_zone():
    instant = _instant("2026-03-05T09:00:00Z")
    assert local_day(instant, ZoneInfo("Asia/Tokyo")).isoformat() == "2026-03-05"
    late = _instant("2026-03-05T03:00:00Z")
    assert local_day(late, ZoneInfo("Asia/Tokyo")).isoformat() == "2026-03-05"


def test_zone_comes_from_sample():
    assert local_day(_instant("2026-03-05T03:00:00Z"), NEW_YORK).isoformat() == "2026-03-04"


def test_spring_forward_boundaries():
    assert local_day(_instant("2026-03-08T04:59:59Z"), NEW_YORK).isoformat() == "2026-03-07"
    assert local_day(_instant("2026-03-08T05:00:00Z"), NEW_YORK).isoformat() == "2026-03-08"
    assert local_day(_instant("2026-03-08T07:00:00Z"), NEW_YORK).isoformat() == "2026-03-08"
    assert local_day(_instant("2026-03-08T09:00:00Z"), NEW_YORK).isoformat() == "2026-03-08"


def test_fall_back_boundaries():
    assert local_day(_instant("2026-11-01T03:59:59Z"), NEW_YORK).isoformat() == "2026-10-31"
    assert local_day(_instant("2026-11-01T04:00:00Z"), NEW_YORK).isoformat() == "2026-11-01"
    assert local_day(_instant("2026-11-01T06:00:00Z"), NEW_YORK).isoformat() == "2026-11-01"
    assert local_day(_instant("2026-11-01T08:00:00Z"), NEW_YORK).isoformat() == "2026-11-01"


def test_string_zone_matches_zone_object():
    instant = _instant("2026-03-05T09:00:00Z")
    assert local_day(instant, "America/New_York") == local_day(instant, NEW_YORK)


def test_unknown_zone_raises():
    with pytest.raises(Exception):
        local_day(_instant("2026-03-05T09:00:00Z"), "Not/AZone")


def test_utc_stability():
    instant = datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc)
    assert local_day(instant, NEW_YORK).isoformat() == "2026-03-05"
