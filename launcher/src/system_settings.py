"""
System settings backends — the config file can't own these (design.md
"Settings scope"): WiFi networks, Bluetooth pairing, sound output devices,
battery status. Everything degrades to "unavailable" on machines without the
relevant stack; nothing here may take the shell down.

WiFi goes through nmcli (NetworkManager's supported CLI — far less code than
raw D-Bus AP enumeration for identical results); Bluetooth through BlueZ
D-Bus; sound through pactl; battery through UPower D-Bus.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable

_RunFn = Callable[..., subprocess.CompletedProcess]


def _run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ------------------------------------------------------------------ WiFi

@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int
    security: str
    in_use: bool


def parse_nmcli_wifi(output: str) -> list[WifiNetwork]:
    """Parse `nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi` terse output.
    Terse mode escapes ':' inside values as '\\:'."""
    import re
    networks: dict[str, WifiNetwork] = {}
    for line in output.splitlines():
        parts = [p.replace('\\:', ':') for p in re.split(r'(?<!\\):', line)]
        if len(parts) < 4:
            continue
        in_use = parts[0].strip() == '*'
        ssid = parts[1].strip()
        if not ssid:
            continue
        try:
            signal = int(parts[2])
        except ValueError:
            signal = 0
        security = parts[3].strip() or 'open'
        existing = networks.get(ssid)
        if existing is None or signal > existing.signal or in_use:
            networks[ssid] = WifiNetwork(ssid, signal, security, in_use or
                                         (existing.in_use if existing else False))
    return sorted(networks.values(), key=lambda n: (not n.in_use, -n.signal))


def list_wifi(run: _RunFn = _run) -> list[WifiNetwork] | None:
    """None → WiFi stack unavailable; [] → no networks found."""
    try:
        result = run(['nmcli', '-t', '-f', 'IN-USE,SSID,SIGNAL,SECURITY',
                      'dev', 'wifi', 'list'], timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_nmcli_wifi(result.stdout)


def _wifi_result(ssid: str, result: subprocess.CompletedProcess) -> tuple[bool, str]:
    if result.returncode == 0:
        return True, f'Connected to {ssid}'
    detail = (result.stderr or result.stdout or '').strip().splitlines()
    return False, detail[-1] if detail else 'Connection failed'


def _key_mgmt_for(security: str | None) -> str:
    """nmcli wifi-sec.key-mgmt for a scan's SECURITY field. SAE only when
    the scan reports WPA3 and nothing older — mixed APs still take wpa-psk,
    which negotiates whatever the AP offers."""
    upper = (security or '').upper()
    if 'WPA3' in upper and 'WPA2' not in upper and 'WPA1' not in upper:
        return 'sae'
    return 'wpa-psk'


def saved_wifi_ssids(run: _RunFn = _run) -> list[str] | None:
    """SSIDs with a saved NetworkManager profile (the Smart Lock picker).

    None → NetworkManager unavailable; [] → no saved Wi-Fi networks."""
    import re
    try:
        result = run(['nmcli', '-t', '-f', 'NAME,TYPE', 'connection',
                      'show'], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    ssids: set[str] = set()
    for line in result.stdout.splitlines():
        # Terse mode escapes ':' inside names as '\:'; TYPE never holds
        # one, so every part but the last belongs to the name.
        parts = [p.replace('\\:', ':') for p in re.split(r'(?<!\\):', line)]
        if len(parts) < 2:
            continue
        name = ':'.join(parts[:-1]).strip()
        if not name:
            continue
        if parts[-1].strip().lower() in ('wifi', '802-11-wireless'):
            ssids.add(name)
    return sorted(ssids, key=str.casefold)


def _existing_profiles(run: _RunFn) -> set[str] | None:
    """Names of known NM profiles, or None when NM can't be asked."""
    try:
        result = run(['nmcli', '-t', '-f', 'NAME', 'connection', 'show'],
                     timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return {line.replace('\\:', ':').strip()
            for line in result.stdout.splitlines() if line.strip()}


def _is_secrets_error(result: subprocess.CompletedProcess) -> bool:
    text = ((result.stderr or '') + '\n' + (result.stdout or '')).lower()
    return 'secret' in text or 'wrong password' in text or 'bad psk' in text


def connect_wifi(ssid: str, password: str | None = None,
                 run: _RunFn = _run,
                 security: str | None = None) -> tuple[bool, str]:
    """Connect to `ssid`. Secrets go through nmcli's passwd-file channel
    (nmcli(1): `connection up ... [passwd-file file]`, lines of the form
    `802-11-wireless-security.psk:<password>`) so the PSK never appears in
    argv, where any local user could read it via /proc/<pid>/cmdline. The
    temp file is 0600 and removed on every path.

    A profile that already exists is brought `up` directly (with the new
    PSK via passwd-file when one is given) instead of stacking another
    duplicate. A new profile is created exactly once, with
    wifi-sec.key-mgmt set — without it NM ignores the passwd-file PSK and
    secured networks never connect. A profile created here is deleted
    again when activation fails on secrets; pre-existing profiles are
    never deleted."""
    if not password:
        try:
            result = run(['nmcli', 'dev', 'wifi', 'connect', ssid], timeout=45)
        except (OSError, subprocess.TimeoutExpired):
            return False, 'NetworkManager unavailable'
        return _wifi_result(ssid, result)

    profiles = _existing_profiles(run)
    if profiles is None:
        return False, 'NetworkManager unavailable'
    created = False
    if ssid not in profiles:
        try:
            add = run(['nmcli', 'connection', 'add', 'type', 'wifi',
                       'con-name', ssid, 'ssid', ssid,
                       'wifi-sec.key-mgmt', _key_mgmt_for(security)],
                      timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return False, 'NetworkManager unavailable'
        if add.returncode == 0:
            created = True
        # A nonzero add usually means a racing connect created the profile
        # first ("profile exists") — only `up` matters, so carry on.

    fd, passwd_path = tempfile.mkstemp(prefix='xxwm-psk-')
    try:
        with os.fdopen(fd, 'w') as passwd_file:
            passwd_file.write(f'802-11-wireless-security.psk:{password}\n')
        try:
            result = run(['nmcli', 'connection', 'up', ssid,
                          'passwd-file', passwd_path], timeout=45)
        except (OSError, subprocess.TimeoutExpired):
            return False, 'NetworkManager unavailable'
        ok, message = _wifi_result(ssid, result)
        if not ok and created and _is_secrets_error(result):
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                run(['nmcli', 'connection', 'delete', ssid], timeout=15)
        return ok, message
    finally:
        with contextlib.suppress(OSError):
            os.unlink(passwd_path)


# ------------------------------------------------------------------ Sound

@dataclass(frozen=True)
class AudioSink:
    name: str
    description: str
    is_default: bool


def parse_pactl_sinks(list_json: str, default_name: str) -> list[AudioSink]:
    try:
        sinks = json.loads(list_json)
    except json.JSONDecodeError:
        return []
    out: list[AudioSink] = []
    for sink in sinks if isinstance(sinks, list) else []:
        name = str(sink.get('name', ''))
        if not name:
            continue
        out.append(AudioSink(
            name=name,
            description=str(sink.get('description', name)),
            is_default=(name == default_name),
        ))
    return out


def list_audio_sinks(run: _RunFn = _run) -> list[AudioSink] | None:
    try:
        listing = run(['pactl', '-f', 'json', 'list', 'sinks'], timeout=10)
        default = run(['pactl', 'get-default-sink'], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listing.returncode != 0:
        return None
    return parse_pactl_sinks(listing.stdout, (default.stdout or '').strip())


def set_default_sink(name: str, run: _RunFn = _run) -> bool:
    try:
        return run(['pactl', 'set-default-sink', name], timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# ------------------------------------------------------------------ Battery

_UPOWER_STATES = {1: 'charging', 2: 'discharging', 3: 'empty',
                  4: 'full', 5: 'pending charge', 6: 'pending discharge'}


def battery_status() -> str | None:
    """`87% — discharging, 3h 12m left` from UPower's display device."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        result = bus.call_sync(
            'org.freedesktop.UPower',
            '/org/freedesktop/UPower/devices/DisplayDevice',
            'org.freedesktop.DBus.Properties', 'GetAll',
            GLib.Variant('(s)', ('org.freedesktop.UPower.Device',)),
            GLib.VariantType('(a{sv})'),
            Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0]
    except Exception:
        return None
    if not result.get('IsPresent'):
        return None
    pct = int(result.get('Percentage', 0))
    state = _UPOWER_STATES.get(int(result.get('State', 0)), 'unknown')
    text = f'{pct}% — {state}'
    seconds = int(result.get('TimeToEmpty', 0) if state == 'discharging'
                  else result.get('TimeToFull', 0))
    if seconds > 0:
        hours, minutes = divmod(seconds // 60, 60)
        text += f', {hours}h {minutes:02d}m ' + (
            'left' if state == 'discharging' else 'to full')
    return text


# ------------------------------------------------------------------ Bluetooth

@dataclass(frozen=True)
class BtDevice:
    address: str
    name: str
    paired: bool
    connected: bool


def _bluez_bus():
    from gi.repository import Gio
    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def list_bt_devices() -> list[BtDevice] | None:
    """None → BlueZ unavailable. Known + discovered devices."""
    try:
        from gi.repository import Gio, GLib
        bus = _bluez_bus()
        objects = bus.call_sync(
            'org.bluez', '/', 'org.freedesktop.DBus.ObjectManager',
            'GetManagedObjects', None,
            GLib.VariantType('(a{oa{sa{sv}}})'),
            Gio.DBusCallFlags.NONE, 4000, None,
        ).unpack()[0]
    except Exception:
        return None
    devices = []
    for _path, ifaces in objects.items():
        props = ifaces.get('org.bluez.Device1')
        if not props:
            continue
        devices.append(BtDevice(
            address=str(props.get('Address', '')),
            name=str(props.get('Name') or props.get('Alias') or props.get('Address', '?')),
            paired=bool(props.get('Paired')),
            connected=bool(props.get('Connected')),
        ))
    return sorted(devices, key=lambda d: (not d.connected, not d.paired, d.name.casefold()))


_BLUEZ = 'org.bluez'
_ADAPTER_IFACE = 'org.bluez.Adapter1'
_DEVICE_IFACE = 'org.bluez.Device1'
_AGENT_IFACE = 'org.bluez.Agent1'
_AGENT_MGR = 'org.bluez.AgentManager1'
_AGENT_PATH = '/org/xxwm/Agent'
_AGENT_CAPABILITY = 'NoInputNoOutput'

_AGENT_XML = (
    '<node><interface name="org.bluez.Agent1">'
    '<method name="Release"/>'
    '<method name="RequestPinCode">'
    '<arg type="o" name="device" direction="in"/>'
    '<arg type="s" name="pincode" direction="out"/>'
    '</method>'
    '<method name="DisplayPinCode">'
    '<arg type="o" name="device" direction="in"/>'
    '<arg type="s" name="pincode" direction="in"/>'
    '</method>'
    '<method name="RequestPasskey">'
    '<arg type="o" name="device" direction="in"/>'
    '<arg type="u" name="passkey" direction="out"/>'
    '</method>'
    '<method name="DisplayPasskey">'
    '<arg type="o" name="device" direction="in"/>'
    '<arg type="u" name="passkey" direction="in"/>'
    '<arg type="q" name="entered" direction="in"/>'
    '</method>'
    '<method name="RequestConfirmation">'
    '<arg type="o" name="device" direction="in"/>'
    '<arg type="u" name="passkey" direction="in"/>'
    '</method>'
    '<method name="RequestAuthorization">'
    '<arg type="o" name="device" direction="in"/>'
    '</method>'
    '<method name="AuthorizeService">'
    '<arg type="o" name="device" direction="in"/>'
    '<arg type="s" name="uuid" direction="in"/>'
    '</method>'
    '<method name="Cancel"/>'
    '</interface></node>'
)


def _managed_objects(bus) -> dict:
    from gi.repository import Gio, GLib
    return bus.call_sync(
        _BLUEZ, '/', 'org.freedesktop.DBus.ObjectManager',
        'GetManagedObjects', None,
        GLib.VariantType('(a{oa{sa{sv}}})'),
        Gio.DBusCallFlags.NONE, 4000, None,
    ).unpack()[0]


def _adapter_path(bus=None) -> str | None:
    """First BlueZ adapter from the ObjectManager — never a hardcoded hci0."""
    try:
        bus = bus if bus is not None else _bluez_bus()
        for path, ifaces in _managed_objects(bus).items():
            if _ADAPTER_IFACE in ifaces:
                return str(path)
    except Exception:
        pass
    return None


def _device_path(bus, adapter: str, address: str) -> str:
    """Object path of a device: looked up by address, else the canonical
    BlueZ form under the resolved adapter."""
    try:
        for path, ifaces in _managed_objects(bus).items():
            props = ifaces.get(_DEVICE_IFACE)
            if props and str(props.get('Address', '')).upper() == address.upper():
                return str(path)
    except Exception:
        pass
    return adapter + '/dev_' + address.upper().replace(':', '_')


def _get_prop(bus, path: str, iface: str, prop: str):
    from gi.repository import Gio, GLib
    value = bus.call_sync(
        _BLUEZ, path, 'org.freedesktop.DBus.Properties', 'Get',
        GLib.Variant('(ss)', (iface, prop)),
        GLib.VariantType('(v)'),
        Gio.DBusCallFlags.NONE, 5000, None,
    ).unpack()[0]
    return value.unpack() if hasattr(value, 'unpack') else value


def _set_prop(bus, path: str, iface: str, prop: str,
              signature: str, value) -> None:
    from gi.repository import Gio, GLib
    bus.call_sync(
        _BLUEZ, path, 'org.freedesktop.DBus.Properties', 'Set',
        GLib.Variant('(ssv)', (iface, prop, GLib.Variant(signature, value))),
        None, Gio.DBusCallFlags.NONE, 5000, None,
    )


def _ensure_adapter_powered(bus, adapter: str) -> None:
    """Power the adapter on when it is off; best-effort, never raises."""
    try:
        if not _get_prop(bus, adapter, _ADAPTER_IFACE, 'Powered'):
            _set_prop(bus, adapter, _ADAPTER_IFACE, 'Powered', 'b', True)
    except Exception:
        pass


def _on_agent_method(_conn, _sender, _path, _iface, method: str,
                     _params, invocation) -> None:
    """NoInputNoOutput agent: confirm everything, display nothing."""
    from gi.repository import GLib
    try:
        if method == 'RequestPinCode':
            invocation.return_value(GLib.Variant('(s)', ('',)))
        elif method == 'RequestPasskey':
            invocation.return_value(GLib.Variant('(u)', (0,)))
        else:
            invocation.return_value(None)
    except Exception as error:
        with contextlib.suppress(Exception):
            invocation.return_error_literal(0, 'Rejected',
                                            getattr(error, 'message', None)
                                            or str(error))


def _register_agent(bus, capability: str = _AGENT_CAPABILITY):
    """Export the pairing agent and make it the default. Returns the
    registration id, or None when BlueZ refused (pairing still attempted)."""
    from gi.repository import Gio, GLib
    try:
        iface = Gio.DBusNodeInfo.new_for_xml(_AGENT_XML).interfaces[0]
        reg_id = bus.register_object(_AGENT_PATH, iface, _on_agent_method)
        bus.call_sync(_BLUEZ, '/org/bluez', _AGENT_MGR, 'RegisterAgent',
                      GLib.Variant('(os)', (_AGENT_PATH, capability)),
                      None, Gio.DBusCallFlags.NONE, 5000, None)
        bus.call_sync(_BLUEZ, '/org/bluez', _AGENT_MGR, 'RequestDefaultAgent',
                      GLib.Variant('(o)', (_AGENT_PATH,)),
                      None, Gio.DBusCallFlags.NONE, 5000, None)
        return reg_id
    except Exception:
        return None


def _unregister_agent(bus, reg_id) -> None:
    from gi.repository import Gio, GLib
    if reg_id is not None:
        with contextlib.suppress(Exception):
            bus.unregister_object(reg_id)
    with contextlib.suppress(Exception):
        bus.call_sync(_BLUEZ, '/org/bluez', _AGENT_MGR, 'UnregisterAgent',
                      GLib.Variant('(o)', (_AGENT_PATH,)),
                      None, Gio.DBusCallFlags.NONE, 5000, None)


def _start_bt_discovery_sync(seconds: int) -> bool:
    try:
        from gi.repository import Gio, GLib
        bus = _bluez_bus()
        adapter = _adapter_path(bus)
        if adapter is None:
            return False
        _ensure_adapter_powered(bus, adapter)
        bus.call_sync(_BLUEZ, adapter, _ADAPTER_IFACE,
                      'StartDiscovery', None, None,
                      Gio.DBusCallFlags.NONE, 5000, None)
        GLib.timeout_add_seconds(seconds, _stop_bt_discovery, adapter)
        return True
    except Exception:
        return False


def start_bt_discovery(seconds: int = 12, on_done=None) -> bool:
    """Kick off discovery off the GTK thread; results appear in the next
    list_bt_devices() call. `on_done(ok)` fires on the main loop."""
    import threading
    from gi.repository import GLib

    def _worker() -> None:
        ok = _start_bt_discovery_sync(seconds)

        def _deliver() -> bool:
            if on_done is not None:
                on_done(ok)
            return False
        GLib.idle_add(_deliver)

    threading.Thread(target=_worker, daemon=True).start()
    return True


def _stop_bt_discovery(adapter: str | None = None) -> bool:
    try:
        from gi.repository import Gio
        bus = _bluez_bus()
        target = adapter or _adapter_path(bus)
        if target is None:
            return False
        bus.call_sync(_BLUEZ, target, _ADAPTER_IFACE,
                      'StopDiscovery', None, None,
                      Gio.DBusCallFlags.NONE, 5000, None)
    except Exception:
        pass
    return False


def pair_bt_device(address: str,
                   agent_capability: str = _AGENT_CAPABILITY,
                   ) -> tuple[bool, str]:
    """Pair, trust, then connect. The agent is registered only for the
    duration of the pairing. Device-gated for real verification."""
    try:
        bus = _bluez_bus()
    except Exception as error:
        return False, getattr(error, 'message', None) or str(error)
    adapter = _adapter_path(bus)
    if adapter is None:
        return False, 'No Bluetooth adapter found'
    _ensure_adapter_powered(bus, adapter)
    path = _device_path(bus, adapter, address)
    reg_id = _register_agent(bus, agent_capability)
    try:
        from gi.repository import Gio
        try:
            bus.call_sync(_BLUEZ, path, _DEVICE_IFACE, 'Pair',
                          None, None, Gio.DBusCallFlags.NONE, 30000, None)
        except Exception as error:
            message = getattr(error, 'message', None) or str(error)
            if 'AlreadyExists' in message:
                return connect_bt_device(address)
            return False, message
        try:
            _set_prop(bus, path, _DEVICE_IFACE, 'Trusted', 'b', True)
        except Exception:
            pass
        try:
            bus.call_sync(_BLUEZ, path, _DEVICE_IFACE, 'Connect',
                          None, None, Gio.DBusCallFlags.NONE, 30000, None)
        except Exception as error:
            return False, getattr(error, 'message', None) or str(error)
        return True, 'Paired and connected'
    finally:
        _unregister_agent(bus, reg_id)


def connect_bt_device(address: str) -> tuple[bool, str]:
    try:
        bus = _bluez_bus()
        adapter = _adapter_path(bus)
        if adapter is None:
            return False, 'No Bluetooth adapter found'
        path = _device_path(bus, adapter, address)
        from gi.repository import Gio
        flags = Gio.DBusCallFlags.NONE
        bus.call_sync(_BLUEZ, path, _DEVICE_IFACE, 'Connect', None, None,
                      flags, 30000, None)
        return True, 'Connected'
    except Exception as error:
        return False, getattr(error, 'message', None) or str(error)
