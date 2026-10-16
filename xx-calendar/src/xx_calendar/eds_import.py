"""One-time EDS import into the own store (Revision 2 §4.4).

Read-only: this module never writes to Evolution Data Server, it only
reads VEVENTs and feeds them through the same UID-dedupe pipeline as
the ``.ics`` import. It runs only when EDS is installed; otherwise
:func:`is_eds_available` is False and :func:`import_from_eds` is a
no-op reporting ``(0, 0, 0)``. Tests inject a fake EBook client, so no
EDS installation is needed headless.

Pure Python, no network, no GTK main-loop blocking: callers run this
on a worker thread like the ``.ics`` import.
"""
from __future__ import annotations


def is_eds_available() -> bool:
    """True when the EDS GI bindings import."""
    try:
        import gi  # noqa: F401

        gi.require_version("EBook", "1.2")
        gi.require_version("EDataServer", "1.2")
        from gi.repository import EBook, EDataServer  # noqa: F401
    except Exception:
        return False
    return True


def vevents_from_client(client) -> list[str]:
    """Pull raw VEVENT strings from an EBook client, read-only.

    The real client exposes ``get_contacts``/``get_object_list`` style
    calls; the fake in tests exposes any of ``list_vevents``,
    ``get_vevents``, ``list_objects``, or ``vevents``. Unknown shapes
    yield ``[]`` instead of raising.
    """
    for attr in ("list_vevents", "get_vevents", "list_objects",
                 "get_object_list", "vevents"):
        try:
            candidate = getattr(client, attr, None)
        except Exception:
            continue
        if candidate is None:
            continue
        try:
            items = candidate() if callable(candidate) else candidate
        except Exception:
            continue
        if isinstance(items, (str, bytes)):
            return [items if isinstance(items, str)
                    else items.decode("utf-8", "replace")]
        try:
            out = [item if isinstance(item, str)
                   else item.decode("utf-8", "replace")
                   if isinstance(item, bytes) else str(item)
                   for item in list(items)]
        except Exception:
            continue
        return [text for text in out if "VEVENT" in text.upper()]
    return []


def import_vevents(store, calendar_id: str,
                   vevent_texts) -> tuple[int, int, int]:
    """Import already-fetched VEVENT strings through the service pipeline."""
    from xx_calendar.service import CalendarService

    service = CalendarService(store, start_worker=False)
    try:
        combined = "\r\n".join(list(vevent_texts))
        return service.import_ics(calendar_id, combined)
    finally:
        service.stop()


def import_from_client(store, calendar_id: str,
                       client) -> tuple[int, int, int]:
    """One-time import from an EBook client object (real or fake)."""
    return import_vevents(store, calendar_id, vevents_from_client(client))


def import_from_eds(store, calendar_id: str,
                    client=None) -> tuple[int, int, int]:
    """One-time EDS import; no-op ``(0, 0, 0)`` when EDS is missing.

    Pass an explicit fake *client* in tests; with None, the real EDS
    registry is consulted only when :func:`is_eds_available` is True.
    """
    if client is not None:
        return import_from_client(store, calendar_id, client)
    if not is_eds_available():
        return (0, 0, 0)
    try:
        from gi.repository import EDataServer

        registry = EDataServer.SourceRegistry.new_sync(None)
        sources = registry.list_sources(None) or []
    except Exception:
        return (0, 0, 0)
    created = updated = skipped = 0
    for source in sources:
        try:
            vevents = vevents_from_client(source)
        except Exception:
            continue
        if not vevents:
            continue
        piece = import_vevents(store, calendar_id, vevents)
        created += piece[0]
        updated += piece[1]
        skipped += piece[2]
    return (int(created), int(updated), int(skipped))
