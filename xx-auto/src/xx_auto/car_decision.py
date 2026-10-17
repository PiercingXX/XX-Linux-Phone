"""Car-connect trigger decision (port of Android CarConnectDecision)."""

from __future__ import annotations

from enum import Enum


class Outcome(Enum):
    OPEN = "open"
    CLOSE = "close"
    IGNORE = "ignore"


def decide(action: str | None, device_address: str | None,
           saved_address: str | None, auto_launch: bool,
           was_auto_opened: bool,
           screen_in_front: bool = False) -> Outcome:
    """Decide what a BlueZ Device1 property change should do.

    ``action`` is "connected" or "disconnected". Linux has no overlay
    gate, so a connect for the saved device always opens (the Android
    OPEN_VIA_NOTIFICATION path collapses into OPEN).
    """
    if (not auto_launch or not (saved_address or "").strip()
            or not (device_address or "").strip()):
        return Outcome.IGNORE
    if device_address.strip().lower() != saved_address.strip().lower():
        return Outcome.IGNORE
    if action == "connected":
        return Outcome.IGNORE if screen_in_front else Outcome.OPEN
    if action == "disconnected":
        return Outcome.CLOSE if was_auto_opened else Outcome.IGNORE
    return Outcome.IGNORE
