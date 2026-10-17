"""Starred contacts through the public xx-contacts D-Bus API.

Reads only: ``List`` enumerate → ``Get`` vCards → keep ``X-STARRED:1``
rows. Every blocking call runs on the caller's (worker) thread; the
UI posts results back with GLib.idle_add. Contact numbers are never
logged — failures name the uid only.
"""

from __future__ import annotations

from dataclasses import dataclass

from .favourite_pick import Number, NumberType, classify_label, primary_number

BUS_NAME = "io.piercingxx.Contacts"
OBJECT_PATH = "/io/piercingxx/Contacts"
INTERFACE = "io.piercingxx.Contacts1"

MAX_FAVOURITES = 8
CALL_TIMEOUT_MS = 5000


@dataclass
class Favourite:
    uid: str
    name: str
    number: str


def _reply_type(signature: str | None):
    if signature is None:
        return None
    from gi.repository import GLib

    return GLib.VariantType.new(signature)


def _call_sync(connection, method: str, parameters,
               reply_signature: str | None,
               timeout_ms: int = CALL_TIMEOUT_MS):
    from gi.repository import Gio

    reply = connection.call_sync(
        BUS_NAME, OBJECT_PATH, INTERFACE, method, parameters,
        _reply_type(reply_signature), Gio.DBusCallFlags.NONE,
        timeout_ms, None)
    if reply is None:
        return ()
    return reply.unpack()


def parse_vcard_phones(vcard: str) -> tuple[str, list[Number]]:
    """(display name, numbers) from a vCard; mobile types classified."""
    name = ""
    numbers: list[Number] = []
    for raw_line in (vcard or "").splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        head, _, value = line.partition(":")
        head_up = head.upper()
        if head_up == "FN" and not name:
            name = value.strip()
        elif head_up == "N" and not name:
            parts = [p.strip() for p in value.split(";")]
            name = " ".join(p for p in (parts[1:2] + parts[:1]) if p)
        elif head_up.startswith("TEL"):
            label = "OTHER"
            if ";" in head:
                for param in head.split(";")[1:]:
                    token = param.split("=")[-1].strip()
                    if token.upper() in ("CELL", "MOBILE", "CELLULAR",
                                         "HOME", "WORK"):
                        label = token
                        break
            if value.strip():
                numbers.append(Number(value.strip(),
                                      classify_label(label)))
    return name, numbers


def is_starred(vcard: str) -> bool:
    for raw_line in (vcard or "").splitlines():
        line = raw_line.strip().upper()
        if line in ("X-STARRED:1", "X-STARRED:TRUE", "X-STARRED:YES"):
            return True
    return False


def list_starred_sync(connection) -> list[Favourite]:
    """Starred contacts with one primary number each (uid order)."""
    from gi.repository import GLib

    (revision, changed, _deleted) = _call_sync(
        connection, "List", GLib.Variant("(t)", (0,)), "(tasas)")
    favourites: list[Favourite] = []
    for uid in changed or []:
        try:
            (vcard,) = _call_sync(connection, "Get",
                                  GLib.Variant("(s)", (uid,)), "(s)")
        except Exception:
            continue
        if not is_starred(vcard):
            continue
        name, numbers = parse_vcard_phones(vcard)
        picked = primary_number(numbers)
        if picked is None:
            continue
        favourites.append(Favourite(uid, name or "Unknown", picked.value))
        if len(favourites) >= MAX_FAVOURITES:
            break
    favourites.sort(key=lambda fav: fav.name.casefold())
    return favourites[:MAX_FAVOURITES]


def tel_uri(number: str) -> str:
    digits = "".join(ch for ch in number if ch.isalnum() or ch in "+*#")
    return f"tel:{digits}"


def primary_label(number_type: NumberType) -> str:
    return {"mobile": "Mobile", "home": "Home",
            "work": "Work"}.get(number_type.value, "Phone")
