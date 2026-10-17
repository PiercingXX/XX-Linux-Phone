"""Smart Lock trust agents (Android parity).

While the phone sits in a trusted situation, swipe-up unlocks without the
PIN. Trust comes from two opt-in agents — a connected trusted Bluetooth
device (BlueZ) or the currently connected trusted Wi-Fi network
(NetworkManager) — and never from anything else: on-body detection is not
implemented (no reliable sensor on this hardware).

Rules (kept exactly):
1. Trust applies only while a trusted agent is connected, and only when at
   least one agent list is configured (both empty by default).
2. The PIN is always required: after boot until the first successful PIN
   unlock, when the last successful PIN unlock is more than 4 hours back,
   after 5 failed attempts / while any lockout runs, and after an explicit
   "Lock now" until the next successful PIN unlock.
3. Trust never unlocks on its own — a swipe is always required — and a
   trusted device that disconnects while unlocked never locks the phone;
   the next lock simply requires the PIN again.

Trust evaluation is event-driven: BlueZ Device1 PropertiesChanged plus
ObjectManager membership, and NetworkManager PropertiesChanged, each
re-query asynchronously over D-Bus. Nothing here polls. The lock screen
also re-evaluates at lock time (prepare) so a stale cache cannot grant a
swipe-unlock it should not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from lock_lines import _FAIL_THRESHOLD

TRUST_MAX_PIN_AGE_S = 4 * 3600

_BLUEZ_BUS = 'org.bluez'
_NM_BUS = 'org.freedesktop.NetworkManager'
_NM_PATH = '/org/freedesktop/NetworkManager'
_OBJMGR_IFACE = 'org.freedesktop.DBus.ObjectManager'
_PROPS_IFACE = 'org.freedesktop.DBus.Properties'
_BLUEZ_DEVICE_IFACE = 'org.bluez.Device1'
_NM_ACTIVE_IFACE = 'org.freedesktop.NetworkManager.Connection.Active'

_NM_STATE_ACTIVATED = 2
_DBUS_TIMEOUT_MS = 5_000


def sanitize_bt_devices(raw: object) -> list[str]:
    """Trusted Bluetooth addresses: non-empty strings, upper-cased so
    `aa:bb:...` matches BlueZ's `AA:BB:...`. Anything else is dropped."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            norm = entry.strip().upper()
            if norm not in out:
                out.append(norm)
    return out


