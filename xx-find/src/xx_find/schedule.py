"""Lane 4 poll cadence and battery budget.

Pure math plus injected seams: no network, GPS, keyring or bus access at
import or in the cadence functions. The battery probe takes an injected
system bus (UPower display device, same path as the shell's standby
helper); tests use fakes.

Cadence (PORT_SPEC 1.2): 15 min idle poll, 6 min while a command is
active (ring/message pending), location fix only on `locate` or a 6 h
heartbeat. Poll jitter +-25% (threat model: traffic-analysis cover);
exponential backoff on consecutive transport errors.
"""

from __future__ import annotations

import logging
import time

from gi.repository import Gio, GLib

LOG = logging.getLogger("xx-find.schedule")

IDLE_POLL_S = 900.0
ACTIVE_POLL_S = 360.0
HEARTBEAT_S = 6 * 3600.0
JITTER_FRAC = 0.25
MAX_BACKOFF_SHIFT = 4
MAX_DELAY_S = 3600.0
RADIO_WAKE_S = 10.0
LOW_BATTERY_PCT = 15.0

UPOWER_NAME = "org.freedesktop.UPower"
UPOWER_DISPLAY_PATH = "/org/freedesktop/UPower/devices/DisplayDevice"
UPOWER_DEVICE_IFACE = "org.freedesktop.UPower.Device"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

UP_STATE_CHARGING = 1
UP_WARNING_UNKNOWN = 0
UP_WARNING_NONE = 1
UP_WARNING_DISCHARGING = 2
UP_WARNING_LOW = 3
UP_WARNING_CRITICAL = 4
UP_WARNING_ACTION = 5
CRITICAL_WARNING_LEVELS = frozenset(
    {UP_WARNING_LOW, UP_WARNING_CRITICAL, UP_WARNING_ACTION})

LOGIN1_NAME = "org.freedesktop.login1"
LOGIN1_PATH = "/org/freedesktop/login1"
LOGIN1_MANAGER = "org.freedesktop.login1.Manager"


def _reply_type(fmt: str) -> GLib.VariantType:
    return GLib.VariantType.new(fmt)


def _deep(value):
    return value.unpack() if isinstance(value, GLib.Variant) else value


def jittered(base_s: float, rand: float) -> float:
    """`base_s` with +-25% jitter from `rand` in [0, 1). Pure."""
    base = float(base_s)
    if not base > 0:
        raise ValueError("base interval must be positive")
    try:
        draw = float(rand)
    except (TypeError, ValueError) as exc:
        raise ValueError("rand must be a float in [0, 1)") from exc
    if not 0.0 <= draw < 1.0:
        raise ValueError("rand must be in [0, 1)")
    return base * ((1.0 - JITTER_FRAC) + (2.0 * JITTER_FRAC) * draw)


def backoff_base(base_s: float, failures: int) -> float:
    """Exponential backoff: `base * 2**failures`, capped. Pure."""
    try:
        count = int(failures)
    except (TypeError, ValueError):
        count = 0
    count = max(0, count)
    shift = min(count, MAX_BACKOFF_SHIFT)
    return min(float(base_s) * (2 ** shift), MAX_DELAY_S)


def poll_delay(*, active: bool, failures: int = 0, rand: float = 0.5,
               idle_s: float = IDLE_POLL_S,
               active_s: float = ACTIVE_POLL_S) -> float:
    """Seconds until the next poll. Pure: jittered backoff on the active
    or idle base. `rand` is an injected draw in [0, 1)."""
    base = float(active_s) if active else float(idle_s)
    grown = backoff_base(base, failures)
    return jittered(grown, rand)


def heartbeat_due(last_report_epoch: int, now: int,
                  interval_s: float = HEARTBEAT_S) -> bool:
    """True when a 6 h location heartbeat is due. Pure."""
    try:
        last = int(last_report_epoch)
        current = int(now)
    except (TypeError, ValueError):
        return True
    if last <= 0:
        return True
    return (current - last) >= float(interval_s)


