"""Which MPRIS player is active (port of Android SessionPick)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlayerState:
    is_playing: bool = False
    has_item: bool = False


def active(states: list[PlayerState]) -> int | None:
    """Index of the active player (most-recent-first), or None."""
    for index, state in enumerate(states):
        if state.is_playing:
            return index
    for index, state in enumerate(states):
        if state.has_item:
            return index
    return None
