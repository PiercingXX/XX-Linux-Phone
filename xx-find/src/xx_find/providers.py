"""Location providers: GeoClue2 client plus ModemManager fallback.

Pure parsing and fix selection stay in `location.py`; everything here
that touches D-Bus takes an injected bus object, so unit tests run
against fakes and never touch the real system bus, GPS or modem.

GeoClue authorization (why this module never registers an agent): the
daemon authorizes each client through the single system-wide agent's
`AuthorizeApp(app_id, req_accuracy)` (capped by its `MaxAccuracyLevel`)
unless the app is pinned in the local whitelist config. XX-WM already
owns that slot (`LocationState` in the shell): it is the single writer
of location policy. This module only ever acts as a *client*
(`GetClient`, set `DesktopId` / `RequestedAccuracyLevel` /
`DistanceThreshold`, `Start`, wait for one `LocationUpdated`, `Stop`).
Our desktop id is added to the image's agent whitelist at image build
time; this repo never writes system config and never registers an
agent, which would steal the shell's slot.

Privacy: every successful read returns a fix dict to the agent, whose
`on_privacy_event` hook (`Agent._do_locate`) records one `location`
event per read for the shell's privacy monitor. Failed reads expose no
position and emit nothing.

Threading: all calls below block with timeouts, so call them on a
worker thread, never on a GTK main loop. D-Bus trap compliance: every
`reply_type` is a `GLib.VariantType` (never a string); no prebuilt
`a{sv}` Variant is nested inside another Variant (property values go in
`(ssv)` with one inner Variant, as the shell does); signal handlers
take all 7 `signal_subscribe` args; subscriptions filter on the object
path with `sender=None` because signals arrive from the daemon's unique
name, not its well-known name.
"""

from __future__ import annotations

import re
import threading
import time

from gi.repository import Gio, GLib

from xx_find import location as location_mod

GEOCLUE_NAME = "org.freedesktop.GeoClue2"
GEOCLUE_MGR_PATH = "/org/freedesktop/GeoClue2/Manager"
GEOCLUE_MGR_IFACE = "org.freedesktop.GeoClue2.Manager"
GEOCLUE_CLIENT_IFACE = "org.freedesktop.GeoClue2.Client"
GEOCLUE_LOCATION_IFACE = "org.freedesktop.GeoClue2.Location"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
OBJMGR_IFACE = "org.freedesktop.DBus.ObjectManager"

GEOCLUE_DESKTOP_ID = "io.piercingxx.Find"
GEOCLUE_DISTANCE_THRESHOLD_M = 0
GEOCLUE_TIMEOUT_S = 20.0

MM_NAME = "org.freedesktop.ModemManager1"
MM_PATH = "/org/freedesktop/ModemManager1"
MM_LOCATION_IFACE = "org.freedesktop.ModemManager1.Modem.Location"

MM_SOURCE_3GPP = 1
MM_SOURCE_GPS_RAW = 2
MM_SOURCE_GPS_NMEA = 4
MM_SETUP_SOURCES = MM_SOURCE_3GPP | MM_SOURCE_GPS_RAW | MM_SOURCE_GPS_NMEA
MODEM_TIMEOUT_S = 10.0

_PROP_TIMEOUT_MS = 5000
_START_TIMEOUT_MS = 10000


def _reply_type(fmt: str) -> GLib.VariantType:
    return GLib.VariantType.new(fmt)


def _props_set(bus, name: str, path: str, iface: str, prop: str,
               value: GLib.Variant, timeout_ms: int = _PROP_TIMEOUT_MS,
               ) -> None:
    bus.call_sync(name, path, PROPS_IFACE, "Set",
                  GLib.Variant("(ssv)", (iface, prop, value)),
                  None, Gio.DBusCallFlags.NONE, timeout_ms, None)


def _props_get(bus, name: str, path: str, iface: str, prop: str,
               timeout_ms: int = _PROP_TIMEOUT_MS):
    reply = bus.call_sync(
        name, path, PROPS_IFACE, "Get",
        GLib.Variant("(ss)", (iface, prop)),
        _reply_type("(v)"), Gio.DBusCallFlags.NONE, timeout_ms, None)
    return _unwrap(reply.unpack()[0])


