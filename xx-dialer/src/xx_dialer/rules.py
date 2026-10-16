"""Rules-screen logic: pure port of the Android Rules chrome, stdlib only.

Covers ``ui/PrecedenceRows.kt`` (the §6 table in evaluation order),
``ui/WindowChips.kt`` (window labels + day-mask math),
``ring/TileCountdown.kt`` (Expecting-a-call countdown) and the small
config ops the Rules page needs (bypass arm/clear, window writes,
observe-week seeding). The GTK view in ``ui_rules.py`` renders exactly
these shapes; headless tests pin them here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from xx_dialer import policy
from xx_dialer.store import should_offer_enforcement

DAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

IDLE_LABEL = "Expecting a call"
MAX_BYPASS_MS = 8 * 60 * 60 * 1000

BYPASS_CHOICES = (30, 120, 480)

LOG_LIMIT = 50

NEIGHBOR_PRESET = "neighbor_spoof"


@dataclass(frozen=True)
class PrecedenceRow:
    n: int
    condition: str
    verdict: str
    tone: str | None
    kind: str


def precedence_rows(unknown: policy.Window, business: policy.Window,
                    stir_action: policy.StirAction =
                    policy.StirAction.BLOCK,
                    hidden_policy: policy.HiddenCallerPolicy =
                    policy.HiddenCallerPolicy.UNKNOWN,
                    repeat_enabled: bool = True) -> list[PrecedenceRow]:
    """The §6 precedence table in the engine's evaluation order."""
    biz = time_range(business) if business.days_mask == policy.ALL_DAYS \
        else "%s %s" % (time_range(business), days_label(business.days_mask))
    unk = time_range(unknown) if unknown.days_mask == policy.ALL_DAYS \
        else "%s %s" % (time_range(unknown), days_label(unknown.days_mask))
    if stir_action == policy.StirAction.BLOCK:
        stir = ("STIR failed, forged caller ID", "Block", None, "block")
    elif stir_action == policy.StirAction.SILENCE:
        stir = ("STIR failed, forged caller ID", "Silence", None, "silence")
    else:
        stir = ("STIR failed, forged caller ID", "off", None, "info")
    if hidden_policy == policy.HiddenCallerPolicy.BLOCK:
        hidden = ("Hidden caller", "Block", None, "block")
    elif hidden_policy == policy.HiddenCallerPolicy.SILENCE:
        hidden = ("Hidden caller", "Silence", None, "silence")
    else:
        hidden = ("Hidden caller", "treated as unknown", None, "info")
    repeat = ("Repeat caller, twice in 15 min",
              "Rings through a silence", "unknown tone", "ring") \
        if repeat_enabled else ("Repeat caller", "off", None, "info")
    raw = [
        ("Emergency callback", "Ring, any time", "default", "ring"),
        ("Blocked group", "Block", None, "block"),
        ("Contact sends to voicemail", "to voicemail", None, "info"),
        ("Blocked numbers", "Block", None, "block"),
        ("Block pattern, contacts exempt", "Block", None, "block"),
        stir,
        ("Starred contact", "Ring, any time", "default", "ring"),
        ("Business, inside %s" % biz, "Ring", "default", "ring"),
        ("Business, outside %s" % biz, "Silence", None, "silence"),
        ("Saved contact", "Ring, any time", "default", "ring"),
        ("Unknown, you called in 48 h", "Ring, any time",
         "unknown tone", "ring"),
        hidden,
        ("Silence pattern", "Silence", None, "silence"),
        ("Unknown, inside %s" % unk, "Ring", "unknown tone", "ring"),
        ("Unknown, outside %s" % unk, "Silence", None, "silence"),
        repeat,
        ("Expecting a call", "Rings through a silence",
         "unknown tone", "ring"),
    ]
    return [PrecedenceRow(i + 1, condition, verdict, tone, kind)
            for i, (condition, verdict, tone, kind) in enumerate(raw)]


def day_bit(day_index: int) -> int:
    return 1 << day_index


def toggle_days(days_mask: int, day_index: int) -> int:
    return days_mask ^ day_bit(day_index)


def chips_from_mask(days_mask: int) -> list[bool]:
    return [bool(days_mask & day_bit(i)) for i in range(len(DAY_LABELS))]


def mask_from_chips(selected: list[bool]) -> int:
    mask = 0
    for i, on in enumerate(selected):
        if on:
            mask |= day_bit(i)
    return mask


