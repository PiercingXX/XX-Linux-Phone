"""
One shared drag-to-reorder sheet for home slots, pinned apps and folder
members — the shell's counterpart to the Android launcher's ReorderSheet.

The ReorderModel is pure list math (headless-tested); ReorderView builds
the Gtk.ListBox whose rows each carry a Gtk.DragSource + Gtk.DropTarget
pair, so pointer drags and phone long-press-then-drag both land on the
same move_row path that saves the whole order on every drop.
"""
from __future__ import annotations

from typing import Callable

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gtk, Pango

# Two-bar grip text. A Unicode pictograph would render inconsistently
# across phone fonts (XXWM_RULES), so this is U+2261 from the theme's
# monospace family — the same family every launcher label uses.
DRAG_HANDLE_GLYPH = '≡'

# Phone-narrow sheet cap: the shell runs at 320–360 px, so labels wrap
# like action-menu items and the list scrolls instead of growing.
_SHEET_TEXT_WIDTH = 24
_SHEET_WIDTH = 300
_SHEET_LIST_HEIGHT = 336


class ReorderModel:
    """Manual order over (id, label) items; every mutator keeps ids unique."""

    def __init__(self, items: list[tuple[str, str]]) -> None:
        self._items = list(items)

    def __len__(self) -> int:
        return len(self._items)

    def ids(self) -> list[str]:
        return [item_id for item_id, _label in self._items]

    def move(self, from_idx: int, to_idx: int) -> bool:
        """Drag reposition: lift one row out and drop it at to_idx."""
        if not (0 <= from_idx < len(self._items)):
            return False
        if not (0 <= to_idx < len(self._items)):
            return False
        if from_idx == to_idx:
            return True
        item = self._items.pop(from_idx)
        self._items.insert(to_idx, item)
        return True

    def move_id(self, dragged_id: str, target_id: str) -> bool:
        """Drop dragged_id onto target_id's row (takes its position)."""
        ids = self.ids()
        if dragged_id not in ids or target_id not in ids:
            return False
        return self.move(ids.index(dragged_id), ids.index(target_id))

    def swap(self, idx: int, delta: int) -> bool:
        """Neighbour swap for ↑/↓ parity with the menus."""
        return self.move(idx, idx + delta)

    def set_order(self, ids: list[str]) -> bool:
        """Apply a whole permutation; anything else leaves the order alone."""
        if sorted(ids) != sorted(self.ids()):
            return False
        by_id = {item[0]: item for item in self._items}
        self._items = [by_id[item_id] for item_id in ids]
        return True

    def replace(self, items: list[tuple[str, str]]) -> None:
        self._items = list(items)


