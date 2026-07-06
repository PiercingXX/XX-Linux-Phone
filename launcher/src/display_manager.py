"""
Display power management.

Uses wlopm (wlr-output-power-management) to talk to phoc so the output is
properly powered on/off.  State is always checked via wlopm rather than
tracked internally — this handles phoc blanking the display on its own
idle timer without us knowing.

Wake sources (evdev nodes carried from the previous test device — verify on
the FP5 with `libinput list-devices` before trusting them):
  - Power button  → event0 + event1
  - Fingerprint   → event3
  - Touch         → event2

Hardware shortcuts (handled via evdev):
  - Power long-press (≥600ms)          → on_power_menu()
  - Power + Volume Down simultaneously → on_screenshot()
  - Volume Up / Down                   → +5% / -5% media volume
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

# All devices that can wake the display or provide hardware shortcuts
_WAKE_DEVS = [
    '/dev/input/event0',   # qpnp_pon  — primary PMIC power button
    '/dev/input/event1',   # gpio-keys — secondary power button + volume keys
    '/dev/input/event2',   # synaptics_dsx — touchscreen (double-tap to wake)
    '/dev/input/event3',   # uinput-fpc — fingerprint reader
]

_WLOPM_OUTPUT = 'HWCOMPOSER-1'
_IDLE_SECS    = 60

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
            ['wlopm', f'--{mode}', _WLOPM_OUTPUT],
            env=_WL_ENV, timeout=3,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except Exception:
        return False


def _display_is_on() -> bool:
    """Query phoc via wlopm — always accurate regardless of who blanked it."""
    try:
        out = subprocess.check_output(
            ['wlopm'], env=_WL_ENV, timeout=2, text=True,
            stderr=subprocess.DEVNULL,
        )
        return f'{_WLOPM_OUTPUT} off' not in out
    except Exception:
        return True  # assume on if wlopm fails


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

        # Power button long-press tracking
        self._power_down_at: float | None = None   # monotonic time of key-down
        self._power_long_src: int | None  = None   # GLib timer for long-press

        # Combo state: both keys must be logically held simultaneously
        self._power_held   = False
        self._voldown_held = False

        for dev in _WAKE_DEVS:
            _watch_evdev(dev, self._on_event)

        self.reset_idle()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def reset_idle(self) -> None:
        if self._idle_src is not None:
            GLib.source_remove(self._idle_src)
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
        _wlopm('off')
        _brightnessctl('set', '0')

    def _wake(self) -> None:
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
                elapsed_ms = (time.monotonic() - (self._power_down_at or 0)) * 1000
                was_long = self._cancel_long_press()
                if not was_long and not self._voldown_held:
                    # Short press: blank/wake
                    if _display_is_on():
                        if self._idle_src is not None:
                            GLib.source_remove(self._idle_src)
                            self._idle_src = None
                        self._blank()
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
            if not _display_is_on():
                self._wake()
            elif self._on_fingerprint:
                GLib.idle_add(self._on_fingerprint)
            return GLib.SOURCE_REMOVE

        # --- Touchscreen: double-tap to wake + idle reset ---
        if ev_type == EV_ABS and code == ABS_MT_TRACKING_ID and value >= 0:
            now = time.monotonic() * 1000
            if not _display_is_on():
                if now - self._last_touch_time < self._DOUBLE_TAP_MS:
                    self._wake()
                self._last_touch_time = now
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
