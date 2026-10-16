"""Location sourcing: GeoClue2 first, ModemManager as fallback.

Only pure parsing/selection lives here so it is unit-testable without
GPS, D-Bus or a modem. Live bus access stays behind injected callables
(see PORT_SPEC.md); nothing in this module touches the system bus.

GeoClue2: system bus `org.freedesktop.GeoClue2`, Manager at
`/org/freedesktop/GeoClue2/Manager`. Clients create a Client object,
set DesktopId, call Start, and read Location (Properties: Latitude,
Longitude, Accuracy, Timestamp). Authorization is per-app: the daemon
asks the registered Agent's AuthorizeApp(app_id, req_accuracy) unless
/etc/geoclue/geoclue.conf already pins the app (allowed=true/false).
Only ONE agent is served system-wide, so xx-find reuses the shell's
agent (quick_actions.LocationState) instead of registering its own.

ModemManager fallback: `org.freedesktop.ModemManager1`, per-modem
`org.freedesktop.ModemManager1.Modem.Location` interface: enable
sources with Setup(signals, then GetLocation returns a{uv} with
3GPP (MCC/MNC/LAC/CI) and GPS (NMEA traces / longitude/latitude).
xx-find parses NMEA GGA/RMC sentences itself (below) and normalizes
3GPP cells for later server-side lookup.
"""

from __future__ import annotations

GEOCLUE_ACCURACY_NONE = 0
GEOCLUE_ACCURACY_EXACT = 8
SOURCE_GEOCLUE = "geoclue"
SOURCE_MODEM_GPS = "modem-gps"
SOURCE_MODEM_CELL = "modem-cell"
SOURCE_PRECEDENCE = (SOURCE_GEOCLUE, SOURCE_MODEM_GPS, SOURCE_MODEM_CELL)


def _nmea_degrees(raw: str, direction: str, width: int) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    degrees = int(value // 100)
    minutes = value - degrees * 100
    decimal = degrees + minutes / 60.0
    if direction in ("S", "W"):
        decimal = -decimal
    elif direction not in ("N", "E"):
        return None
    if not (-90.0 - 180.0 <= decimal <= 90.0 + 180.0):
        return None
    del width
    return decimal


def parse_nmea_latlon(sentence: str) -> tuple[float, float] | None:
    """Parse $GPGGA/$GPRMC (or GN- talker) into (lat, lon). None when the
    sentence carries no fix, fails checksum-free field checks, or is not
    a position sentence. No checksum enforcement: modems may elide it."""
    if not isinstance(sentence, str):
        return None
    text = sentence.strip()
    if text.startswith("$"):
        text = text[1:]
    fields = text.split(",")
    if not fields:
        return None
    kind = fields[0][-3:] if len(fields[0]) >= 3 else ""
    lat = lon = None
    if kind == "GGA":
        if len(fields) < 6 or not fields[2] or not fields[4]:
            return None
        if fields[6] == "0":
            return None
        lat = _nmea_degrees(fields[2], fields[3], 2)
        lon = _nmea_degrees(fields[4], fields[5], 3)
    elif kind == "RMC":
        if len(fields) < 7 or fields[2] != "A":
            return None
        lat = _nmea_degrees(fields[3], fields[4], 2)
        lon = _nmea_degrees(fields[5], fields[6], 3)
    else:
        return None
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return (lat, lon)


def normalize_cell_3gpp(mcc: str | int, mnc: str | int,
                        lac: str | int, cid: str | int) -> dict | None:
    """Normalize one 3GPP cell identity for server-side lookup. None when
    any field is missing or non-numeric."""
    try:
        cell = {"mcc": int(mcc), "mnc": int(mnc),
                "lac": int(lac), "cid": int(cid)}
    except (TypeError, ValueError):
        return None
    if cell["mcc"] <= 0 or cell["cid"] < 0:
        return None
    return cell


def pick_fix(candidates: list[dict]) -> dict | None:
    """Choose the best fix: highest-precedence source wins; ties break on
    freshest epoch, then smallest accuracy radius. Each candidate needs
    {lat, lon, source, epoch}; acc_m optional."""
    best = None
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        try:
            source = cand["source"]
            rank = SOURCE_PRECEDENCE.index(source)
            epoch = int(cand["epoch"])
            lat = float(cand["lat"])
            lon = float(cand["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        acc = cand.get("acc_m")
        try:
            acc_f = float(acc) if acc is not None else float("inf")
        except (TypeError, ValueError):
            acc_f = float("inf")
        key = (-rank, epoch, -acc_f)
        if best is None or key > best[0]:
            best = (key, cand)
    return best[1] if best else None