def _props_get_all(bus, name: str, path: str, iface: str,
                   timeout_ms: int = _PROP_TIMEOUT_MS) -> dict:
    reply = bus.call_sync(
        name, path, PROPS_IFACE, "GetAll",
        GLib.Variant("(s)", (iface,)),
        _reply_type("(a{sv})"), Gio.DBusCallFlags.NONE, timeout_ms, None)
    props = reply.unpack()[0]
    return dict(props) if isinstance(props, dict) else {}


def _unwrap(value):
    return value.unpack() if isinstance(value, GLib.Variant) else value


def _as_float(value) -> float | None:
    value = _unwrap(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def location_epoch(value, default) -> int:
    """Timestamp to unix epoch. Accepts a single number or a (sec, usec)
    pair, which is what location daemons commonly serve."""
    try:
        fallback = int(default)
    except (TypeError, ValueError):
        fallback = 0
    value = _unwrap(value)
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return int(value) if int(value) > 0 else fallback
    if isinstance(value, (tuple, list)) and value:
        try:
            first = int(value[0])
        except (TypeError, ValueError):
            return fallback
        return first if first > 0 else fallback
    return fallback


def _location_fix(props: dict, epoch_default: int) -> dict | None:
    lat = _as_float(props.get("Latitude"))
    lon = _as_float(props.get("Longitude"))
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return {"lat": lat, "lon": lon,
            "acc_m": _as_float(props.get("Accuracy")),
            "epoch": location_epoch(props.get("Timestamp"), epoch_default),
            "source": location_mod.SOURCE_GEOCLUE}


def geoclue_fix(bus, *, desktop_id: str = GEOCLUE_DESKTOP_ID,
                timeout_s: float = GEOCLUE_TIMEOUT_S,
                now=time.time) -> dict | None:
    """One GeoClue2 fix over an injected system bus. Returns a fix dict
    or None. Always issues `Stop` and unsubscribes before returning."""
    try:
        client_path = bus.call_sync(
            GEOCLUE_NAME, GEOCLUE_MGR_PATH, GEOCLUE_MGR_IFACE,
            "GetClient", None, _reply_type("(o)"),
            Gio.DBusCallFlags.NONE, _PROP_TIMEOUT_MS, None).unpack()[0]
    except Exception:
        return None
    if not isinstance(client_path, str) or not client_path.startswith("/"):
        return None
    try:
        _props_set(bus, GEOCLUE_NAME, client_path, GEOCLUE_CLIENT_IFACE,
                   "DesktopId", GLib.Variant("s", desktop_id))
        _props_set(bus, GEOCLUE_NAME, client_path, GEOCLUE_CLIENT_IFACE,
                   "RequestedAccuracyLevel",
                   GLib.Variant("u", location_mod.GEOCLUE_ACCURACY_EXACT))
        _props_set(bus, GEOCLUE_NAME, client_path, GEOCLUE_CLIENT_IFACE,
                   "DistanceThreshold",
                   GLib.Variant("u", GEOCLUE_DISTANCE_THRESHOLD_M))
    except Exception:
        return None

    state: dict = {}
    arrived = threading.Event()

    def _on_location_updated(conn, sender, path, iface, signal, params,
                             user_data) -> None:
        try:
            _old, new = params.unpack()
        except Exception:
            return
        if new:
            state["path"] = new
            arrived.set()

    try:
        sub_id = bus.signal_subscribe(
            None, GEOCLUE_CLIENT_IFACE, "LocationUpdated", client_path,
            None, Gio.DBusSignalFlags.NONE, _on_location_updated, None)
    except Exception:
        return None
    try:
        try:
            bus.call_sync(GEOCLUE_NAME, client_path, GEOCLUE_CLIENT_IFACE,
                          "Start", None, None,
                          Gio.DBusCallFlags.NONE, _START_TIMEOUT_MS, None)
        except Exception:
            return None
        loc_path = _client_location_path(bus, client_path)
        if not loc_path:
            arrived.wait(timeout_s)
            loc_path = state.get("path") or _client_location_path(
                bus, client_path)
        if not loc_path:
            return None
        try:
            props = _props_get_all(bus, GEOCLUE_NAME, loc_path,
                                   GEOCLUE_LOCATION_IFACE)
        except Exception:
            return None
        return _location_fix(props, int(now()))
    finally:
        try:
            bus.call_sync(GEOCLUE_NAME, client_path, GEOCLUE_CLIENT_IFACE,
                          "Stop", None, None,
                          Gio.DBusCallFlags.NONE, _PROP_TIMEOUT_MS, None)
        except Exception:
            pass
        try:
            bus.signal_unsubscribe(sub_id)
        except Exception:
            pass


def _client_location_path(bus, client_path: str) -> str | None:
    try:
        value = _props_get(bus, GEOCLUE_NAME, client_path,
                           GEOCLUE_CLIENT_IFACE, "Location")
    except Exception:
        return None
    if isinstance(value, str) and value.startswith("/") and value != "/":
        return value
    return None


def _find_modem_location_path(bus,
                              timeout_ms: int = _PROP_TIMEOUT_MS,
                              ) -> str | None:
    try:
        reply = bus.call_sync(
            MM_NAME, MM_PATH, OBJMGR_IFACE, "GetManagedObjects",
            None, _reply_type("(a{oa{sa{sv}}})"),
            Gio.DBusCallFlags.NONE, timeout_ms, None)
        objects = reply.unpack()[0]
    except Exception:
        return None
    if not isinstance(objects, dict):
        return None
    for path, ifaces in objects.items():
        try:
            names = list(ifaces.keys()) if hasattr(ifaces, "keys") \
                else list(ifaces)
        except Exception:
            continue
        if MM_LOCATION_IFACE in names:
            return str(path)
    return None


def _as_str_list(value) -> list[str]:
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _parse_3gpp(value) -> dict | None:
    if isinstance(value, dict):
        lowered = {}
        for key, item in value.items():
            try:
                lowered[str(key).strip().lower()] = _unwrap(item)
            except Exception:
                continue
        lac = lowered.get("lac")
        cid = lowered.get("cid", lowered.get("ci",
                                             lowered.get("cellid",
                                                         lowered.get("cell"))))
        return location_mod.normalize_cell_3gpp(
            lowered.get("mcc"), lowered.get("mnc"), lac, cid)
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        return location_mod.normalize_cell_3gpp(
            _unwrap(value[0]), _unwrap(value[1]),
            _unwrap(value[2]), _unwrap(value[3]))
    if isinstance(value, str):
        parts = [p for p in re.split(r"[,\s;|]+", value.strip()) if p]
        if len(parts) == 4:
            return location_mod.normalize_cell_3gpp(*parts)
    return None


def _parse_coord(value) -> tuple[float, float] | None:
    if isinstance(value, dict):
        lowered = {}
        for key, item in value.items():
            try:
                lowered[str(key).strip().lower()] = _unwrap(item)
            except Exception:
                continue
        lat = _as_float(lowered.get("lat", lowered.get("latitude")))
        lon = _as_float(lowered.get("lon", lowered.get("long",
                                                       lowered.get(
                                                           "longitude"))))
        if lat is None or lon is None:
            return None
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        lat, lon = _as_float(value[0]), _as_float(value[1])
        if lat is None or lon is None:
            return None
    elif isinstance(value, str):
        parsed = location_mod.parse_nmea_latlon(value.strip())
        if parsed is None:
            return None
        lat, lon = parsed
    else:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return (lat, lon)


def modem_candidates_from_location_dict(loc: dict, epoch: int,
                                        ) -> tuple[list[dict], dict | None]:
    """Split a ModemManager `GetLocation` a{uv} dict into GPS candidates
    for `pick_fix` plus the raw 3GPP cell (server-side lookup input)."""
    candidates: list[dict] = []
    cell: dict | None = None
    if not isinstance(loc, dict):
        return (candidates, cell)
    for key, raw in loc.items():
        try:
            source = int(key) if not isinstance(key, bool) else -1
        except (TypeError, ValueError):
            continue
        value = _unwrap(raw)
        if source == MM_SOURCE_3GPP:
            parsed = _parse_3gpp(value)
            if parsed is not None:
                cell = parsed
        elif source == MM_SOURCE_GPS_NMEA:
            for sentence in _as_str_list(value):
                parsed = location_mod.parse_nmea_latlon(sentence)
                if parsed is not None:
                    candidates.append(
                        {"lat": parsed[0], "lon": parsed[1],
                         "acc_m": None, "epoch": int(epoch),
                         "source": location_mod.SOURCE_MODEM_GPS})
                    break
        elif source == MM_SOURCE_GPS_RAW:
            parsed = _parse_coord(value)
            if parsed is not None:
                candidates.append(
                    {"lat": parsed[0], "lon": parsed[1],
                     "acc_m": None, "epoch": int(epoch),
                     "source": location_mod.SOURCE_MODEM_GPS})
    return (candidates, cell)


_MMCLI_FIELD = re.compile(r"^([^:|$]+):\s*'?([^']*?)'?\s*$")


def _mmcli_field(text: str, label: str) -> str | None:
    for line in text.splitlines():
        segment = line.strip().strip("|").strip().split("|")[-1].strip()
        match = _MMCLI_FIELD.match(segment)
        if match and match.group(1).strip().lower() == label.lower():
            value = match.group(2).strip()
            return value or None
    return None


def parse_mmcli_location_get(text: str, epoch: int,
                             ) -> tuple[list[dict], dict | None]:
    """Parse `mmcli --modem N --location-get` output into GPS candidates
    plus the 3GPP cell, the same shapes `modem_fix` feeds to `pick_fix`."""
    candidates: list[dict] = []
    cell: dict | None = None
    if not isinstance(text, str):
        return (candidates, cell)
    mcc = _mmcli_field(text, "Mobile country code")
    mnc = _mmcli_field(text, "Mobile network code")
    lac = _mmcli_field(text, "Location area code")
    cid = _mmcli_field(text, "Cell ID")
    if mcc is not None and mnc is not None and lac is not None \
            and cid is not None:
        cell = location_mod.normalize_cell_3gpp(mcc, mnc, lac, cid)
    for line in text.splitlines():
        stripped = line.strip().strip("|").strip().strip("'\"")
        if "$" not in stripped:
            continue
        token = stripped[stripped.index("$"):]
        parsed = location_mod.parse_nmea_latlon(token)
        if parsed is not None:
            candidates.append(
                {"lat": parsed[0], "lon": parsed[1],
                 "acc_m": None, "epoch": int(epoch),
                 "source": location_mod.SOURCE_MODEM_GPS})
            break
    return (candidates, cell)


def _read_modem_location_state(bus, path: str,
                               timeout_ms: int,
                               ) -> tuple[int | None, bool | None]:
    """Snapshot the modem's persistent location state before `Setup`.

    `Setup` changes the enabled sources GLOBALLY and persistently, so a
    locate must restore exactly these values in a `finally` (see
    `modem_fix`): `Enabled` is the sources bitmask, `SignalsLocation`
    is the signal bool. Best effort: unreadable properties yield None
    and skip the restore rather than guessing."""
    try:
        enabled_raw = _props_get(bus, MM_NAME, path, MM_LOCATION_IFACE,
                                 "Enabled", timeout_ms)
    except Exception:
        enabled_raw = None
    try:
        signals_raw = _props_get(bus, MM_NAME, path, MM_LOCATION_IFACE,
                                 "SignalsLocation", timeout_ms)
    except Exception:
        signals_raw = None
    enabled: int | None = None
    if isinstance(enabled_raw, bool):
        enabled = None
    elif isinstance(enabled_raw, int):
        enabled = int(enabled_raw)
    signals: bool | None = None
    if isinstance(signals_raw, bool):
        signals = bool(signals_raw)
    elif isinstance(signals_raw, int) and signals_raw in (0, 1):
        signals = bool(signals_raw)
    return (enabled, signals)


def _restore_modem_location_state(bus, path: str, enabled: int | None,
                                  signals: bool | None,
                                  timeout_ms: int) -> bool:
    """Restore the snapshot from `_read_modem_location_state`. Returns
    True when the restore call succeeded; False when skipped or failed
    (never raises, so a `finally` cannot mask the read result)."""
    if enabled is None or signals is None:
        return False
    if isinstance(enabled, bool):
        return False
    try:
        bus.call_sync(MM_NAME, path, MM_LOCATION_IFACE, "Setup",
                      GLib.Variant("(ub)", (int(enabled), bool(signals))),
                      None, Gio.DBusCallFlags.NONE, timeout_ms, None)
    except Exception:
        return False
    return True


def modem_fix(bus, *, modem_path: str | None = None,
              timeout_s: float = MODEM_TIMEOUT_S,
              now=time.time) -> dict | None:
    """One ModemManager fallback fix: enable the 3GPP/GPS sources, read
    `GetLocation`, prefer modem GPS over the cell (existing `pick_fix`
    rule). A cell-only area yields the cell record for server-side
    lookup; anything else yields None.

    `Setup` changes the modem's enabled sources GLOBALLY and
    persistently (GPS stays on after one locate: battery drain, and
    other apps see a changed modem). This snapshots `Enabled` and
    `SignalsLocation` first and restores them in a `finally` after the
    read, on success, timeout and error alike; the modem is never left
    changed when the snapshot was readable."""
    timeout_ms = max(1, int(timeout_s * 1000))
    try:
        path = modem_path or _find_modem_location_path(bus, timeout_ms)
        if not path:
            return None
        saved_enabled, saved_signals = _read_modem_location_state(
            bus, path, timeout_ms)
    except Exception:
        return None
    try:
        bus.call_sync(MM_NAME, path, MM_LOCATION_IFACE, "Setup",
                      GLib.Variant("(ub)", (MM_SETUP_SOURCES, False)),
                      None, Gio.DBusCallFlags.NONE, timeout_ms, None)
    except Exception:
        return None
    try:
        reply = bus.call_sync(
            MM_NAME, path, MM_LOCATION_IFACE, "GetLocation",
            None, _reply_type("(a{uv})"),
            Gio.DBusCallFlags.NONE, timeout_ms, None)
        loc = reply.unpack()[0]
    except Exception:
        return None
    finally:
        _restore_modem_location_state(bus, path, saved_enabled,
                                      saved_signals, timeout_ms)
    epoch = int(now())
    candidates, cell = modem_candidates_from_location_dict(loc, epoch)
    best = location_mod.pick_fix(candidates)
    if best is not None:
        return best
    if cell is not None:
        return {"source": location_mod.SOURCE_MODEM_CELL,
                "epoch": epoch, "cell": cell}
    return None


def _open_system_bus():
    """Real system bus, opened lazily so import never touches D-Bus."""
    from gi.repository import Gio as _Gio
    return _Gio.bus_get_sync(_Gio.BusType.SYSTEM, None)


def build_read_fix(*, desktop_id: str = GEOCLUE_DESKTOP_ID,
                   system_bus_factory=None, modem_bus_factory=None,
                   geoclue_timeout_s: float = GEOCLUE_TIMEOUT_S,
                   modem_timeout_s: float = MODEM_TIMEOUT_S,
                   now=time.time):
    """Zero-arg `read_fix` for `AgentDeps`: GeoClue first, modem
    fallback. Privacy events stay with the agent's `on_privacy_event`
    hook, which fires once per successful read; pass no hook here."""
    sys_factory = system_bus_factory or _open_system_bus
    mm_factory = modem_bus_factory or sys_factory

    def _read() -> dict | None:
        try:
            bus = sys_factory()
        except Exception:
            bus = None
        if bus is not None:
            fix = geoclue_fix(bus, desktop_id=desktop_id,
                              timeout_s=geoclue_timeout_s, now=now)
            if fix is not None:
                return fix
        try:
            modem_bus = mm_factory()
        except Exception:
            modem_bus = None
        if modem_bus is not None:
            return modem_fix(modem_bus, timeout_s=modem_timeout_s, now=now)
        return None

    return _read
