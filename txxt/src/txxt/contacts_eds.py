"""Read-only EDS name lookup (second fallback behind xx-contacts).

Best-effort only: any failure (EDS absent, registry unreachable, query
rejected) returns None and the caller falls through to bare numbers.
Never raises, never writes, never prompts.
"""

from __future__ import annotations


def eds_lookup_name(number: str) -> str | None:
    """Display name for ``number`` from the EDS address book, or None."""
    needle = (number or "").strip()
    if not needle:
        return None
    try:
        gi, EBook, EDataServer = _imports()
    except Exception:
        return None
    try:
        registry = EDataServer.SourceRegistry.new_sync(None)
        sources = registry.list_sources(EDataServer.SOURCE_EXTENSION_ADDRESS_BOOK)
        for source in sources or []:
            name = _search_source(EBook, source, needle)
            if name:
                return name
    except Exception:
        return None
    return None


def _imports():
    import gi

    gi.require_version("EBook", "1.2")
    gi.require_version("EDataServer", "1.2")
    from gi.repository import EBook, EDataServer

    return gi, EBook, EDataServer


def _search_source(EBook, source, needle: str) -> str | None:
    digits = "".join(ch for ch in needle if ch.isdigit())
    queries = [f'(phone_matches "{needle}")']
    if digits and digits != needle:
        queries.append(f'(phone_matches "{digits}")')
    sexp = queries[0] if len(queries) == 1 else "(or " + " ".join(queries) + ")"
    try:
        client = EBook.BookClient.connect_sync(source, None)
        found, _contacts = client.get_contacts_sync(sexp, None)
    except Exception:
        return None
    if not found:
        return None
    for contact in found:
        try:
            name = contact.get_property("full-name")
        except Exception:
            name = None
        if name and str(name).strip():
            return str(name).strip()
    return None
