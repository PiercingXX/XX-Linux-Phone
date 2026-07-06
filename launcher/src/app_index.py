from __future__ import annotations

from dataclasses import dataclass

from gi.repository import Gio, GLib


@dataclass(frozen=True)
class AppEntry:
    app_id: str
    name: str
    description: str
    executable: str
    search_text: str
    desktop_app: Gio.DesktopAppInfo


class AppIndex:
    def __init__(self) -> None:
        self.entries: list[AppEntry] = []

    def refresh(self) -> list[AppEntry]:
        entries: list[AppEntry] = []
        seen: set[str] = set()

        for info in Gio.AppInfo.get_all():
            if not isinstance(info, Gio.DesktopAppInfo):
                continue
            if not info.should_show() or info.get_nodisplay():
                continue

            app_id = info.get_id() or ''
            if not app_id or app_id in seen:
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

        self.entries = sorted(entries, key=lambda entry: entry.name.casefold())
        return self.entries

    def top(self, limit: int = 8) -> list[AppEntry]:
        return self.entries[:limit]

    def resolve(self, app_ids: list[str]) -> list[AppEntry]:
        by_id = {entry.app_id: entry for entry in self.entries}
        return [by_id[app_id] for app_id in app_ids if app_id in by_id]

    def search(
        self,
        query: str,
        sort_by_usage: bool = False,
        launch_counts: dict | None = None,
    ) -> list[AppEntry]:
        trimmed = query.strip().casefold()
        if not trimmed:
            entries = list(self.entries)
        else:
            prefix_matches = [e for e in self.entries if e.name.casefold().startswith(trimmed)]
            contains_matches = [e for e in self.entries if trimmed in e.search_text and e not in prefix_matches]
            entries = prefix_matches + contains_matches

        if sort_by_usage and launch_counts:
            entries.sort(key=lambda e: launch_counts.get(e.app_id, 0), reverse=True)

        return entries

    def launch(self, entry: AppEntry) -> tuple[bool, str | None]:
        try:
            entry.desktop_app.launch([], None)
        except GLib.Error as error:
            return False, error.message
        return True, None