def clock_label(minute_of_day: int) -> str:
    return "%02d:%02d" % (minute_of_day // 60, minute_of_day % 60)


def time_range(window: policy.Window) -> str:
    return "%s-%s" % (clock_label(window.start_minute),
                      clock_label(window.end_minute))


def days_label(days_mask: int) -> str:
    if days_mask == policy.ALL_DAYS:
        return "every day"
    on = chips_from_mask(days_mask)
    parts: list[str] = []
    i = 0
    while i < len(on):
        if not on[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(on) and on[j + 1]:
            j += 1
        parts.append(DAY_LABELS[i] if j == i
                     else "%s-%s" % (DAY_LABELS[i], DAY_LABELS[j]))
        i = j + 1
    return ", ".join(parts) if parts else "no days"


def window_line(window: policy.Window) -> str:
    return "%s, %s" % (time_range(window), days_label(window.days_mask))


def bypass_active_ms(until_ms: int | float | None,
                     now_ms: int) -> bool:
    """Live bypass only while in the future and within the 8 h bound."""
    if until_ms is None:
        return False
    try:
        remaining = int(until_ms) - now_ms
    except (TypeError, ValueError):
        return False
    return 0 < remaining <= MAX_BYPASS_MS


def countdown_duration(remaining_ms: int) -> str:
    """Remaining time as 4m or 1h59m; minutes round up, never 0m live."""
    if remaining_ms <= 0:
        return "0m"
    total_minutes = (remaining_ms + 59_999) // 60_000
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours > 0:
        return "%dh%02dm" % (hours, minutes)
    return "%dm" % minutes


def countdown_label(until_ms: int | float | None,
                    now_ms: int) -> str:
    if until_ms is None:
        return IDLE_LABEL
    try:
        remaining = int(until_ms) - now_ms
    except (TypeError, ValueError):
        return IDLE_LABEL
    if not 0 < remaining <= MAX_BYPASS_MS:
        return IDLE_LABEL
    return "Expecting, %s left" % countdown_duration(remaining)


def duration_label(minutes: int) -> str:
    if minutes % 60 == 0:
        return "%d h" % (minutes // 60)
    return "%d min" % minutes


def bypass_until_ms(config: dict) -> int | None:
    raw = config.get("bypass_until")
    if raw is None:
        return None
    try:
        value = int(float(str(raw).strip() or "x"))
    except (TypeError, ValueError):
        return None
    return value


def set_bypass(config: dict, minutes: int, now_ms: int) -> dict:
    config["bypass_until"] = int(now_ms + minutes * 60_000)
    config["bypass_duration_minutes"] = int(minutes)
    return config


def clear_bypass(config: dict) -> dict:
    config["bypass_until"] = None
    return config


def set_window(config: dict, key: str, start: int, end: int,
               days: int) -> dict:
    window = policy.Window(int(start), int(end), int(days))
    config[key] = {"start": window.start_minute, "end": window.end_minute,
                   "days": window.days_mask}
    return config


def set_enforcement(config: dict, enforcing: bool) -> dict:
    config["enforcement_mode"] = "enforcing" if enforcing else "observing"
    return config


def seed_observe_week(config: dict, now_ms: int) -> dict:
    """Start the observe week here, put-if-absent, never restarting it."""
    if not config.get("observe_week_end"):
        config["observe_week_end"] = float(now_ms + 7 * 24 * 60 * 60 * 1000)
    return config


def offer_enforcement(config: dict, now_ms: int) -> bool:
    return should_offer_enforcement(config.get("enforcement_mode"),
                                    now_ms, config.get("observe_week_end"))


def week_start_ms(now_ms: int) -> int:
    """Monday 00:00 local for the week containing now_ms."""
    local = datetime.fromtimestamp(now_ms / 1000)
    monday = datetime(local.year, local.month, local.day) - \
        timedelta(days=local.weekday())
    return int(monday.timestamp() * 1000)


def screen_tallies(store, since_ms: int) -> dict:
    """Tallies from the screen log: per-verdict, per-reason, and total."""
    by_verdict: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for row in store.last_screened(1000):
        if row.at < since_ms:
            continue
        by_verdict[row.verdict] = by_verdict.get(row.verdict, 0) + 1
        reason = row.reason or "—"
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {"by_verdict": by_verdict, "by_reason": by_reason,
            "total": sum(by_verdict.values())}


def test_number(service, raw: str, now=None) -> tuple[str, str]:
    """Run the REAL engine through the policy service's CheckNumber.

    Same method the D-Bus handler serves the shell with, so the shown
    verdict and reason are what a real call would get right now — but
    without writing the audit row, so test taps never inflate the
    tallies or the screening log.
    """
    return service.check_number(raw, now, log=False)
