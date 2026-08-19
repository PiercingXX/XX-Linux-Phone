"""
Display power management.

Backlight is driven with brightnessctl; wlopm (wlr-output-power-management) is
used for DPMS when present but is optional. Blank state is tracked internally
(self._blanked) — wlopm is not required to be installed, so we never depend on
querying it to wake back up.

Wake sources and the output name are auto-detected, not hardcoded: every
readable /dev/input/event* node is watched (reads are non-exclusive) and
_on_event filters by code, so power/touch/volume wake the display whatever
event number they land on and whatever the device. This replaces an earlier
FP5-specific map (event0-3 / HWCOMPOSER-1) that trapped other devices dark.

Hardware shortcuts (handled via evdev):
  - Power short press                  → blank / wake (toggle)
  - Power long-press (≥600ms)          → on_power_menu()
  - Power + Volume Down simultaneously → on_screenshot()
  - Volume Up / Down                   → +5% / -5% media volume
  - Touch (while blanked)              → wake
"""
from __future__ import annotations

import datetime
import os
import struct
import subprocess
import threading
import time
from typing import Callable

from gi.repository import GLib

_EV_FMT  = 'llHHi'
_EV_SIZE = struct.calcsize(_EV_FMT)

EV_KEY    = 1
EV_ABS    = 3
ABS_MT_TRACKING_ID = 57   # touch-down when value >= 0
KEY_POWER     = 116
KEY_VOLUMEDOWN = 114
KEY_VOLUMEUP   = 115

_POWER_LONG_PRESS_MS = 600   # ms hold to trigger power menu instead of blank

# Wake devices and the output name used to be hardcoded per-device (the FP5's
# event0-3 / HWCOMPOSER-1). That silently traps every other device in the dark:
# the backlight blanks but the wrong input nodes / output name mean nothing can
# wake it. Both are now auto-detected at runtime — see _all_event_devices() and
# _detect_output().
_IDLE_SECS = 60


def _all_event_devices() -> list[str]:
    """Every readable /dev/input/event* node. Reading is non-exclusive (no
    EVIOCGRAB), so watching them all is harmless — _on_event filters by code."""
    import glob
    return sorted(d for d in glob.glob('/dev/input/event*') if os.access(d, os.R_OK))


def _detect_output() -> str:
    """First output wlopm reports; '*' (all outputs) if it can't be parsed —
    wlopm accepts '*' for on/off, so blanking still works either way."""
    try:
        out = subprocess.check_output(
            ['wlopm'], env=_WL_ENV, timeout=2, text=True, stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] in ('on', 'off'):
                return parts[0]
    except Exception:
        pass
    return '*'


_WLOPM_OUTPUT: str | None = None


def _output() -> str:
    global _WLOPM_OUTPUT
    if _WLOPM_OUTPUT is None:
        _WLOPM_OUTPUT = _detect_output()
    return _WLOPM_OUTPUT

_WL_ENV = {
    **os.environ,
    'WAYLAND_DISPLAY': 'wayland-0',
    'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}',
}


# ---------------------------------------------------------------------------
# Display control helpers
# ---------------------------------------------------------------------------

