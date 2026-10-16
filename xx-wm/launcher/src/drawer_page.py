from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app_index import AppEntry

_WEB_SEARCH_URL = 'https://duckduckgo.com/?q='


def _open_settings(win) -> None:
    """Show settings with the TOP hop when the window offers it.

    Bare test doubles predate _open_settings and only switch the stack
    page; real windows always carry the method, so behavior there is the
    hop that raises settings above a focused app.
    """
    opener = getattr(win, '_open_settings', None)
    if callable(opener):
        opener()
    else:
        win.stack.set_visible_child_name('settings')


class DrawerPage:
    """App drawer builder and list behavior for ShellWindow.

    Holds only a reference to the window; all state (list, search,
    sort mode, folder expansion) stays on the window so bare-instance
    headless tests keep working. Method names mirror the ShellWindow
    delegators in window.py.
    """

    def __init__(self, win) -> None:
        self._win = win

    def _build_apps_page(self):
        from gi.repository import Gtk

        win = self._win
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_vexpand(True)
        # One uniform surface sheet (theme CSS paints .drawer-sheet): the
        # list never floats as a grey panel on the root background.
        outer.add_css_class('drawer-sheet')
        outer.set_margin_bottom(24)
        outer.set_margin_start(24)
        outer.set_margin_end(0)

        win._sort_mode = 'az'

        # No 'All apps' header or count — the drawer is just the list, like
        # the PiercingXX Android launcher; the sort toggle rides alone, dim
        win._sort_btn = Gtk.Button(label='A-Z')
        win._sort_btn.add_css_class('flat')
        win._sort_btn.add_css_class('dim-label')
        win._sort_btn.set_halign(Gtk.Align.END)
        win._sort_btn.connect('clicked', win._toggle_sort_mode)

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_row.set_margin_end(24)
        header_row.append(Gtk.Box(hexpand=True))
        header_row.append(win._sort_btn)

        win.apps_search.set_margin_end(24)

        win.apps_scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        win.apps_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        win.apps_scroller.set_child(win.apps_list)

        # A-Z jump strip on the right edge: every letter a real ≥ 44 px
        # touch target in its own narrow scroller, so 27 letters never push
        # the search field off the phone screen.
        win._alpha_letter_rows = {}
        alpha_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        alpha_box.set_valign(Gtk.Align.CENTER)
        alpha_box.set_margin_end(4)

        for ch in '#ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            btn = Gtk.Button(label=ch)
            btn.add_css_class('flat')
            btn.add_css_class('alpha-jump')
            btn.connect('clicked', win._on_alpha_jump, ch)
            alpha_box.append(btn)

        win._alpha_scroller = Gtk.ScrolledWindow()
        win._alpha_scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Fills the list height and scrolls the 44 px targets inside it:
        # minimum height stays the search field's, never 27 tall buttons.
        win._alpha_scroller.set_vexpand(True)
        win._alpha_scroller.set_valign(Gtk.Align.FILL)
        win._alpha_scroller.set_child(alpha_box)

        list_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        list_row.append(win.apps_scroller)
        list_row.append(win._alpha_scroller)

        # Search lives at the bottom where the thumb is; keyboard only on tap.
        # The list row expands and shrinks; header and search keep their
        # minimum so search is never pushed off-screen.
        list_row.set_vexpand(True)
        outer.append(header_row)
        outer.append(list_row)
        outer.append(win.apps_search)

        # The drawer leaves the top ~15% of the screen free so it reads as a
        # sheet rather than a full-screen takeover (design.md "App drawer").
        # A fixed spacer widget would bake that gap into the page's minimum
        # height; this layout reserves it only from allocated space, so the
        # page minimum stays the content's and search always fits.
        class _SheetTopGapLayout(Gtk.LayoutManager):
            _GAP_FRACTION = 0.15

            def do_measure(self, widget, orientation, for_size):
                child = widget.get_first_child()
                if child is None:
                    return (0, 0, -1, -1)
                return child.measure(orientation, for_size)

            def do_allocate(self, widget, width, height, baseline):
                from gi.repository import Gsk, Graphene
                child = widget.get_first_child()
                if child is None:
                    return
                gap = int(height * self._GAP_FRACTION)
                point = Graphene.Point()
                point.x = 0.0
                point.y = float(gap)
                child.allocate(width, max(0, height - gap), baseline,
                               Gsk.Transform.new().translate(point))

        sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sheet.set_vexpand(True)
        sheet.set_layout_manager(_SheetTopGapLayout())
        sheet.append(outer)
        return sheet

    def _toggle_sort_mode(self, _btn) -> None:
        win = self._win
        win._sort_mode = 'install' if win._sort_mode == 'az' else 'az'
        win._sort_btn.set_label('Install date' if win._sort_mode == 'install' else 'A-Z')
        win._populate_apps(win.apps_search.get_text())

    def _home_slot_app_ids(self) -> set[str]:
        win = self._win
        ids: set[str] = set()
        for slot in win.config.home_slots:
            if slot.get('app_id'):
                ids.add(str(slot['app_id']))
            for member in slot.get('folder') or []:
                if member.get('app_id'):
                    ids.add(str(member['app_id']))
        return ids

    def _drawer_folder_slots(self) -> list[tuple[int, dict, list[tuple[int, dict]]]]:
        """Folder slots the drawer shows, in home-slot order (folders first).

        Members keep their original folder-list indices so the shared member
        menu edits the right entry; members whose app is not installed are
        skipped at render, and empty folders never appear.
        """
        win = self._win
        installed = {entry.app_id for entry in win.app_index.entries}
        out: list[tuple[int, dict, list[tuple[int, dict]]]] = []
        for idx, slot in enumerate(win.config.home_slots):
            if slot.get('type') != 'folder':
                continue
            members = [
                (m_idx, m) for m_idx, m in enumerate(slot.get('folder') or [])
                if m.get('app_id') and str(m['app_id']) in installed
            ]
            if members:
                out.append((idx, slot, members))
        return out

    @staticmethod
    def _install_time(entry: AppEntry) -> float:
        try:
            filename = entry.desktop_app.get_filename()
            return Path(filename).stat().st_mtime if filename else 0.0
        except OSError:
            return 0.0

    def _on_alpha_jump(self, _btn, letter: str) -> None:
        win = self._win
        idx = win._alpha_letter_rows.get(letter)
        if idx is None and letter == '#':
            idx = 0
        if idx is None:
            return
        row = win.apps_list.get_row_at_index(idx)
        if row is None:
            return
        alloc = row.get_allocation()
        adj = win.apps_scroller.get_vadjustment()
        adj.set_value(max(0.0, alloc.y))

    def _populate_apps(self, query: str = '') -> None:
        from gi.repository import GLib, Gtk

        win = self._win
        results = win.app_index.search(query)
        labels = win.config.app_labels
        trimmed = query.strip().casefold()

        if trimmed:
            # Search surfaces everything: hidden apps, home-slot apps, folder
            # members — plus matches against renamed labels.
            matched = {e.app_id for e in results}
            renamed_hits = [
                e for e in win.app_index.entries
                if e.app_id not in matched and trimmed in labels.get(e.app_id, '').casefold()
            ]
            results = results + renamed_hits
        else:
            # The browse list hides hidden apps and everything already on home
            browse_hidden = set(win.config.hidden_apps) | win._home_slot_app_ids()
            results = [e for e in results if e.app_id not in browse_hidden]
            if getattr(win, '_sort_mode', 'az') == 'install':
                results = sorted(results, key=win._install_time, reverse=True)
            # Pinned apps surface first, in their pinned order
            pinned_order = {app_id: i for i, app_id in enumerate(win.config.pinned)}
            pinned = sorted((e for e in results if e.app_id in pinned_order),
                            key=lambda e: pinned_order[e.app_id])
            results = pinned + [e for e in results if e.app_id not in pinned_order]

        win._last_search_results = results if trimmed else []

        # Home folders stay on home — the drawer only lists apps that are
        # not already a slot or folder member (they remain searchable)
        folder_slots = win._drawer_folder_slots()
        if (win._drawer_open_folder is not None
                and win._drawer_open_folder not in {i for i, _s, _m in folder_slots}):
            win._drawer_open_folder = None

        expanded_members = 0
        for i, _slot, members in folder_slots:
            if i == win._drawer_open_folder:
                expanded_members = len(members)
        row_offset = len(folder_slots) + expanded_members

        # Build letter→first-row-index map for A-Z jump strip
        win._alpha_letter_rows = {}
        for idx, entry in enumerate(results):
            display = labels.get(entry.app_id, entry.name)
            first = display[0].upper() if display else '#'
            letter = first if first.isalpha() else '#'
            if letter not in win._alpha_letter_rows:
                win._alpha_letter_rows[letter] = idx + row_offset

        win._replace_rows(win.apps_list, results, 'No apps matched this search.', win._make_app_row)

        # Folder drop-downs sit above the app rows (design.md "App drawer")
        win._drawer_folder_header = None
        insert_at = 0
        for idx, slot, members in folder_slots:
            header = win._make_drawer_folder_row(slot, idx)
            if idx == win._drawer_open_folder:
                win._drawer_folder_header = header
            win.apps_list.insert(header, insert_at)
            insert_at += 1
            if idx == win._drawer_open_folder:
                for m_idx, member in members:
                    win.apps_list.insert(
                        win._make_drawer_member_row(idx, m_idx, member), insert_at)
                    insert_at += 1

        # Synthetic "Launcher Settings" entry always sits at the very end
        if not trimmed or trimmed in 'launcher settings':
            win.apps_list.append(win._make_settings_row())

        # Search results anchor at the bottom, above the search field, within
        # thumb reach; a set that overflows the view still reads from the top.
        win.apps_list.set_valign(Gtk.Align.END if trimmed else Gtk.Align.FILL)
        if trimmed:
            GLib.idle_add(win._scroll_apps_to_top)

        if trimmed:
            win.app_count_label.set_text(f'{len(results)} matches')
        else:
            win.app_count_label.set_text(f'{len(results)} apps indexed')

    def _scroll_apps_to_top(self) -> bool:
        from gi.repository import GLib

        win = self._win
        win.apps_scroller.get_vadjustment().set_value(0.0)
        return GLib.SOURCE_REMOVE

    def _make_drawer_folder_row(self, slot: dict, slot_index: int):
        from gi.repository import Gtk, Pango

        win = self._win
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # Same centered alignment as app rows; long folder names ellipsize
        # instead of forcing the page wider than the phone screen.
        title = Gtk.Label(label=slot.get('label', ''), xalign=0.5)
        title.add_css_class('app-name')
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)
        indicator = Gtk.Label(label='⌄' if slot_index == win._drawer_open_folder else '›')
        indicator.add_css_class('dim-label')
        inner.append(title)
        inner.append(indicator)
        inner.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.set_child(inner)
        btn.connect('clicked', lambda _b, i=slot_index: win._toggle_drawer_folder(i))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _make_drawer_member_row(self, slot_index: int, member_index: int, member: dict):
        from gi.repository import Gtk, Pango

        win = self._win
        label = member.get('label', '')
        paused = win.focus_state.is_paused_app(str(member.get('app_id') or ''))
        from settings_screentime import is_limited_today
        limited = is_limited_today(win, str(member.get('app_id') or ''))
        if paused:
            suffix = ' · paused'
        elif limited:
            suffix = ' · time limit'
        else:
            suffix = ''
        # Same centered alignment as folder and app rows; the indented
        # button frame (folder-member margin) is what sets members apart.
        title = Gtk.Label(label=label + suffix, xalign=0.5)
        title.add_css_class('app-name')
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.add_css_class('folder-member')
        if paused or limited:
            btn.add_css_class('paused')
        btn.set_child(title)

        def _launch(_b) -> None:
            win._drawer_open_folder = None
            win._populate_apps(win.apps_search.get_text())
            win._launch_slot({'type': 'app', **member})

        btn.connect('clicked', _launch)
        win._add_long_press(
            btn, lambda w: win.item_actions.show_member_menu(w, slot_index, member_index))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _toggle_drawer_folder(self, slot_index: int) -> None:
        from gi.repository import GLib

        win = self._win
        opening = win._drawer_open_folder != slot_index
        win._drawer_open_folder = slot_index if opening else None
        win._populate_apps(win.apps_search.get_text())
        if opening:
            GLib.idle_add(win._center_drawer_folder)

    def _center_drawer_folder(self) -> bool:
        from gi.repository import GLib

        win = self._win
        # Scroll so the folder row plus its drop-down sit vertically centered
        header = win._drawer_folder_header
        if header is None or win._drawer_open_folder is None:
            return GLib.SOURCE_REMOVE
        row_h = header.get_height()
        if row_h <= 0:
            return GLib.SOURCE_CONTINUE
        # Size from rendered members only: uninstalled members are skipped
        # at render, so counting the raw list would overshoot the block.
        members = next((m for i, _s, m in win._drawer_folder_slots()
                        if i == win._drawer_open_folder), [])
        block_h = row_h * (1 + len(members))
        view_h = win.apps_scroller.get_height()
        alloc = header.get_allocation()
        block_h = min(block_h, view_h)
        target = alloc.y - max(0, (view_h - block_h) // 2)
        adj = win.apps_scroller.get_vadjustment()
        adj.set_value(max(0.0, float(target)))
        return GLib.SOURCE_REMOVE

    def _add_long_press(self, widget, callback) -> None:
        from gi.repository import Gtk

        def _on_long_press(gesture, _x: float, _y: float) -> None:
            # Claim the sequence so releasing doesn't also fire the row's click
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            callback(widget)

        long_press = Gtk.GestureLongPress.new()
        long_press.set_touch_only(False)
        long_press.connect('pressed', _on_long_press)
        widget.add_controller(long_press)
        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)
        right_click.connect('pressed', lambda _g, _n, _x, _y, w=widget: callback(w))
        widget.add_controller(right_click)

    def _make_settings_row(self):
        from gi.repository import Gtk, Pango

        win = self._win
        title = Gtk.Label(label='Launcher Settings', xalign=0.5)
        title.add_css_class('app-name')
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.set_child(title)
        btn.connect('clicked', lambda _b: _open_settings(win))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _replace_rows(
        self,
        list_box,
        entries: list[AppEntry],
        empty_message: str,
        row_maker: object = None,
    ) -> None:
        from gi.repository import Gtk

        win = self._win
        child = list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            list_box.remove(child)
            child = next_child

        if not entries:
            label = Gtk.Label(label=empty_message, xalign=0)
            label.add_css_class('dim-label')
            row = Gtk.ListBoxRow(selectable=False, activatable=False)
            row.set_child(label)
            list_box.append(row)
            return

        maker = row_maker if callable(row_maker) else win._make_app_row
        for entry in entries:
            list_box.append(maker(entry))

    def _make_app_row(self, entry: AppEntry):
        from gi.repository import Gtk, Pango

        win = self._win
        display_name = win.config.label_for(entry.app_id, entry.name)
        paused = win.focus_state.is_paused_app(entry.app_id)
        if paused:
            display_name += ' · paused'
        from settings_screentime import is_limited_today
        limited = is_limited_today(win, entry.app_id)
        if limited:
            display_name += ' · time limit'
        # Single-line centered rows, name only — matches the PiercingXX
        # Android launcher's drawer; descriptions were noise at phone size
        title = Gtk.Label(label=display_name, xalign=0.5)
        title.add_css_class('app-name')
        title.set_ellipsize(Pango.EllipsizeMode.END)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(title)
        content.set_hexpand(True)

        launch_btn = Gtk.Button()
        launch_btn.add_css_class('flat')
        launch_btn.add_css_class('app-entry')
        if paused or limited:
            launch_btn.add_css_class('paused')
        launch_btn.set_child(content)
        launch_btn.connect('clicked', lambda _b, e=entry: win._launch_entry(e))
        win._add_long_press(
            launch_btn, lambda w, e=entry: win.item_actions.show_app_menu(w, e))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(launch_btn)
        return row

    def _launch_entry(self, entry: AppEntry) -> None:
        from settings_page import _GESTURE_TITLES

        win = self._win
        if win._pick_slot_mode:
            win._pick_slot_mode = False
            win._add_app_slot(entry)
            return
        if win._pick_gesture_key:
            key = win._pick_gesture_key
            win._pick_gesture_key = None
            win.gesture_config.set(key, f'launch:{entry.app_id}')
            win._refresh_gesture_labels()
            _open_settings(win)
            name = win.config.label_for(entry.app_id, entry.name)
            win._show_status(f'{name} bound to {_GESTURE_TITLES[key]}.')
            return
        if getattr(win, '_pick_exempt_mode', False):
            win._pick_exempt_mode = False
            try:
                from app_standby import normalize_app_id
            except ImportError:
                normalize_app_id = None
            try:
                exempt = list(win.config.standby_exempt)
                wanted = (normalize_app_id(entry.app_id)
                          if normalize_app_id is not None
                          else entry.app_id.strip().casefold())
                if not any((normalize_app_id(e)
                            if normalize_app_id is not None
                            else str(e).strip().casefold()) == wanted
                           for e in exempt):
                    exempt.append(entry.app_id)
                    win.config.set_standby_exempt(exempt)
            except Exception:
                pass
            _open_settings(win)
            win._show_status(
                f'{win.config.label_for(entry.app_id, entry.name)} '
                'will never freeze.')
            return
        if win.focus_state.is_paused_app(entry.app_id):
            win._show_focus_notice(win.config.label_for(entry.app_id, entry.name))
            return
        ok, error = win.app_index.launch(
            entry, tracker=getattr(win, 'standby', None))
        if ok:
            win.config.record_launch(entry.app_id)
            # The app surfacing above the shell is the feedback; a toast
            # per launch was noise over every drawer tap.
            win.drop_to_background()
        else:
            win._show_status(f'Failed to launch {entry.name}: {error}')

    def _on_apps_search_changed(self, entry) -> None:
        win = self._win
        text = entry.get_text()
        win._populate_apps(text)
        # Auto-launch the single result while typing, when enabled
        query = text.strip()
        if (win.config.search_auto_launch and query and not query.startswith('!')
                and len(win._last_search_results) == 1):
            entry.set_text('')
            win._launch_entry(win._last_search_results[0])

    def _on_apps_search_activate(self, entry) -> None:
        win = self._win
        query = entry.get_text().strip()
        if not query:
            return
        # `!query` → web search; a query with zero app matches falls back too
        if query.startswith('!'):
            win._open_web_search(query[1:].strip())
            entry.set_text('')
            return
        results = win._last_search_results
        if results:
            entry.set_text('')
            win._launch_entry(results[0])
        else:
            win._open_web_search(query)
            entry.set_text('')

    def _open_web_search(self, query: str) -> None:
        from gi.repository import Gio, GLib
        from urllib.parse import quote_plus

        win = self._win
        if not query:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(_WEB_SEARCH_URL + quote_plus(query), None)
            win._show_status(f'Searching the web for {query}...')
        except GLib.Error as error:
            win._show_status(f'No browser available: {error.message}')
