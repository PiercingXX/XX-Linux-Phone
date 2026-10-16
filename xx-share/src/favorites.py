"""Favorites: pinned peer fingerprints with optional auto-accept.

Thin helpers over ShareConfig.data so both the service (auto-accept
policy) and the GTK window (toggle rows) share one tested shape. The
fingerprint is the device certificate SHA-256 shown in the UI.
"""
from __future__ import annotations


def _clean_fingerprint(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def favorites_list(config: object) -> list[str]:
    raw = config.data.get("favorites", [])  # type: ignore[union-attr]
    if not isinstance(raw, list):
        return []
    seen: list[str] = []
    for item in raw:
        cleaned = _clean_fingerprint(item)
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen


def is_favorite(config: object, fingerprint: str) -> bool:
    return _clean_fingerprint(fingerprint) in favorites_list(config)


def set_favorite(config: object, fingerprint: str, favorite: bool) -> list[str]:
    cleaned = _clean_fingerprint(fingerprint)
    if not cleaned:
        return favorites_list(config)
    current = favorites_list(config)
    if favorite and cleaned not in current:
        current.append(cleaned)
    elif not favorite and cleaned in current:
        current.remove(cleaned)
    config.data["favorites"] = current  # type: ignore[union-attr]
    config.save()  # type: ignore[union-attr]
    return list(current)


def auto_accept_enabled(config: object) -> bool:
    return bool(config.data.get("auto_accept_favorites"))  # type: ignore[union-attr]


def set_auto_accept(config: object, enabled: bool) -> None:
    config.data["auto_accept_favorites"] = bool(enabled)  # type: ignore[union-attr]
    config.save()  # type: ignore[union-attr]