def _wlopm(mode: str) -> bool:
    try:
        r = subprocess.run(
            ['wlopm', f'--{mode}', _output()],
            env=_WL_ENV, timeout=3,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except Exception:
        return False


def _brightnessctl(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ['brightnessctl'] + list(args),
            text=True, timeout=2, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _take_screenshot() -> None:
    screenshots_dir = os.path.expanduser('~/Pictures/Screenshots')
    os.makedirs(screenshots_dir, exist_ok=True)
    filename = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S') + '.png'
    path = os.path.join(screenshots_dir, filename)
    try:
        subprocess.Popen(
            ['grim', path],
            env=_WL_ENV, close_fds=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass  # grim not installed


def _set_volume(delta: str) -> None:
    try:
        subprocess.Popen(
            ['pactl', 'set-sink-volume', '@DEFAULT_SINK@', delta],
            close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# DisplayManager
# ---------------------------------------------------------------------------

class DisplayManager:
    """
    Idle/wake lifecycle + hardware button shortcuts.
    Always queries wlopm for actual display state so phoc's own idle timer
    blanking is handled transparently.
    """

    def __init__(
        self,
        on_wake: Callable[[], None] | None = None,
        on_power_menu: Callable[[], None] | None = None,
        on_screenshot: Callable[[], None] | None = None,
        on_fingerprint: Callable[[], None] | None = None,
    ) -> None:
        self._on_wake        = on_wake
        self._on_power_menu  = on_power_menu
        self._on_screenshot  = on_screenshot
        self._on_fingerprint = on_fingerprint

        self._saved_brightness: int   = 200
        self._idle_src: int | None    = None
        self._last_touch_time: float  = 0.0
        self._DOUBLE_TAP_MS = 600
        # Source of truth for blank state. wlopm (if present) reports DPMS, but
        # it is optional; brightnessctl-only devices have no queryable state, so
        # we track it ourselves and never depend on _display_is_on() to wake.
        self._blanked = False

        # Power button long-press tracking
        self._power_down_at: float | None = None   # monotonic time of key-down
        self._power_long_src: int | None  = None   # GLib timer for long-press

        # Combo state: both keys must be logically held simultaneously
        self._power_held   = False
        self._voldown_held = False

        # Watch every readable input node so power/touch/volume wake the
        # display regardless of which event number the device landed on.
        self._wake_devs = _all_event_devices()
        for dev in self._wake_devs:
            _watch_evdev(dev, self._on_event)

        # Safety: only ever blank the backlight if we actually have a way to
        # wake it back up. With no readable input device, leave the screen on.
        self._idle_enabled = bool(self._wake_devs)
        self.reset_idle()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def reset_idle(self) -> None:
        if self._idle_src is not None:
            GLib.source_remove(self._idle_src)
            self._idle_src = None
        if not getattr(self, '_idle_enabled', True):
            return
        self._idle_src = GLib.timeout_add_seconds(_IDLE_SECS, self._on_idle)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_idle(self) -> bool:
        self._idle_src = None
        self._blank()
        return GLib.SOURCE_REMOVE

    def _blank(self) -> None:
        bright = _brightnessctl('get')
        if bright and bright.isdigit() and int(bright) > 0:
            self._saved_brightness = int(bright)
        self._blanked = True
        _wlopm('off')
        _brightnessctl('set', '0')

    def _wake(self) -> None:
        self._blanked = False
        _wlopm('on')
        _brightnessctl('set', str(max(1, self._saved_brightness)))
        self.reset_idle()
        if self._on_wake:
            GLib.idle_add(self._on_wake)

    def _on_event(self, path: str, ev_type: int, code: int, value: int) -> bool:
        if ev_type != EV_KEY and ev_type != EV_ABS:
            return GLib.SOURCE_REMOVE

        # --- Power button ---
        if ev_type == EV_KEY and code == KEY_POWER:
            if value == 1:  # key down
                self._power_held = True
                self._power_down_at = time.monotonic()
                # Check Power+VolDown combo immediately
                if self._voldown_held:
                    self._cancel_long_press()
                    self._trigger_screenshot()
                    return GLib.SOURCE_REMOVE
                # Start long-press timer
                self._power_long_src = GLib.timeout_add(
                    _POWER_LONG_PRESS_MS, self._on_power_long_press
                )
            elif value == 0:  # key up
                self._power_held = False
                was_long = self._cancel_long_press()
                if not was_long and not self._voldown_held:
                    # Short press toggles blank/wake using our own state
                    if not self._blanked:
                        if self._idle_src is not None:
                            GLib.source_remove(self._idle_src)
                            self._idle_src = None
                        self._blank()
                        # Lock as the screen goes dark, so wake shows the lock
                        if self._on_wake:
                            GLib.idle_add(self._on_wake)
                    else:
                        self._wake()
            return GLib.SOURCE_REMOVE

        # --- Volume Down ---
        if ev_type == EV_KEY and code == KEY_VOLUMEDOWN:
            if value == 1:
                self._voldown_held = True
                if self._power_held:
                    self._cancel_long_press()
                    self._trigger_screenshot()
                else:
                    _set_volume('-5%')
                    self.reset_idle()
            elif value == 0:
                self._voldown_held = False
            return GLib.SOURCE_REMOVE

        # --- Volume Up ---
        if ev_type == EV_KEY and code == KEY_VOLUMEUP:
            if value == 1:
                _set_volume('+5%')
                self.reset_idle()
            return GLib.SOURCE_REMOVE

        # --- Fingerprint touch → wake if off, else try auth ---
        if path == '/dev/input/event3' and ev_type == EV_KEY and value == 1:
            if self._blanked:
                self._wake()
            elif self._on_fingerprint:
                GLib.idle_add(self._on_fingerprint)
            return GLib.SOURCE_REMOVE

        # --- Touchscreen: single-tap wakes when blanked, else idle reset ---
        if ev_type == EV_ABS and code == ABS_MT_TRACKING_ID and value >= 0:
            if self._blanked:
                # A blanked screen should wake on the first touch, not require
                # a double-tap the user can't see to time
                self._wake()
            else:
                self._last_touch_time = 0.0
                self.reset_idle()

        return GLib.SOURCE_REMOVE

    def _on_power_long_press(self) -> bool:
        self._power_long_src = None
        if self._on_power_menu:
            GLib.idle_add(self._on_power_menu)
        return GLib.SOURCE_REMOVE

    def _cancel_long_press(self) -> bool:
        """Cancel pending long-press timer. Returns True if it was pending (i.e. was long)."""
        if self._power_long_src is not None:
            GLib.source_remove(self._power_long_src)
            self._power_long_src = None
            return False  # cancelled before firing = was NOT a long press
        return False

    def _trigger_screenshot(self) -> None:
        if self._on_screenshot:
            GLib.idle_add(self._on_screenshot)
        else:
            _take_screenshot()


# ---------------------------------------------------------------------------
# evdev reader
# ---------------------------------------------------------------------------

def _watch_evdev(path: str, callback: Callable[[str, int, int, int], bool]) -> None:
    def _reader() -> None:
        try:
            with open(path, 'rb') as f:
                while True:
                    data = f.read(_EV_SIZE)
                    if len(data) != _EV_SIZE:
                        break
                    _sec, _usec, ev_type, code, value = struct.unpack(_EV_FMT, data)
                    GLib.idle_add(
                        lambda p=path, t=ev_type, c=code, v=value: callback(p, t, c, v)
                    )
        except Exception:
            pass

    t = threading.Thread(target=_reader, daemon=True, name=f'evdev:{path}')
    t.start()
