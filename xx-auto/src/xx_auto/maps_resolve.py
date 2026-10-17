"""Maps-tile handoff targets (port of Android MapsLaunch)."""

from __future__ import annotations

from enum import Enum


class Target(Enum):
    DRIVE = "drive"
    LAUNCHER = "launcher"
    NOT_INSTALLED = "not_installed"


def resolve(has_drive_action: bool, is_installed: bool) -> Target:
    """Explicit drive entry first, then any launcher, else not-installed."""
    if has_drive_action:
        return Target.DRIVE
    if is_installed:
        return Target.LAUNCHER
    return Target.NOT_INSTALLED
