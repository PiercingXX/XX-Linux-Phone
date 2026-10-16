"""Pedometer status copy: pure model behind the thin GTK widgets.

Lane 1 honesty carries into the resident sampler: when the pedometer
is off or no sensor exists, steps show an honest "no source" line and
gap days keep rendering as gaps — never fake zeroes. Without a
hardware step counter the phone must be awake to count, and the
status line says so plainly. No GI, no I/O, so tests run headless;
ui/home and ui/profile only render these strings.
"""

from __future__ import annotations

from dataclasses import dataclass

SOURCE_HARDWARE = "hardware"
SOURCE_RESIDENT = "resident"

TITLE_OFF = "Step counting is off"
DETAIL_OFF = (
    "Turn on step counting to count steps while the phone is awake. "
    "Manual activities still count."
)
TITLE_NO_SOURCE = "No step source"
DETAIL_NO_SOURCE = (
    "No accelerometer or step counter found, so steps stay untracked "
    "— gap days render as gaps, never zeroes. Manual activities still count."
)
TITLE_ON_HARDWARE = "Step counting is on"
DETAIL_ON_HARDWARE = "Reading the hardware step counter every 15 minutes."
TITLE_ON_RESIDENT = "Step counting is on"
DETAIL_ON_RESIDENT = (
    "Counting steps while the phone is awake. Without a hardware step "
    "counter, steps are only counted while the phone is awake."
)


@dataclass(frozen=True)
class StepStatus:
    title: str
    detail: str
    enabled: bool
    has_source: bool
    source: str


def describe(
    collect_enabled: bool,
    hardware_available: bool,
    accelerometer_available: bool,
) -> StepStatus:
    """Honest pedometer line for the given opt-in + probe state."""
    if not collect_enabled:
        return StepStatus(
            title=TITLE_OFF, detail=DETAIL_OFF,
            enabled=False, has_source=False, source="",
        )
    if hardware_available:
        return StepStatus(
            title=TITLE_ON_HARDWARE, detail=DETAIL_ON_HARDWARE,
            enabled=True, has_source=True, source=SOURCE_HARDWARE,
        )
    if accelerometer_available:
        return StepStatus(
            title=TITLE_ON_RESIDENT, detail=DETAIL_ON_RESIDENT,
            enabled=True, has_source=True, source=SOURCE_RESIDENT,
        )
    return StepStatus(
        title=TITLE_NO_SOURCE, detail=DETAIL_NO_SOURCE,
        enabled=True, has_source=False, source="",
    )
