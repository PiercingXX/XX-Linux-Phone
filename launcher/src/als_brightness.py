from __future__ import annotations

import math
from pathlib import Path

from gi.repository import GLib

# Ambient light sensor via Linux IIO subsystem.
# Kernel path: /sys/bus/iio/devices/iio:deviceN/in_illuminance_input (lux)
# Verify the FP5's sensor node with: ls /sys/bus/iio/devices/
# Backlight: /sys/class/backlight/*/brightness + max_brightness

_IIO_GLOB = '/sys/bus/iio/devices/iio:device*/in_illuminance_input'
_BACKLIGHT_GLOB = '/sys/class/backlight/*/brightness'
_POLL_INTERVAL_MS = 5000
_CHANGE_THRESHOLD = 0.12  # only apply if brightness changes by ≥12%

_LUX_CURVE: list[tuple[float, float]] = [
    (0.0,    0.02),
    (5.0,    0.05),
    (50.0,   0.15),
    (200.0,  0.35),
    (600.0,  0.60),
    (1500.0, 0.80),
    (5000.0, 1.00),
]


def _lux_to_ratio(lux: float) -> float:
    """Map ambient lux to a 0.0–1.0 brightness ratio using a log-ish curve."""
    lux = max(0.0, lux)
    for i in range(len(_LUX_CURVE) - 1):
        lx0, r0 = _LUX_CURVE[i]
        lx1, r1 = _LUX_CURVE[i + 1]
        if lux <= lx1:
            t = (lux - lx0) / (lx1 - lx0)
            return r0 + t * (r1 - r0)
    return _LUX_CURVE[-1][1]


def _find_path(glob: str) -> Path | None:
    matches = list(Path('/').glob(glob.lstrip('/')))
    return matches[0] if matches else None


class ALSBrightness:
    """
    Reads ambient light sensor via IIO sysfs and adjusts display brightness.
    Enable/disable with `start()` / `stop()`.
    Falls back silently if sensor or backlight sysfs node is absent.
    """

    def __init__(self) -> None:
        self._sensor_path: Path | None = _find_path(_IIO_GLOB)
        self._backlight_path: Path | None = _find_path(_BACKLIGHT_GLOB)
        self._max_brightness: int = self._read_max()
        self._last_ratio: float = -1.0
        self._timer_id: int | None = None
        self._enabled: bool = False

    def _read_max(self) -> int:
        if self._backlight_path is None:
            return 255
        max_path = self._backlight_path.parent / 'max_brightness'
        try:
            return int(max_path.read_text().strip())
        except (OSError, ValueError):
            return 255

    def available(self) -> bool:
        return self._sensor_path is not None and self._backlight_path is not None

    def start(self) -> None:
        if self._enabled:
            return
        self._enabled = True
        self._poll()
        if self._timer_id is None:
            self._timer_id = GLib.timeout_add(_POLL_INTERVAL_MS, self._poll)

    def stop(self) -> None:
        self._enabled = False
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _poll(self) -> bool:
        if not self._enabled:
            return False
        lux = self._read_lux()
        if lux is None:
            return True

        ratio = _lux_to_ratio(lux)
        if abs(ratio - self._last_ratio) >= _CHANGE_THRESHOLD:
            self._apply(ratio)
            self._last_ratio = ratio
        return True

    def _read_lux(self) -> float | None:
        if self._sensor_path is None:
            return None
        try:
            return float(self._sensor_path.read_text().strip())
        except (OSError, ValueError):
            return None

    def _apply(self, ratio: float) -> None:
        if self._backlight_path is None:
            return
        value = max(1, round(ratio * self._max_brightness))
        try:
            self._backlight_path.write_text(str(value))
        except OSError:
            pass

    @property
    def enabled(self) -> bool:
        return self._enabled
