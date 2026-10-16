"""L5 EDS interop: one-time read-only import, optional one-way publish.

The database owns the contacts (Revision 2); Evolution Data Server is
only a mirror other apps can read. Import never writes to EDS, and
publish never reads from it: the two directions share nothing but the
vCard text. Publishing stays off unless ``AppConfig.publish_to_eds``
is set, so a fresh install writes nowhere outside its own database.

Import-safe without EDS typelibs: the GI import happens lazily inside
the real client, so headless machines and tests use
:class:`FakeEBookClient`, which mirrors the real method shapes exactly.
"""

from __future__ import annotations

#: Address-book source name the publisher mirrors into.
EDS_SOURCE_NAME = "XX Contacts"


def is_available() -> bool:
    """True when the EDS GI typelibs are installed."""
    try:
        import gi

        gi.require_version("EBook", "3.0")
        gi.require_version("EBookContacts", "1.2")
    except (ImportError, ValueError):
        return False
    return True


class FakeEBookClient:
    """In-memory EDS stand-in: ``uid -> vCard`` with the real shapes."""

    def __init__(self, cards: dict[str, str] | None = None) -> None:
        self._cards: dict[str, str] = dict(cards or {})

    def list_uids(self) -> list[str]:
        return sorted(self._cards)

    def list_vcards(self) -> list[str]:
        return [self._cards[uid] for uid in sorted(self._cards)]

    def upsert(self, uid: str, vcard_text: str) -> None:
        self._cards[str(uid)] = str(vcard_text)

    def remove(self, uid: str) -> None:
        self._cards.pop(str(uid), None)


class EBookClient:
    """Thin wrapper over the default (or named) EDS address book.

    Every method raises ``RuntimeError`` when EDS is not installed;
    callers surface that as "GNOME Contacts is not available" and keep
    working. Blocking sync calls are for worker threads only, never
    the GTK main loop.
    """

    def __init__(self, source_uid: str | None = None) -> None:
        if not is_available():
            raise RuntimeError("EDS typelibs are not installed")
        from gi.repository import EBook

        registry = EBook.SourceRegistry.new_sync(None)
        if source_uid:
            source = registry.ref_source(source_uid)
        else:
            source = registry.ref_default_address_book()
        self._client = EBook.BookClient.connect_sync(source, None)

    def list_vcards(self) -> list[str]:
        _ok, contacts = self._client.get_contacts_sync(
            "(contains \"x-evolution-any-field\" \"\")", None)
        vcards: list[str] = []
        for contact in contacts or ():
            if isinstance(contact, str):
                vcards.append(contact)
                continue
            text = None
            for attr in ("get_const_vcard_string", "dup_vcard_string"):
                getter = getattr(contact, attr, None)
                if callable(getter):
                    try:
                        text = getter()
                    except Exception:
                        text = None
                    if text:
                        break
            if not text:
                try:
                    text = contact.get_property("vcard-string")
                except Exception:
                    text = None
            if text:
                vcards.append(text)
            else:
                raise RuntimeError("could not read EDS contacts")
        return vcards

    def list_uids(self) -> list[str]:
        from xx_contacts import vcard as vcard_module

        uids: list[str] = []
        for text in self.list_vcards():
            _data, hint = vcard_module.contact_from_vcard(text)
            if hint:
                uids.append(hint)
        return sorted(uids)

    def upsert(self, uid: str, vcard_text: str) -> None:
        from gi.repository import EBookContacts

        contact = EBookContacts.EContact.new_from_vcard(vcard_text)
        try:
            self._client.modify_contact_sync(contact, None)
        except Exception:
            self._client.add_contact_sync(contact, None)

    def remove(self, uid: str) -> None:
        from gi.repository import EBookContacts

        try:
            contact = EBookContacts.EContact.new_from_vcard(
                f"BEGIN:VCARD\r\nVERSION:4.0\r\nUID:{uid}\r\n"
                f"FN:removed\r\nEND:VCARD\r\n")
            self._client.remove_contact_sync(contact, None)
        except Exception as exc:
            raise RuntimeError(f"could not remove EDS contact: {exc}"
                               ) from exc


def read_import_cards(client, default_region: str = "US"):
    """Parse every vCard the EDS client holds; the client is untouched."""
    from xx_contacts import import_export as import_export_module

    cards = []
    for text in client.list_vcards():
        cards.extend(import_export_module.parse_text(text or "",
                                                    default_region or "US"))
    return cards


def publish_uids(records) -> dict[str, str]:
    """vCard per database uid for the one-way EDS mirror (no photos)."""
    from xx_contacts import vcard as vcard_module

    return {record.uid: vcard_module.contact_to_vcard(record)
            for record in records or () if getattr(record, "uid", "")}


def sync_to_eds(records, client) -> tuple[int, int]:
    """Mirror the database into EDS; return ``(written, removed)``.

    One way only: every database contact is written, stale EDS uids
    are removed, and nothing is ever read back. Callers debounce this
    off the service ``Changed`` signal.
    """
    wanted = publish_uids(records)
    try:
        stale = [uid for uid in client.list_uids() if uid not in wanted]
    except Exception:
        stale = []
    for uid, text in wanted.items():
        client.upsert(uid, text)
    removed = 0
    for uid in stale:
        try:
            client.remove(uid)
        except Exception:
            continue
        removed += 1
    return len(wanted), removed


class EdsPublisher:
    """Debounced one-way mirror: schedule on ``Changed``, flush later."""

    DEBOUNCE_MS = 2000

    def __init__(self, make_client, read_records, enabled=False) -> None:
        self._make_client = make_client
        self._read_records = read_records
        self.enabled = bool(enabled)
        self._pending_id = 0

    def schedule(self) -> None:
        """Queue a mirror pass; only the last one in the window runs."""
        if not self.enabled:
            return
        try:
            from gi.repository import GLib
        except (ImportError, ValueError):
            return
        if self._pending_id:
            try:
                GLib.source_remove(self._pending_id)
            except Exception:
                pass
        self._pending_id = GLib.timeout_add(self.DEBOUNCE_MS, self._flush)

    def _flush(self) -> bool:
        self._pending_id = 0
        if not self.enabled:
            return False
        try:
            records = self._read_records()
        except Exception:
            return False
        try:
            client = self._make_client()
        except Exception:
            return False
        try:
            sync_to_eds(records, client)
        except Exception:
            pass
        return False