def is_low_battery(pct: float | None, charging: bool,
                   warning_level: int | None = None) -> bool:
    """Low-battery gate: `WarningLevel` low/critical/action, or <= 15%
    and not charging. Unknown level never gates on its own.

    `warning_level` follows UPower's `UpDeviceLevel` for `WarningLevel`
    (`0` unknown, `1` none, `2` discharging, `3` low, `4` critical,
    `5` action; only up to action is relevant). Charging always wins:
    a charger connected means no last report either way.
    """
    if charging:
        return False
    if warning_level is not None:
        try:
            level = int(warning_level)
        except (TypeError, ValueError):
            level = -1
        if level in CRITICAL_WARNING_LEVELS:
            return True
    if pct is None:
        return False
    try:
        value = float(pct)
    except (TypeError, ValueError):
        return False
    return value <= LOW_BATTERY_PCT


def idle_polls_per_day(idle_s: float = IDLE_POLL_S) -> float:
    return 86400.0 / float(idle_s)


def budget_report(*, idle_s: float = IDLE_POLL_S,
                  active_s: float = ACTIVE_POLL_S,
                  wake_s: float = RADIO_WAKE_S) -> dict:
    """Computed battery budget from the spec targets. Pure; callers log it.

    Targets: idle < 1 %/day (one ~10 s radio wake per 15 min poll, no
    GPS); active-locate session < 3 %/h. This reports the wake counts
    the targets assume; on-device measurement (lane 4 procedure in
    `docs/shell-integration.md`) confirms the percentages later.
    """
    polls = idle_polls_per_day(idle_s)
    radio_s = polls * float(wake_s)
    return {"idle_poll_s": float(idle_s),
            "active_poll_s": float(active_s),
            "idle_polls_per_day": polls,
            "radio_wake_s_per_poll": float(wake_s),
            "radio_s_per_day": radio_s,
            "idle_target_pct_per_day": 1.0,
            "active_target_pct_per_hour": 3.0,
            "low_battery_pct": LOW_BATTERY_PCT,
            "heartbeat_s": float(HEARTBEAT_S)}


def log_budget(logger=None) -> dict:
    """Log the computed budget and return it. No measurement here."""
    report = budget_report()
    (logger or LOG).info(
        "xx-find budget: %.0f idle polls/day (every %.0fs) x %.0fs radio "
        "= %.0fs/day airtime; target <1%%/day idle, <3%%/h active; "
        "low-battery <=%.0f%%%% then last report until charging; "
        "heartbeat every %.0fh",
        report["idle_polls_per_day"], report["idle_poll_s"],
        report["radio_wake_s_per_poll"], report["radio_s_per_day"],
        report["low_battery_pct"], report["heartbeat_s"] / 3600.0)
    return report


def _open_system_bus():
    from gi.repository import Gio as _Gio
    return _Gio.bus_get_sync(_Gio.BusType.SYSTEM, None)


def battery_snapshot(bus, timeout_ms: int = 2000) -> dict:
    """One UPower display-device snapshot over an injected bus. Never
    raises: unreadable properties yield `None` so callers fail closed."""
    try:
        reply = bus.call_sync(
            UPOWER_NAME, UPOWER_DISPLAY_PATH, PROPS_IFACE, "GetAll",
            GLib.Variant("(s)", (UPOWER_DEVICE_IFACE,)),
            _reply_type("(a{sv})"), Gio.DBusCallFlags.NONE,
            timeout_ms, None)
        props = reply.unpack()[0]
    except Exception:
        return {"pct": None, "charging": False, "warning_level": None,
                "present": False}
    if not isinstance(props, dict):
        return {"pct": None, "charging": False, "warning_level": None,
                "present": False}
    if not bool(_deep(props.get("IsPresent", False))):
        return {"pct": None, "charging": False, "warning_level": None,
                "present": False}
    try:
        pct: float | None = float(_deep(props.get("Percentage", 0)))
    except (TypeError, ValueError):
        pct = None
    try:
        state = int(_deep(props.get("State", 0)))
    except (TypeError, ValueError):
        state = 0
    try:
        warning: int | None = int(_deep(props.get("WarningLevel",
                                                  UP_WARNING_UNKNOWN)))
    except (TypeError, ValueError):
        warning = None
    return {"pct": pct, "charging": state == UP_STATE_CHARGING,
            "warning_level": warning, "present": True}


