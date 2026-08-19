"""
Long-press action menus shared by the app drawer and home folder members —
the shell's counterpart to the Android launcher's AppItemActions.

Menus are Gtk.Popovers anchored on the pressed row, themed via the
`.action-menu` CSS class (see window.py's theme provider). Text entries
commit on Enter, same as the confirm button (design.md "Dialogs & menus").
"""
from __future__ import annotations

import time
from typing import Callable

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

from app_index import AppEntry
from config import ShellConfig

_DISABLE_DURATIONS = [('1 hour', 1), ('2 hours', 2), ('4 hours', 4), ('8 hours', 8)]


class AppItemActions:
    """Builds and shows item action popovers; mutations go through config."""

    def __init__(self, config: ShellConfig,
                 on_changed: Callable[[], None],
                 on_status: Callable[[str], None],
                 focus_state: object | None = None) -> None:
        self._config = config
        self._on_changed = on_changed
        self._on_status = on_status
        self._focus = focus_state

    # -- popover plumbing --------------------------------------------------

    def _show_popover(self, anchor: Gtk.Widget, content: Gtk.Widget) -> Gtk.Popover:
        popover = Gtk.Popover()
        popover.add_css_class('action-menu')
        popover.set_child(content)
        popover.set_parent(anchor)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.connect('closed', lambda p: p.unparent())
        popover.popup()
        return popover

    def _action_list(self, actions: list[tuple[str, Callable[[], None]]],
                     title: str | None = None) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        if title:
            heading = Gtk.Label(label=title, xalign=0)
            heading.add_css_class('menu-title')
            box.append(heading)
        self._popover_ref: Gtk.Popover | None = None

        def _run(callback: Callable[[], None]) -> None:
            if self._popover_ref is not None:
                self._popover_ref.popdown()
            callback()

        for label, callback in actions:
            btn = Gtk.Button(label=label)
            btn.add_css_class('flat')
            btn.add_css_class('menu-action')
            btn.get_child().set_xalign(0)
            btn.connect('clicked', lambda _b, cb=callback: _run(cb))
            box.append(btn)
        return box

    def _show_action_menu(self, anchor: Gtk.Widget, title: str | None,
                          actions: list[tuple[str, Callable[[], None]]]) -> None:
        content = self._action_list(actions, title)
        self._popover_ref = self._show_popover(anchor, content)

    def _show_entry_dialog(self, anchor: Gtk.Widget, title: str, initial: str,
                           commit_label: str, on_commit: Callable[[str], None]) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class('menu-title')
        entry = Gtk.Entry()
        entry.set_text(initial)
        entry.select_region(0, -1)
        commit_btn = Gtk.Button(label=commit_label)
        commit_btn.add_css_class('flat')
        commit_btn.add_css_class('menu-action')
        box.append(heading)
        box.append(entry)
        box.append(commit_btn)

        popover = self._show_popover(anchor, box)

        def _commit(*_args: object) -> None:
            text = entry.get_text().strip()
            popover.popdown()
            if text:
                on_commit(text)

        entry.connect('activate', _commit)
        commit_btn.connect('clicked', _commit)
        entry.grab_focus()

    # -- drawer app rows ---------------------------------------------------

    def show_app_menu(self, anchor: Gtk.Widget, entry: AppEntry,
                      hidden_view: bool = False) -> None:
        config = self._config
        actions: list[tuple[str, Callable[[], None]]] = []
        actions.append(('App info', lambda: self._show_app_info(anchor, entry)))
        actions.append(('Rename', lambda: self._show_rename(anchor, entry)))
        actions.append(('Add to folder', lambda: self._show_add_to_folder(anchor, entry)))
        if entry.app_id in config.hidden_apps or hidden_view:
            actions.append(('Show', lambda: self._set_hidden(entry, False)))
        else:
            actions.append(('Hide', lambda: self._set_hidden(entry, True)))
        actions.append(('Disable for…', lambda: self._show_disable_for(anchor, entry.app_id, entry.name)))
        if self._focus is not None:
            if self._focus.is_focus_app(entry.app_id):
                actions.append(('Focus: unpause this app',
                                lambda: self._set_focus_app(entry, False)))
            else:
                actions.append(('Focus: pause this app',
                                lambda: self._set_focus_app(entry, True)))
        if entry.app_id in config.pinned:
            actions.append(('Unpin', lambda: self._set_pinned(entry, False)))
            actions.append(('Move up', lambda: self._move_pinned(entry, -1)))
            actions.append(('Move down', lambda: self._move_pinned(entry, +1)))
        else:
            actions.append(('Pin', lambda: self._set_pinned(entry, True)))
        display = config.label_for(entry.app_id, entry.name)
        self._show_action_menu(anchor, display, actions)

    def _show_app_info(self, anchor: Gtk.Widget, entry: AppEntry) -> None:
        lines = [entry.name]
        if entry.description:
            lines.append(entry.description)
        lines.append(entry.app_id)
        if entry.executable:
            lines.append(entry.executable)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for idx, line in enumerate(lines):
            lbl = Gtk.Label(label=line, xalign=0, wrap=True)
            lbl.add_css_class('menu-title' if idx == 0 else 'dim-label')
            box.append(lbl)
        self._show_popover(anchor, box)

    def _show_rename(self, anchor: Gtk.Widget, entry: AppEntry) -> None:
        current = self._config.label_for(entry.app_id, entry.name)

        def _commit(text: str) -> None:
            self._config.set_app_label(entry.app_id, None if text == entry.name else text)
            self._rename_in_slots(entry.app_id, text)
            self._on_changed()
            self._on_status(f'Renamed to {text}.')

        self._show_entry_dialog(anchor, 'Rename', current, 'Rename', _commit)

    def _rename_in_slots(self, app_id: str, label: str) -> None:
        slots = self._config.home_slots
        changed = False
        for slot in slots:
            if slot.get('type') == 'app' and slot.get('app_id') == app_id:
                slot['label'] = label
                changed = True
            for member in slot.get('folder') or []:
                if member.get('app_id') == app_id:
                    member['label'] = label
                    changed = True
        if changed:
            self._config.set_home_slots(slots)

    def _show_add_to_folder(self, anchor: Gtk.Widget, entry: AppEntry) -> None:
        slots = self._config.home_slots
        display = self._config.label_for(entry.app_id, entry.name)
        actions: list[tuple[str, Callable[[], None]]] = []
        for idx, slot in enumerate(slots):
            if slot.get('type') != 'folder':
                continue
            actions.append((slot.get('label', 'Folder'),
                            lambda i=idx: self._add_member(i, entry, display)))
        actions.append(('New folder…', lambda: self._show_new_folder(anchor, entry, display)))
        self._show_action_menu(anchor, 'Add to folder', actions)

    def _add_member(self, slot_index: int, entry: AppEntry, display: str) -> None:
        slots = self._config.home_slots
        if not (0 <= slot_index < len(slots)):
            return
        members = slots[slot_index].setdefault('folder', [])
        if any(m.get('app_id') == entry.app_id for m in members):
            self._on_status('Already in that folder.')
            return
        members.append({'label': display, 'app_id': entry.app_id})
        self._config.set_home_slots(slots)
        self._on_changed()
        self._on_status(f'Added to {slots[slot_index].get("label", "folder")}.')

    def _show_new_folder(self, anchor: Gtk.Widget, entry: AppEntry, display: str) -> None:
        def _commit(name: str) -> None:
            slots = self._config.home_slots
            if len(slots) >= 8:
                self._on_status('Home is full — remove a slot first.')
                return
            slots.append({'type': 'folder', 'label': name, 'app_id': None, 'cmd': None,
                          'folder': [{'label': display, 'app_id': entry.app_id}]})
            self._config.set_home_slots(slots)
            self._on_changed()
            self._on_status(f'Created folder {name}.')

        self._show_entry_dialog(anchor, 'New folder', '', 'Create', _commit)

    def _set_hidden(self, entry: AppEntry, hide: bool) -> None:
        hidden = [h for h in self._config.hidden_apps if h != entry.app_id]
        if hide:
            hidden.append(entry.app_id)
        self._config.set_hidden_apps(hidden)
        self._on_changed()
        self._on_status('App hidden — still reachable via search.' if hide else 'App shown in drawer.')

    def _set_pinned(self, entry: AppEntry, pin: bool) -> None:
        pinned = [p for p in self._config.pinned if p != entry.app_id]
        if pin:
            pinned.append(entry.app_id)
        self._config.set_pinned(pinned)
        self._on_changed()
        self._on_status('Pinned.' if pin else 'Unpinned.')

    def _move_pinned(self, entry: AppEntry, delta: int) -> None:
        pinned = self._config.pinned
        if entry.app_id not in pinned:
            return
        idx = pinned.index(entry.app_id)
        new_idx = idx + delta
        if not (0 <= new_idx < len(pinned)):
            self._on_status('Already at the top.' if delta < 0 else 'Already at the bottom.')
            return
        pinned[idx], pinned[new_idx] = pinned[new_idx], pinned[idx]
        self._config.set_pinned(pinned)
        self._on_changed()

    def _set_focus_app(self, entry: AppEntry, focused: bool) -> None:
        self._focus.set_app_focused(entry.app_id, focused)
        self._on_changed()
        self._on_status(
            f'{entry.name} pauses while Focus is on.' if focused
            else f'{entry.name} removed from Focus.')

    def _show_disable_for(self, anchor: Gtk.Widget, app_id: str, name: str) -> None:
        def _mute(hours: int, label: str) -> None:
            self._config.set_app_muted(app_id, time.time() + hours * 3600)
            self._on_status(f'{name} notifications off for {label}.')

        actions = [(label, lambda h=hours, lb=label: _mute(h, lb))
                   for label, hours in _DISABLE_DURATIONS]
        self._show_action_menu(anchor, 'Disable for…', actions)

    # -- folder members (home and drawer) ----------------------------------

    def show_member_menu(self, anchor: Gtk.Widget, slot_index: int, member_index: int) -> None:
        slots = self._config.home_slots
        if not (0 <= slot_index < len(slots)):
            return
        members = slots[slot_index].get('folder') or []
        if not (0 <= member_index < len(members)):
            return
        member = members[member_index]
        label = member.get('label', '')
        app_id = member.get('app_id')

        actions: list[tuple[str, Callable[[], None]]] = []
        actions.append(('App info', lambda: self._show_member_info(anchor, member)))
        actions.append(('Rename', lambda: self._show_member_rename(anchor, slot_index, member_index)))
        if app_id:
            actions.append(('Disable for…', lambda: self._show_disable_for(anchor, app_id, label)))
        actions.append(('Move up', lambda: self._move_member(slot_index, member_index, -1)))
        actions.append(('Move down', lambda: self._move_member(slot_index, member_index, +1)))
        actions.append(('Remove from folder', lambda: self._remove_member(slot_index, member_index)))
        self._show_action_menu(anchor, label, actions)

    def _show_member_info(self, anchor: Gtk.Widget, member: dict) -> None:
        lines = [member.get('label', '')]
        if member.get('app_id'):
            lines.append(str(member['app_id']))
        if member.get('cmd'):
            lines.append(' '.join(member['cmd']))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for idx, line in enumerate(lines):
            lbl = Gtk.Label(label=line, xalign=0, wrap=True)
            lbl.add_css_class('menu-title' if idx == 0 else 'dim-label')
            box.append(lbl)
        self._show_popover(anchor, box)

    def _show_member_rename(self, anchor: Gtk.Widget, slot_index: int, member_index: int) -> None:
        slots = self._config.home_slots
        member = (slots[slot_index].get('folder') or [])[member_index]

        def _commit(text: str) -> None:
            slots[slot_index]['folder'][member_index]['label'] = text
            if member.get('app_id'):
                self._config.set_app_label(str(member['app_id']), text)
            self._config.set_home_slots(slots)
            self._on_changed()
            self._on_status(f'Renamed to {text}.')

        self._show_entry_dialog(anchor, 'Rename', member.get('label', ''), 'Rename', _commit)

    def _remove_member(self, slot_index: int, member_index: int) -> None:
        slots = self._config.home_slots
        members = slots[slot_index].get('folder') or []
        if not (0 <= member_index < len(members)):
            return
        removed = members.pop(member_index)
        slots[slot_index]['folder'] = members
        self._config.set_home_slots(slots)
        self._on_changed()
        self._on_status(f'{removed.get("label", "Item")} removed from folder.')

    def _move_member(self, slot_index: int, member_index: int, delta: int) -> None:
        slots = self._config.home_slots
        members = slots[slot_index].get('folder') or []
        new_idx = member_index + delta
        if not (0 <= new_idx < len(members)):
            self._on_status('Already at the top.' if delta < 0 else 'Already at the bottom.')
            return
        members[member_index], members[new_idx] = members[new_idx], members[member_index]
        slots[slot_index]['folder'] = members
        self._config.set_home_slots(slots)
        self._on_changed()
