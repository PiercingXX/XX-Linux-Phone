"""Elapsed/duration formatting for the now-playing card (port of Elapsed)."""

from __future__ import annotations


def format_ms(ms: int) -> str:
    """`m:ss` under an hour, `h:mm:ss` from an hour up; negatives clamp."""
    total = max(0, int(ms)) // 1000
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def row(position_ms: int, duration_ms: int) -> str:
    """`elapsed / duration` with elapsed clamped to duration."""
    return f"{format_ms(min(position_ms, duration_ms))} / {format_ms(duration_ms)}"
