from __future__ import annotations

from dataclasses import dataclass

from gi.repository import Gio, GLib

from app_standby import launch_with_pid


@dataclass(frozen=True)
class AppEntry:
    app_id: str
    name: str
    description: str
    executable: str
    search_text: str
    desktop_app: Gio.DesktopAppInfo


def _current_desktops() -> set[str]:
    """XDG desktop ids for OnlyShowIn/NotShowIn matching, casefolded."""
    import os
    raw = os.environ.get('XDG_CURRENT_DESKTOP', '')
    return {part.strip().casefold() for part in raw.split(':') if part.strip()}


def _string_list(info: object, key: str) -> list[str]:
    try:
        values = info.get_string_list(key)
    except Exception:
        return []
    return [str(v) for v in values or []]


def desktop_visible(info: object, desktops: set[str] | None = None) -> bool:
    """Explicit OnlyShowIn/NotShowIn check. `should_show()` depends on the
    session exporting XDG_CURRENT_DESKTOP correctly, so match the lists
    directly and deterministically instead of trusting the environment."""
    if desktops is None:
        desktops = _current_desktops()
    only = {v.casefold() for v in _string_list(info, 'OnlyShowIn')}
    if only and desktops.isdisjoint(only):
        return False
    excluded = {v.casefold() for v in _string_list(info, 'NotShowIn')}
    if excluded and not desktops.isdisjoint(excluded):
        return False
    return True


# Other shells and standalone launchers never appear in the drawer, not
# even in search (design.md "App drawer"). Matched with or without the
# .desktop suffix; user-hidden apps (config.hidden_apps) stay searchable,
# these do not.
_SHELL_LAUNCHER_IDS = frozenset({
    'gnome-shell', 'org.gnome.shell',
    'phosh', 'mobi.phosh.shell',
    'squeekboard', 'sm.puri.squeekboard',
    'wofi', 'rofi', 'bemenu', 'fuzzel', 'dmenu',
    'ulauncher', 'kupfer', 'albert', 'synapse', 'gnome-do',
    'krunner', 'org.kde.krunner',
    'io.piercingxx.xxwm',
})


def _normalized_app_id(app_id: str) -> str:
    normalized = app_id[:-8] if app_id.endswith('.desktop') else app_id
    return normalized.strip().casefold()


def _is_shell_or_launcher(app_id: str) -> bool:
    return _normalized_app_id(app_id) in _SHELL_LAUNCHER_IDS


# Stock apps superseded by an XX app: when the XX replacement is installed,
# the stock entry leaves the drawer AND search (user-hidden apps stay
# searchable; these do not). Only the pairs in this table, matched with or
# without the .desktop suffix.
_SUPERSEDED_STOCK_APPS = {
    'org.gnome.Contacts': 'io.piercingxx.XXContacts',
    'org.gnome.Calls': 'io.piercingxx.XXDialer',
    'sm.puri.Chatty': 'io.piercingxx.TxxT',
}

_SUPERSEDED_BY = {
    _normalized_app_id(stock): _normalized_app_id(replacement)
    for stock, replacement in _SUPERSEDED_STOCK_APPS.items()
}


def _superseded(app_id: str, installed: set[str]) -> bool:
    """True when `app_id` is a superseded stock app whose XX replacement
    is installed (i.e. listed alongside it)."""
    replacement = _SUPERSEDED_BY.get(_normalized_app_id(app_id))
    return replacement is not None and replacement in installed


class AppIndex:
    def __init__(self) -> None:
        self.entries: list[AppEntry] = []

    def refresh(self) -> list[AppEntry]:
        entries: list[AppEntry] = []
        seen: set[str] = set()
        desktops = _current_desktops()

        for info in Gio.AppInfo.get_all():
            if not isinstance(info, Gio.DesktopAppInfo):
                continue
            if not info.should_show() or info.get_nodisplay():
                continue
            if not desktop_visible(info, desktops):
                continue

            app_id = info.get_id() or ''
            if not app_id or app_id in seen:
                continue
            if _is_shell_or_launcher(app_id):
                continue

            name = (info.get_display_name() or info.get_name() or '').strip()
            if not name:
                continue

            description = (info.get_description() or info.get_generic_name() or '').strip()
            executable = (info.get_executable() or '').strip()
            keywords = info.get_keywords() or []
            search_text = ' '.join(part for part in [name, description, executable, ' '.join(keywords), app_id] if part).casefold()

            entries.append(
                AppEntry(
                    app_id=app_id,
                    name=name,
                    description=description,
                    executable=executable,
                    search_text=search_text,
                    desktop_app=info,
                )
            )
            seen.add(app_id)

        installed = {_normalized_app_id(entry.app_id) for entry in entries}
        entries = [entry for entry in entries
                   if not _superseded(entry.app_id, installed)]
        self.entries = sorted(entries, key=lambda entry: entry.name.casefold())
        return self.entries

    def top(self, limit: int = 8) -> list[AppEntry]:
        return self.entries[:limit]

    def resolve(self, app_ids: list[str]) -> list[AppEntry]:
        by_id = {entry.app_id: entry for entry in self.entries}
        return [by_id[app_id] for app_id in app_ids if app_id in by_id]

    def search(self, query: str) -> list[AppEntry]:
        trimmed = query.strip().casefold()
        if not trimmed:
            return list(self.entries)
        prefix_matches = [e for e in self.entries if e.name.casefold().startswith(trimmed)]
        contains_matches = [e for e in self.entries if trimmed in e.search_text and e not in prefix_matches]
        return prefix_matches + contains_matches

    def launch(self, entry: AppEntry, tracker: object = None) -> tuple[bool, str | None]:
        """Launch an entry, thawing it in standby first and placing the
        child in its own scope. Tracker errors never break the launch."""
        if tracker is not None:
            try:
                tracker.thaw_for_launch(entry.app_id)
            except Exception:
                pass
        try:
            if tracker is not None:
                launch_with_pid(
                    entry.desktop_app,
                    on_pid=lambda pid: tracker.track(entry.app_id, pid),
                )
            else:
                entry.desktop_app.launch([], None)
        except GLib.Error as error:
            return False, error.message
        return True, None
