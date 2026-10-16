"""List visibility and row actions. Port of ContactVisibility.kt + RowActions.kt."""

from __future__ import annotations


def show_on_list(blocked: bool, search_query: str | None) -> bool:
    if not blocked:
        return True
    return bool(search_query and search_query.strip())


def row_actions(has_phone: bool) -> list[str]:
    actions = ["open"]
    if has_phone:
        actions += ["call", "message"]
    return actions + ["share", "group", "delete"]