class ReorderView:
    """ListBox UI over a ReorderModel; drops persist through on_order_changed."""

    def __init__(self, items: list[tuple[str, str]],
                 on_order_changed: Callable[[list[str]], None]) -> None:
        self.model = ReorderModel(items)
        self._on_order_changed = on_order_changed
        self._rows: dict[str, Gtk.ListBoxRow] = {}
        self._list_box = Gtk.ListBox()
        self._list_box.add_css_class('reorder-list')
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        for item_id, label in items:
            self._list_box.append(self._make_row(item_id, label))
        drop_end = Gtk.DropTarget.new(Gtk.ListBoxRow, Gdk.DragAction.MOVE)
        drop_end.connect('drop', self._on_drop_at_end)
        self._list_box.add_controller(drop_end)

    @property
    def list_box(self) -> Gtk.ListBox:
        return self._list_box

    def row_ids(self) -> list[str]:
        """Ids in current visual order (ListBox children, top to bottom)."""
        out = []
        child = self._list_box.get_first_child()
        while child is not None:
            for item_id, row in self._rows.items():
                if row is child:
                    out.append(item_id)
                    break
            child = child.get_next_sibling()
        return out

    def move_row(self, dragged_id: str, target_id: str) -> bool:
        """Shared drop path: DnD 'drop' handlers and tests both land here."""
        if not self.model.move_id(dragged_id, target_id):
            return False
        dragged = self._rows[dragged_id]
        target_index = self.model.ids().index(dragged_id)
        self._list_box.remove(dragged)
        self._list_box.insert(dragged, target_index)
        self._on_order_changed(self.model.ids())
        return True

    def replace(self, items: list[tuple[str, str]]) -> None:
        """Swap the whole list in place (Sort A–Z while open)."""
        self.model.replace(items)
        self._rows = {}
        child = self._list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._list_box.remove(child)
            child = next_child
        for item_id, label in items:
            self._list_box.append(self._make_row(item_id, label))
        self._on_order_changed(self.model.ids())

    def _make_row(self, item_id: str, label: str) -> Gtk.ListBoxRow:
        text = Gtk.Label(label=label, xalign=0)
        text.add_css_class('app-name')
        text.set_ellipsize(Pango.EllipsizeMode.END)
        text.set_hexpand(True)
        text.set_max_width_chars(_SHEET_TEXT_WIDTH)
        handle = Gtk.Label(label=DRAG_HANDLE_GLYPH)
        handle.add_css_class('dim-label')
        handle.add_css_class('reorder-handle')
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        inner.set_hexpand(True)
        inner.append(text)
        inner.append(handle)
        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(inner)
        self._rows[item_id] = row

        drag = Gtk.DragSource()
        drag.set_actions(Gdk.DragAction.MOVE)

        def _prepare(_src, _x: float, _y: float, dragged=row) -> Gdk.ContentProvider:
            return Gdk.ContentProvider.new_for_value(dragged)

        drag.connect('prepare', _prepare)
        row.add_controller(drag)

        drop = Gtk.DropTarget.new(Gtk.ListBoxRow, Gdk.DragAction.MOVE)
        drop.connect('drop', lambda _t, value, _x, _y, target=row:
                     self._on_drop(value, target))
        row.add_controller(drop)

        # Lift feedback only: the sequence is never claimed, so the
        # DragSource above still receives the hold-and-move that starts
        # a phone touch drag.
        lift = Gtk.GestureLongPress.new()
        lift.set_touch_only(False)
        lift.connect('pressed', lambda _g, _x, _y, r=row: r.add_css_class('lifted'))
        row.add_controller(lift)
        return row

    def _on_drop(self, value: object, target: Gtk.ListBoxRow) -> bool:
        if not isinstance(value, Gtk.ListBoxRow):
            return False
        dragged_id = next((k for k, r in self._rows.items() if r is value), None)
        target_id = next((k for k, r in self._rows.items() if r is target), None)
        if dragged_id is None or target_id is None:
            return False
        return self.move_row(dragged_id, target_id)

    def _on_drop_at_end(self, _target, value: object, _x: float, _y: float) -> bool:
        if not isinstance(value, Gtk.ListBoxRow):
            return False
        ids = self.model.ids()
        dragged_id = next((k for k, r in self._rows.items() if r is value), None)
        if dragged_id is None or not ids or dragged_id == ids[-1]:
            return False
        return self.move_row(dragged_id, ids[-1])


def build_reorder_box(title: str, items: list[tuple[str, str]],
                      on_order_changed: Callable[[list[str]], None],
                      on_sort: Callable[[], None] | None = None,
                      sort_label: str = 'Sort A–Z',
                      on_done: Callable[[], None] | None = None,
                      ) -> tuple[Gtk.Widget, ReorderView]:
    """Sheet body: title, optional sort row, scrolling drag list, Done."""
    from xx_ui import widgets

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class('xx-dialog')
    box.set_size_request(_SHEET_WIDTH, -1)
    heading = Gtk.Label(label=title, xalign=0)
    heading.add_css_class('xx-dialog-title')
    heading.set_ellipsize(Pango.EllipsizeMode.END)
    heading.set_max_width_chars(_SHEET_TEXT_WIDTH)
    box.append(heading)

    view = ReorderView(items, on_order_changed)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_min_content_height(min(
        _SHEET_LIST_HEIGHT, max(128, 56 * len(items))))
    scroller.set_propagate_natural_height(True)
    scroller.set_child(view.list_box)
    box.append(scroller)

    if on_sort is not None:
        sort_btn = widgets.menu_row(sort_label, on_sort)
        box.append(sort_btn)
    done_btn = widgets.menu_row('Done', on_done)
    box.append(done_btn)
    return box, view
