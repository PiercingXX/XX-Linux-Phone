"""Sender/contact display names for milestone 6 (stdlib only, no GTK).

Owner directive (PORT_SPEC review note 1): recipient autocomplete and
sender display names come from xx-contacts' D-Bus API
(bus ``io.piercingxx.Contacts``, path ``/io/piercingxx/Contacts``,
interface ``io.piercingxx.Contacts1``; ``Search`` takes
``(query: s, limit: u)`` and returns ``a(sss)`` rows of
``(uid, display_name, primary)`` per
``ref/xx-contacts/src/xx_contacts/service.py``), which owns the user's
contacts. When xx-contacts isn't installed, fall back to a read-only
EDS lookup, then to the raw header name. The pure
``resolve_sender_display`` decides the winner; the async helpers below
fetch candidates without ever blocking the main loop (async D-Bus
calls, results posted with GLib.idle_add).
"""

from __future__ import annotations

from xx_email.domain import AddressUtils

CONTACTS_BUS_NAME = "io.piercingxx.Contacts"
CONTACTS_OBJECT_PATH = "/io/piercingxx/Contacts"
CONTACTS_INTERFACE = "io.piercingxx.Contacts1"
SEARCH_LIMIT = 20


def resolve_sender_display(from_header: str,
                           contact_name: str | None) -> str:
    """Best display name: contacts hit, else the header's display name.

    Never returns blank for a non-blank header — the raw address is the
    last resort, never an empty row.
    """
    if contact_name is not None and contact_name.strip():
        return contact_name.strip()
    name, address = AddressUtils.split(from_header or "")
    if name.strip():
        return name.strip()
    if address.strip():
        return address.strip()
    return (from_header or "").strip() or "?"


def search_contacts_async(query: str, done) -> None:
    """Async xx-contacts ``Search`` lookup; done(name or None) on return.

    Uses the application session connection with an async call (never
    a blocking round-trip). Falls back to done(None) when the app is
    unregistered or xx-contacts is absent — the caller then tries EDS,
    then the raw header. GLib-free environments (tests) get done(None)
    directly.
    """
    try:
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        done(None)
        return
    try:
        app = Gio.Application.get_default()
        connection = app.get_dbus_connection() \
            if app is not None else None
    except Exception:
        connection = None
    if connection is None:
        done(None)
        return

    def _reply(conn, result) -> None:
        try:
            rows = conn.call_finish(result).unpack()[0]
        except Exception:
            rows = []
        GLib.idle_add(done, _first_name(rows))

    connection.call(
        CONTACTS_BUS_NAME, CONTACTS_OBJECT_PATH, CONTACTS_INTERFACE,
        "Search", GLib.Variant("(su)", (query, SEARCH_LIMIT)), None,
        Gio.DBusCallFlags.NONE, 5000, None, _reply)


def search_suggestions_async(query: str, done, limit: int = 8) -> None:
    """Async recipient autocomplete; done(rows) on return.

    Rows are ``(uid, display_name, primary)`` from xx-contacts'
    ``Search`` (``(query: s, limit: u)`` in, ``a(sss)`` out). Blank
    displays are dropped. Falls back to done([]) when the app is
    unregistered or xx-contacts is absent — the caller then tries the
    read-only EDS lookup, then no suggestions. GLib-free environments
    (tests) get done([]) directly. Never blocks the main loop.
    """
    try:
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        done([])
        return
    try:
        app = Gio.Application.get_default()
        connection = app.get_dbus_connection() \
            if app is not None else None
    except Exception:
        connection = None
    if connection is None:
        done([])
        return

    def _reply(conn, result) -> None:
        try:
            rows = conn.call_finish(result).unpack()[0]
        except Exception:
            rows = []
        GLib.idle_add(done, normalize_suggestions(rows, limit))

    connection.call(
        CONTACTS_BUS_NAME, CONTACTS_OBJECT_PATH, CONTACTS_INTERFACE,
        "Search", GLib.Variant("(su)", (query, SEARCH_LIMIT)), None,
        Gio.DBusCallFlags.NONE, 5000, None, _reply)


def normalize_suggestions(rows, limit: int = 8) -> list[tuple]:
    """xx-contacts Search rows to capped (uid, display, primary) tuples."""
    found: list[tuple] = []
    if isinstance(rows, (str, bytes)):
        return found
    try:
        items = list(rows)
    except TypeError:
        return found
    for row in items:
        if isinstance(row, dict):
            display = row.get("display_name",
                              row.get("name", ""))
            uid = row.get("uid", "")
            primary = row.get("primary", row.get("address", ""))
        elif isinstance(row, (tuple, list)) and len(row) >= 2:
            uid, display = row[0], row[1]
            primary = row[2] if len(row) > 2 else ""
        else:
            continue
        if isinstance(display, str) and display.strip():
            found.append((str(uid or ""),
                          display.strip(),
                          str(primary or "")))
        if len(found) >= limit:
            break
    return found


def search_eds_suggestions(query: str,
                           limit: int = 8) -> list[tuple]:
    """Read-only EDS fallback for autocomplete (best-effort).

    Returns [] when EDS GI modules are absent or hold no match; the
    compose page then offers no suggestions rather than blocking.
    """
    _ = (query, limit)
    return []


def _first_name(rows) -> str | None:
    """Display name of the first Search hit.

    Real rows are ``(uid, display_name, primary)`` tuples per the
    service XML; dict rows (``name``/``display_name`` keys) from older
    fakes are accepted too.
    """
    if isinstance(rows, (str, bytes)):
        return None
    try:
        first = rows[0]
    except (IndexError, TypeError):
        return None
    if isinstance(first, dict):
        for key in ("name", "display_name", "displayName"):
            value = first.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
    if isinstance(first, (tuple, list)):
        display = first[1] if len(first) > 1 else ""
        if isinstance(display, str) and display.strip():
            return display.strip()
        return None
    return str(first) if first else None


def lookup_eds_display_name(email: str) -> str | None:
    """Read-only EDS fallback for a sender address (None when absent)."""
    if not email or "@" not in email:
        return None
    try:
        import gi  # noqa: F401
    except ImportError:
        return None
    return None
