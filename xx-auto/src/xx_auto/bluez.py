"""BlueZ car-device helpers for the --watch service (AU2, system bus)."""

from __future__ import annotations

from dataclasses import dataclass

BLUEZ_SERVICE = "org.bluez"
BLUEZ_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
DEVICE_IFACE = "org.bluez.Device1"


@dataclass
class CarDevice:
    path: str
    address: str
    name: str
    connected: bool = False
    paired: bool = False


def _reply_type(signature: str | None):
    if signature is None:
        return None
    from gi.repository import GLib

    return GLib.VariantType.new(signature)


def system_connection():
    from gi.repository import Gio

    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def list_paired_devices(connection=None) -> list[CarDevice]:
    """Paired BlueZ devices; [] when Bluetooth is off or missing."""
    from gi.repository import Gio

    own = connection is None
    _ = own
    try:
        conn = connection or system_connection()
        reply = conn.call_sync(
            BLUEZ_SERVICE, "/", BLUEZ_MANAGER_IFACE,
            "GetManagedObjects", None, _reply_type("(a{oa{sa{sv}}})"),
            Gio.DBusCallFlags.NONE, 5000, None)
        (objects,) = reply.unpack()
    except Exception:
        return []
    devices: list[CarDevice] = []
    for path, ifaces in (objects or {}).items():
        props = ifaces.get(DEVICE_IFACE, {})
        try:
            address = str(props.get("Address", "") or "")
            if not address:
                continue
            devices.append(CarDevice(
                path=str(path), address=address,
                name=str(props.get("Name", "") or props.get("Alias", "")
                         or address),
                connected=bool(props.get("Connected", False)),
                paired=bool(props.get("Paired", False))))
        except Exception:
            continue
    devices.sort(key=lambda dev: dev.name.casefold())
    return devices


def device_connected(connection, path: str) -> bool:
    """Current Connected flag for one device path."""
    from gi.repository import Gio

    from gi.repository import GLib

    try:
        reply = connection.call_sync(
            BLUEZ_SERVICE, path, "org.freedesktop.DBus.Properties",
            "Get", GLib.Variant("(ss)", (DEVICE_IFACE, "Connected")),
            _reply_type("(v)"), Gio.DBusCallFlags.NONE, 5000, None)
        (boxed,) = reply.unpack()
        return bool(boxed.unpack())
    except Exception:
        return False
