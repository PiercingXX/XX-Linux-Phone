"""Day bucketing: UTC instant -> local calendar day in the sample's zone.

Port of `:core` DayBucketing.kt. The zone comes from the sample, never the
device, so a sample's day is stable across travel and DST boundaries.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


def local_day(instant: datetime, zone: ZoneInfo | str) -> date:
    """Return the local calendar date of instant in zone."""
    resolved = ZoneInfo(zone) if isinstance(zone, str) else zone
    return instant.astimezone(resolved).date()
