"""Which drive tiles are drawn (port of Android DriveTiles, AU3)."""

from __future__ import annotations

from enum import Enum


class Tile(Enum):
    RADIO = "radio"
    AUDIOBOOK = "audiobook"
    MAPS = "maps"
    CALLS = "calls"


def visible(surface_quick_pick: bool = True, surface_nav: bool = True,
            surface_calls: bool = False) -> set[Tile]:
    """Tiles whose surface toggle is on, in design order."""
    out: set[Tile] = set()
    if surface_quick_pick:
        out.add(Tile.RADIO)
        out.add(Tile.AUDIOBOOK)
    if surface_nav:
        out.add(Tile.MAPS)
    if surface_calls:
        out.add(Tile.CALLS)
    return out


def tile_for(desktop_entry: str, identity: str = "") -> Tile | None:
    """The tile a player belongs to, so it can draw inverted while active."""
    name = f"{desktop_entry} {identity}".lower()
    if "audiobook" in name:
        return Tile.AUDIOBOOK
    if "radio" in name:
        return Tile.RADIO
    return None