def build_battery_probe(*, system_bus_factory=None):
    """Zero-arg `probe() -> (pct|None, charging)` over an injected bus."""

    def _probe() -> tuple[float | None, bool]:
        try:
            factory = system_bus_factory or _open_system_bus
            bus = factory()
        except Exception:
            return (None, False)
        if bus is None:
            return (None, False)
        snap = battery_snapshot(bus)
        if not snap.get("present"):
            return (None, False)
        return (snap.get("pct"), bool(snap.get("charging")))

    return _probe


def build_battery_snapshot_probe(*, system_bus_factory=None):
    """Zero-arg `probe() -> snapshot dict` over an injected bus."""

    def _probe() -> dict:
        try:
            factory = system_bus_factory or _open_system_bus
            bus = factory()
        except Exception:
            return {"pct": None, "charging": False,
                    "warning_level": None, "present": False}
        if bus is None:
            return {"pct": None, "charging": False,
                    "warning_level": None, "present": False}
        return battery_snapshot(bus)

    return _probe


def build_last_report_hook(*, battery_probe, read_fix, send_report,
                           config, on_privacy_event=None):
    """Zero-arg `attempt() -> outcome` for the low-battery/shutdown path.

    Probes the battery through the injected `battery_probe` (a
    `(pct, charging)` tuple or a snapshot dict with `WarningLevel`);
    when critical, takes one fix and sends it through the existing
    `send_report(config, fix)` path with `battery_pct` attached, then
    fires `on_privacy_event("location", "xx-find-agent")` like a locate.
    Never raises: every failure returns `{"sent": False, "reason": ...}`
    so a shutdown hook never delays shutdown.
    """

    def _attempt() -> dict:
        try:
            state = battery_probe()
        except Exception as exc:
            return {"sent": False, "reason": "battery probe failed: %s"
                    % exc}
        pct: float | None = None
        charging = False
        warning: int | None = None
        try:
            if isinstance(state, dict):
                pct = state.get("pct")
                charging = bool(state.get("charging"))
                warning = state.get("warning_level")
            elif isinstance(state, (tuple, list)) and len(state) >= 2:
                pct, charging = state[0], bool(state[1])
                warning = state[2] if len(state) >= 3 else None
        except Exception:
            return {"sent": False, "reason": "bad battery state"}
        if not is_low_battery(pct, charging, warning):
            return {"sent": False, "reason": "battery ok"}
        try:
            fix = read_fix()
        except Exception as exc:
            return {"sent": False, "reason": "fix failed: %s" % exc}
        if not fix:
            return {"sent": False, "reason": "no fix"}
        report_fix = dict(fix)
        if pct is not None:
            try:
                report_fix["battery_pct"] = int(float(pct))
            except (TypeError, ValueError):
                pass
        if on_privacy_event is not None:
            try:
                on_privacy_event("location", "xx-find-agent")
            except Exception:
                pass
        try:
            send_report(config, report_fix)
        except Exception as exc:
            return {"sent": False, "reason": "report failed: %s" % exc}
        return {"sent": True, "reason": "last report sent"}

    return _attempt


def subscribe_shutdown_hook(bus, on_prepare) -> object:
    """Subscribe to logind `PrepareForShutdown` on an injected bus.

    `sender` stays `None` because the signal arrives from logind's
    unique name, not its well-known name; the object path filters the
    subscription. Returns the subscription id for `signal_unsubscribe`.
    The 7-arg handler shape mirrors real `signal_subscribe` callbacks.
    """
    def _on_signal(conn, sender, path, iface, signal, params,
                   user_data) -> None:
        try:
            (starting,) = params.unpack()
        except Exception:
            return
        if starting:
            try:
                on_prepare()
            except Exception:
                pass

    return bus.signal_subscribe(
        None, LOGIN1_MANAGER, "PrepareForShutdown", LOGIN1_PATH,
        None, Gio.DBusSignalFlags.NONE, _on_signal, None)


def next_wake(*, active: bool, failures: int = 0,
              idle_s: float = IDLE_POLL_S,
              active_s: float = ACTIVE_POLL_S,
              rand: float = 0.5, now=None) -> dict:
    """One scheduling decision for the poll loop. Pure except the clock."""
    clock = now or time.time
    delay = poll_delay(active=active, failures=failures, rand=rand,
                       idle_s=idle_s, active_s=active_s)
    return {"delay_s": delay, "at": float(clock()) + delay,
            "active": bool(active), "failures": max(0, int(failures))}
