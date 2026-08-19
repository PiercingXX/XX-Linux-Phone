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

import json
import subprocess
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


def connect_wifi(ssid: str, password: str | None = None,
                 run: _RunFn = _run) -> tuple[bool, str]:
    cmd = ['nmcli', 'dev', 'wifi', 'connect', ssid]
    if password:
        cmd += ['password', password]
    try:
        result = run(cmd, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        return False, 'NetworkManager unavailable'
    if result.returncode == 0:
        return True, f'Connected to {ssid}'
    detail = (result.stderr or result.stdout or '').strip().splitlines()
    return False, detail[-1] if detail else 'Connection failed'


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


def _device_path(address: str) -> str:
    return '/org/bluez/hci0/dev_' + address.replace(':', '_')


def start_bt_discovery(seconds: int = 12) -> bool:
    """Kick off discovery; results appear in the next list_bt_devices() call."""
    try:
        from gi.repository import Gio, GLib
        bus = _bluez_bus()
        bus.call_sync('org.bluez', '/org/bluez/hci0', 'org.bluez.Adapter1',
                      'StartDiscovery', None, None,
                      Gio.DBusCallFlags.NONE, 2000, None)
        GLib.timeout_add_seconds(seconds, _stop_bt_discovery)
        return True
    except Exception:
        return False


def _stop_bt_discovery() -> bool:
    try:
        from gi.repository import Gio
        _bluez_bus().call_sync('org.bluez', '/org/bluez/hci0', 'org.bluez.Adapter1',
                               'StopDiscovery', None, None,
                               Gio.DBusCallFlags.NONE, 2000, None)
    except Exception:
        pass
    return False


def pair_bt_device(address: str) -> tuple[bool, str]:
    """Pair then connect. Device-gated for real verification."""
    try:
        from gi.repository import Gio
        bus = _bluez_bus()
        path = _device_path(address)
        bus.call_sync('org.bluez', path, 'org.bluez.Device1', 'Pair',
                      None, None, Gio.DBusCallFlags.NONE, 30000, None)
        bus.call_sync('org.bluez', path, 'org.bluez.Device1', 'Connect',
                      None, None, Gio.DBusCallFlags.NONE, 30000, None)
        return True, 'Paired and connected'
    except Exception as error:
        message = getattr(error, 'message', None) or str(error)
        if 'AlreadyExists' in message:
            return connect_bt_device(address)
        return False, message


def connect_bt_device(address: str) -> tuple[bool, str]:
    try:
        from gi.repository import Gio
        _bluez_bus().call_sync('org.bluez', _device_path(address),
                               'org.bluez.Device1', 'Connect', None, None,
                               Gio.DBusCallFlags.NONE, 30000, None)
        return True, 'Connected'
    except Exception as error:
        return False, getattr(error, 'message', None) or str(error)
