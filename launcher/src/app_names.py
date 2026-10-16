"""Shared application display-name helpers.

One fallback rule for every surface: when no desktop entry matches an
app id, show the id's last component (never the raw dotted id).
"""
from __future__ import annotations


def fallback_app_name(app_id: str) -> str:
    """Last id component when no desktop entry exists (never the raw id)."""
    base = (app_id or '').strip()
    if base.endswith('.desktop'):
        base = base[:-len('.desktop')]
    if '/' in base:
        base = base.rsplit('/', 1)[-1]
    if '.' in base:
        base = base.rsplit('.', 1)[-1]
    return base or app_id


def friendly_app_name(win, app_id: str) -> str:
    """Desktop file Name via AppIndex, else the id's last component."""
    try:
        entries = win.app_index.entries
    except Exception:
        return fallback_app_name(app_id)
    wanted = (app_id or '').strip()
    stripped = wanted[:-len('.desktop')] if wanted.endswith('.desktop') else wanted
    for entry in entries or ():
        entry_id = getattr(entry, 'app_id', None) or ''
        if entry_id == wanted or entry_id == stripped or (
                entry_id.endswith('.desktop')
                and entry_id[:-len('.desktop')] == stripped):
            try:
                return win.config.label_for(app_id, entry.name)
            except Exception:
                return getattr(entry, 'name', app_id) or app_id
    return fallback_app_name(app_id)