def sanitize_ssids(raw: object) -> list[str]:
    """Trusted Wi-Fi SSIDs: non-empty strings kept verbatim (SSIDs are
    case-sensitive). Anything else is dropped."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry:
            if entry not in out:
                out.append(entry)
    return out


def is_configured(config) -> bool:
    """Smart Lock is configured once either trust list is non-empty."""
    return bool(config.smart_lock_bt_devices or config.smart_lock_wifi_ssids)


#: On-body detection is not implemented: no reliable sensor exists on
#: this hardware, so the settings UI hides the row unless this flips.
ON_BODY_SUPPORTED = False


def on_body_supported() -> bool:
    """Whether "stay unlocked while on body" may be offered."""
    return ON_BODY_SUPPORTED


@dataclass
class SmartLockState:
    """Everything the PIN-or-trust decision needs. Pure data: tests build
    it directly, the shell builds it via current_state()."""

    pin_set: bool = False
    configured: bool = False
    trusted_bt: list[str] = field(default_factory=list)
    trusted_ssid: str | None = None
    boot_pin_done: bool = False
    last_pin_time: float = 0.0
    now: float = 0.0
    fail_count: int = 0
    lockout_active: bool = False
    force_pin_until_next_unlock: bool = False


_REASON_ATTEMPTS = 'PIN required — too many attempts'
_REASON_MANUAL = 'PIN required — locked manually'
_REASON_RESTART = 'PIN required — unlock with PIN after restart'
_REASON_STALE = 'PIN required — PIN not entered for over 4 hours'
_REASON_NEARBY = 'PIN required — no trusted device nearby'


def evaluate(state: SmartLockState) -> tuple[bool, str]:
    """(requires_pin, reason). Pure: the full rule table lives here.

    Without a PIN there is nothing to bypass. With one, trust waives the
    keypad only when every PIN-must rule is clear; the reason names the
    first rule still forcing the PIN, and stays empty unless Smart Lock
    is configured (an unconfigured phone shows no Smart Lock line at all).
    """
    if not state.pin_set:
        return (False, '')
    if state.fail_count >= _FAIL_THRESHOLD or state.lockout_active:
        key = _REASON_ATTEMPTS
    elif state.force_pin_until_next_unlock:
        key = _REASON_MANUAL
    elif not state.boot_pin_done:
        key = _REASON_RESTART
    # Wall-clock time jumps (NTP correction, manual change); a backwards
    # jump makes elapsed negative, so fail closed and require the PIN.
    elif (state.now - state.last_pin_time > TRUST_MAX_PIN_AGE_S
            or state.now < state.last_pin_time):
        key = _REASON_STALE
    elif not state.trusted_bt and not state.trusted_ssid:
        key = _REASON_NEARBY
    else:
        return (False, '')
    return (True, key if state.configured else '')


def lock_line_for(state: SmartLockState) -> str:
    """The small muted lock-screen line: '' when Smart Lock is
    unconfigured, otherwise the trust credit or the PIN-required reason."""
    if not state.configured:
        return ''
    requires_pin, reason = evaluate(state)
    if not requires_pin:
        holder = state.trusted_bt[0] if state.trusted_bt else state.trusted_ssid
        return f'Kept unlocked by {holder}'
    return reason


# ------------------------------------------------------------------
# Process-wide PIN-gated runtime state
# ------------------------------------------------------------------

_boot_pin_done = False
_force_pin_until_next_unlock = False


def _reset_runtime_state() -> None:
    """Test seam: forget boot/explicit-lock memory as a fresh process would."""
    global _boot_pin_done, _force_pin_until_next_unlock
    _boot_pin_done = False
    _force_pin_until_next_unlock = False


def note_pin_unlock(config, now: float | None = None) -> None:
    """Record a successful PIN unlock: boot gate and any explicit-lock
    demand clear, and the 4-hour clock restarts from now."""
    global _boot_pin_done, _force_pin_until_next_unlock
    _boot_pin_done = True
    _force_pin_until_next_unlock = False
    try:
        config.record_smart_lock_pin_time(
            now if now is not None else time.time())
    except Exception:
        pass


def note_explicit_lock() -> None:
    """The power menu's "Lock now" path calls this: the next lock demands
    the PIN even inside trust, until a successful PIN unlock clears it."""
    global _force_pin_until_next_unlock
    _force_pin_until_next_unlock = True


def explicit_lock_pending() -> bool:
    return _force_pin_until_next_unlock


def _parse_bluez_objects(objects: dict, trusted: set[str]) -> list[str]:
    """Display labels (Alias, else Address) of connected trusted devices."""
    wanted = {str(entry).strip().upper() for entry in trusted}
    found: list[str] = []
    for _path, ifaces in objects.items():
        if not isinstance(ifaces, dict):
            continue
        dev = ifaces.get(_BLUEZ_DEVICE_IFACE)
        if not isinstance(dev, dict):
            continue
        try:
            props = dict(dev)
        except (TypeError, ValueError):
            continue
        if not props.get('Connected', False):
            continue
        address = props.get('Address')
        if not isinstance(address, str):
            continue
        if address.strip().upper() not in wanted:
            continue
        alias = props.get('Alias')
        label = alias if isinstance(alias, str) and alias else address
        if label not in found:
            found.append(label)
    return found


def _parse_nm_objects(objects: dict, trusted: set[str]) -> str | None:
    """SSID of the first activated Wi-Fi connection in the trusted list."""
    for _path, ifaces in objects.items():
        if not isinstance(ifaces, dict):
            continue
        active = ifaces.get(_NM_ACTIVE_IFACE)
        if not isinstance(active, dict):
            continue
        try:
            props = dict(active)
        except (TypeError, ValueError):
            continue
        try:
            state = int(props.get('State', 0))
        except (TypeError, ValueError):
            continue
        if state != _NM_STATE_ACTIVATED:
            continue
        if props.get('Type') != '802-11-wireless':
            continue
        ssid = props.get('Id')
        if isinstance(ssid, str) and ssid in trusted:
            return ssid
    return None


class SmartLockMonitor:
    """Event-driven trust cache. Subscribes once on the system bus; every
    trust change arrives via a D-Bus signal and re-queries asynchronously,
    so nothing here ever polls or blocks the GTK loop.

    The monitor never unlocks anything itself — it only keeps the cache
    the lock screen consults at swipe time. A trust loss while unlocked
    therefore never locks the phone; the next lock just requires the PIN.
    """

    def __init__(self, config,
                 on_trust_changed: Callable[[], None] | None = None,
                 bus=None) -> None:
        self._config = config
        self._listeners: list[Callable[[], None]] = (
            [on_trust_changed] if on_trust_changed is not None else [])
        self._bus = bus
        self._subs: list[int] = []
        self.trusted_bt: list[str] = []
        self.trusted_ssid: str | None = None
        if bus is not None:
            self._subscribe()
            self.refresh()

    def add_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def start(self) -> None:
        """Attach to the system bus off the caller's hands: safe to call
        from the GTK thread (bus acquisition is one quick local connect;
        every query after that is async). Never raises."""
        if self._bus is not None:
            return
        try:
            from gi.repository import Gio
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception:
            self._bus = None
            return
        try:
            self._subscribe()
        except Exception:
            return
        self.refresh()

    def refresh(self) -> None:
        """Re-query both agents now (the lock screen calls this at lock
        time). Async: results land in the _on_*_objects callbacks."""
        if self._bus is None:
            return
        try:
            self._bus.call(
                _BLUEZ_BUS, '/', _OBJMGR_IFACE, 'GetManagedObjects',
                None, None, 0, _DBUS_TIMEOUT_MS, None,
                self._on_bluez_objects,
            )
            self._bus.call(
                _NM_BUS, _NM_PATH, _OBJMGR_IFACE, 'GetManagedObjects',
                None, None, 0, _DBUS_TIMEOUT_MS, None,
                self._on_nm_objects,
            )
        except Exception:
            pass

    def stop(self) -> None:
        bus, self._bus = self._bus, None
        subs, self._subs = self._subs, []
        if bus is not None:
            for sub_id in subs:
                try:
                    bus.signal_unsubscribe(sub_id)
                except Exception:
                    pass

    def _subscribe(self) -> None:
        assert self._bus is not None
        # Trailing None user_data: handlers take the full 7-arg signal
        # form (conn, sender, path, iface, signal, params, user_data),
        # the same discipline as modem_monitor.
        self._subs.append(self._bus.signal_subscribe(
            _BLUEZ_BUS, _PROPS_IFACE, 'PropertiesChanged', None, None, 0,
            self._on_bt_properties_changed, None,
        ))
        self._subs.append(self._bus.signal_subscribe(
            _BLUEZ_BUS, _OBJMGR_IFACE, 'InterfacesAdded', None, None, 0,
            self._on_bluez_membership, None,
        ))
        self._subs.append(self._bus.signal_subscribe(
            _BLUEZ_BUS, _OBJMGR_IFACE, 'InterfacesRemoved', None, None, 0,
            self._on_bluez_membership, None,
        ))
        self._subs.append(self._bus.signal_subscribe(
            _NM_BUS, _PROPS_IFACE, 'PropertiesChanged', None, None, 0,
            self._on_nm_properties_changed, None,
        ))

    def _emit_if_changed(self, bt: list[str] | None = None,
                         ssid: object = ()) -> None:
        changed = False
        if bt is not None and list(bt) != self.trusted_bt:
            self.trusted_bt = list(bt)
            changed = True
        if ssid != () and ssid != self.trusted_ssid:
            self.trusted_ssid = ssid  # type: ignore[assignment]
            changed = True
        if changed:
            for listener in list(self._listeners):
                try:
                    listener()
                except Exception:
                    pass

    def _on_bluez_objects(self, conn, result) -> None:
        # Gio replies are tuples of out-args: GetManagedObjects is (a{...},).
        try:
            objects = dict(conn.call_finish(result).unpack()[0])
        except Exception:
            # A failed query must drop the cache, not keep stale trust
            # that would waive the PIN with no device present.
            self._emit_if_changed(bt=[])
            return
        try:
            trusted = set(self._config.smart_lock_bt_devices)
        except Exception:
            trusted = set()
        self._emit_if_changed(bt=_parse_bluez_objects(objects, trusted))

    def _on_nm_objects(self, conn, result) -> None:
        try:
            objects = dict(conn.call_finish(result).unpack()[0])
        except Exception:
            self._emit_if_changed(ssid=None)
            return
        try:
            trusted = set(self._config.smart_lock_wifi_ssids)
        except Exception:
            trusted = set()
        self._emit_if_changed(ssid=_parse_nm_objects(objects, trusted))

    def _on_bt_properties_changed(self, _conn, _sender, _path, _iface,
                                  _signal, params, _user_data) -> None:
        try:
            unpacked = params.unpack()
            iface_name, changed, _invalidated = (
                unpacked[0], unpacked[1], unpacked[2])
        except Exception:
            return
        if iface_name != _BLUEZ_DEVICE_IFACE:
            return
        try:
            changed = dict(changed)
        except (TypeError, ValueError):
            return
        if 'Connected' not in changed:
            return
        self._enum_bluez()

    def _on_bluez_membership(self, _conn, _sender, _path, _iface,
                             _signal, _params, _user_data) -> None:
        self._enum_bluez()

    def _on_nm_properties_changed(self, _conn, _sender, _path, _iface,
                                  _signal, params, _user_data) -> None:
        try:
            iface_name = params.unpack()[0]
        except Exception:
            return
        if iface_name not in (_NM_BUS, _NM_ACTIVE_IFACE):
            return
        self._enum_nm()

    def _enum_bluez(self) -> None:
        if self._bus is None:
            return
        try:
            self._bus.call(
                _BLUEZ_BUS, '/', _OBJMGR_IFACE, 'GetManagedObjects',
                None, None, 0, _DBUS_TIMEOUT_MS, None,
                self._on_bluez_objects,
            )
        except Exception:
            pass

    def _enum_nm(self) -> None:
        if self._bus is None:
            return
        try:
            self._bus.call(
                _NM_BUS, _NM_PATH, _OBJMGR_IFACE, 'GetManagedObjects',
                None, None, 0, _DBUS_TIMEOUT_MS, None,
                self._on_nm_objects,
            )
        except Exception:
            pass


_LIVE: SmartLockMonitor | None = None


def ensure_monitor(config, on_trust_changed: Callable[[], None] | None = None,
                   bus=None) -> SmartLockMonitor | None:
    """The process-wide monitor (one D-Bus subscription set per shell).
    Never raises; None when the bus is unreachable and no bus was injected
    (trust then reads as absent and the PIN is required)."""
    global _LIVE
    if _LIVE is not None:
        if on_trust_changed is not None:
            _LIVE.add_listener(on_trust_changed)
        return _LIVE
    try:
        monitor = SmartLockMonitor(config, on_trust_changed, bus=bus)
        if monitor._bus is None and bus is None:
            monitor.start()
            if monitor._bus is None:
                return None
        _LIVE = monitor
        return _LIVE
    except Exception:
        return None


def _reset_live_monitor() -> None:
    """Test seam: drop the process-wide monitor (fresh-process amnesia)."""
    global _LIVE
    live, _LIVE = _LIVE, None
    if live is not None:
        try:
            live.stop()
        except Exception:
            pass


def refresh() -> None:
    """Re-evaluate both agents now; the lock screen calls this at lock time."""
    if _LIVE is not None:
        try:
            _LIVE.refresh()
        except Exception:
            pass


def current_state(config, fail_count: int = 0, lockout_active: bool = False,
                  now: float | None = None) -> SmartLockState:
    """Build the decision state from config, the live trust cache, and
    the process-wide PIN gates. Pure read: never touches D-Bus."""
    live = _LIVE
    try:
        last_pin = config.smart_lock_last_pin_time
    except Exception:
        last_pin = 0.0
    return SmartLockState(
        pin_set=config.pin_hash is not None,
        configured=is_configured(config),
        trusted_bt=list(live.trusted_bt) if live is not None else [],
        trusted_ssid=live.trusted_ssid if live is not None else None,
        boot_pin_done=_boot_pin_done,
        last_pin_time=last_pin,
        now=now if now is not None else time.time(),
        fail_count=fail_count,
        lockout_active=lockout_active,
        force_pin_until_next_unlock=_force_pin_until_next_unlock,
    )
