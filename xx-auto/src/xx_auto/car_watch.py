"""Car watcher: `xx-auto --watch` (systemd user unit, opt-in).

Opens xx-auto when the saved BlueZ device connects, closes it on
disconnect only if this watcher opened it. A manual launch (no tracked
child) never self-closes.
"""

from __future__ import annotations

import subprocess
import time

POLL_SECONDS = 3


def _launcher():
    import shutil

    for candidate in ("xx-auto",):
        path = shutil.which(candidate)
        if path:
            return [path]
    return None


def run_watch(argv: list[str] | None = None) -> int:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    from . import bluez as bluez_mod
    from .applog import LOG
    from .car_decision import Outcome, decide
    from .settings_store import load_settings

    settings = load_settings()
    if not settings.auto_launch or not settings.auto_launch_device:
        print("xx-auto --watch: auto-launch is off; nothing to do")
        return 0
    try:
        system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except Exception as exc:
        LOG.w("watch", f"no system bus: {type(exc).__name__}")
        print("xx-auto --watch: no system bus")
        return 1
    launch_cmd = _launcher()
    child = None
    was_opened = False
    LOG.i("watch", "watching for car connect")
    while True:
        settings = load_settings()
        if not settings.auto_launch or not settings.auto_launch_device:
            break
        devices = {dev.address.lower(): dev
                   for dev in bluez_mod.list_paired_devices(system)}
        saved = (settings.auto_launch_device or "").lower()
        dev = devices.get(saved)
        connected = bool(dev and dev.connected)
        if child is not None and child.poll() is not None:
            child = None
            was_opened = False
        action = "connected" if connected else "disconnected"
        outcome = decide(action,
                         settings.auto_launch_device,
                         settings.auto_launch_device, True, was_opened,
                         screen_in_front=False)
        if outcome is Outcome.OPEN and child is None and launch_cmd:
            try:
                child = subprocess.Popen(launch_cmd)
                was_opened = True
                LOG.i("watch", "car connected: opened xx-auto")
            except Exception as exc:
                LOG.w("watch", f"open failed: {type(exc).__name__}")
        elif outcome is Outcome.CLOSE and child is not None:
            try:
                child.terminate()
            except Exception:
                pass
            child = None
            was_opened = False
            LOG.i("watch", "car gone: closed xx-auto")
        time.sleep(POLL_SECONDS)
    if child is not None and was_opened:
        try:
            child.terminate()
        except Exception:
            pass
    return 0
