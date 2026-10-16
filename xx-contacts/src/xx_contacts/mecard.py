"""L4 Me card without GTK: designate, resolve, and share one contact.

The Me card is a designated contact UID kept in app config (Linux has
no self-profile concept like ``ContactsContract.Profile``). Pure
module so pytest runs headless; the GTK page (``mecard_ui``) renders
whatever :func:`resolve_me` returns.
"""

from __future__ import annotations

from xx_contacts.store import NotFoundError


def get_me_uid(config) -> str:
    """The designated Me UID, or ``""`` when none is set."""
    return (getattr(config, "me_uid", "") or "").strip()


def set_me_uid(config, config_path: str, uid: str) -> None:
    """Designate ``uid`` as Me and persist the config."""
    config.me_uid = (uid or "").strip()
    config.save(config_path)


def clear_me_uid(config, config_path: str) -> None:
    """Remove the Me designation and persist the config."""
    config.me_uid = ""
    config.save(config_path)


def is_me(config, uid: str) -> bool:
    """True when ``uid`` is the designated Me contact."""
    mine = get_me_uid(config)
    return bool(mine) and mine == (uid or "").strip()


def resolve_me(store, config):
    """The Me record, or None when unset or the contact is gone."""
    uid = get_me_uid(config)
    if not uid:
        return None
    try:
        return store.get(uid)
    except NotFoundError:
        return None


def me_share_vcard(store, config, photo_bytes: bytes | None = None) -> str:
    """vCard 4.0 text of the Me contact for sharing; ``""`` when unset."""
    from xx_contacts import vcard as vcard_module

    record = resolve_me(store, config)
    if record is None:
        return ""
    return vcard_module.contact_to_vcard(record, photo_bytes=photo_bytes)
