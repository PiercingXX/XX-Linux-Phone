"""Blocking page: blocked numbers, content-filter terms, dialer import.

Ports ``ui/BlockingActivity`` (edits the four persisted rule sets and the
unknown-sender hold, applying immediately). Every mutation writes the
SQLite rule tables and rebuilds the process-wide live filter, so the
next inbound message is filtered by the new rules.

Contact blocks additionally tag the xx-contacts entry ``"blocked"``
through the atomic ``UpdateTags`` call (never read-modify-write);
numbers with no contact entry stay local-only. The dialer import pulls
the dialer's local block table when it is reachable and stays silent
otherwise.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from txxt import blocking as blocking_mod  # noqa: E402
from txxt.conversations import tap_to_focus  # noqa: E402

DIALER_BLOCK_ENV = 'TXXT_DIALER_BLOCKS'
DIALER_BLOCK_NAMES = ('blocked.json', 'blocklist.json')


def dialer_search_roots() -> list[Path]:
    """Where the dialer's local block table may live, in priority order."""
    roots: list[Path] = []
    override = os.environ.get(DIALER_BLOCK_ENV)
    if override:
        roots.append(Path(override).parent)
    try:
        lane_root = Path(__file__).resolve().parents[2]
    except Exception:
        lane_root = None
    if lane_root is not None:
        roots.append(lane_root / 'ref' / 'xx-dialer')
        roots.append(lane_root.parent / 'xx-dialer')
    data_home = os.environ.get('XDG_DATA_HOME') or str(Path.home() / '.local' / 'share')
    roots.append(Path(data_home) / 'xx-dialer')
    return roots


def find_dialer_block_file() -> Path | None:
    """First reachable dialer block-table file, or None when absent."""
    for root in dialer_search_roots():
        for name in DIALER_BLOCK_NAMES:
            try:
                candidate = root / name
            except Exception:
                continue
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    override = os.environ.get(DIALER_BLOCK_ENV)
    if override:
        try:
            direct = Path(override)
            if direct.is_file():
                return direct
        except OSError:
            pass
    return None


def parse_dialer_blocks(raw: str) -> list[str]:
    """Numbers from a dialer block-table payload (list or mapped list)."""
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if isinstance(data, dict):
        for key in ('blocked', 'blocklist', 'numbers'):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            return []
    if not isinstance(data, list):
        return []
    numbers = []
    for entry in data:
        text = entry if isinstance(entry, str) else str(entry.get('number', ''))
        if text.strip() and text.strip() not in numbers:
            numbers.append(text.strip())
    return numbers


def import_dialer_blocks(store) -> tuple[int, int]:
    """Merge the reachable dialer block table into ``sender`` rules.

    Returns ``(imported, total)``; ``(0, 0)`` when no table is
    reachable. Idempotent: ``blocklist_add`` replaces per key.
    """
    found = find_dialer_block_file()
    if found is None:
        return 0, 0
    try:
        numbers = parse_dialer_blocks(found.read_text(encoding='utf-8'))
    except OSError:
        return 0, 0
    for number in numbers:
        try:
            store.blocklist_add('sender', number)
        except Exception:
            pass
    try:
        blocking_mod.live_apply_from_store(store)
    except Exception:
        pass
    return len(numbers), len(numbers)


class BlockingPage(Adw.NavigationPage):
    """Rule editor over the store's ``blocklist``/``starred`` tables."""

    def __init__(
        self, navigation: Adw.NavigationView, parent_window: Gtk.Window,
        store, resolver=None, on_changed=None,
    ) -> None:
        super().__init__()
        self._navigation = navigation
        self._window = parent_window
        self._store = store
        self._resolver = resolver
        self._on_changed = on_changed
        self.set_title('Blocking')

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label='Blocking'))
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        toolbar.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_hexpand(True)
        body.set_margin_start(12)
        body.set_margin_end(12)
        body.set_margin_top(6)
        body.set_margin_bottom(6)

        self._quarantine_row = Adw.SwitchRow()
        self._quarantine_row.set_title('Hold unknown senders for review')
        self._quarantine_row.set_subtitle('Off delivers everything not blocked')
        hold_group = Adw.PreferencesGroup()
        hold_group.set_title('Unknown senders')
        hold_group.add(self._quarantine_row)
        body.append(hold_group)
        self._quarantine_row.connect('notify::active', self._on_quarantine_changed)

        self._lists: dict[str, Gtk.ListBox] = {}
        self._inputs: dict[str, Adw.EntryRow] = {}
        for kind, title, hint in (
            ('sender', 'Blocked numbers', 'Number to block'),
            ('keyword', 'Blocked keywords', 'Whole-word term'),
            ('phrase', 'Blocked phrases', 'Substring phrase'),
        ):
            group = Adw.PreferencesGroup()
            group.set_title(title)
            entry = Adw.EntryRow()
            entry.set_title(hint)
            tap_to_focus(entry)
            self._inputs[kind] = entry
            group.add(entry)
            add_button = Gtk.Button(label='Add')
            add_button.connect('clicked', self._on_add_clicked, kind)
            group.add(add_button)
            rows = Gtk.ListBox()
            rows.set_selection_mode(Gtk.SelectionMode.NONE)
            self._lists[kind] = rows
            group.add(rows)
            body.append(group)

        dialer_group = Adw.PreferencesGroup()
        dialer_group.set_title('Dialer')
        self._import_button = Gtk.Button(label='Import dialer block list')
        self._import_button.connect('clicked', self._on_import_clicked)
        dialer_group.add(self._import_button)
        self._import_label = Gtk.Label(label='')
        self._import_label.set_xalign(0.0)
        self._import_label.set_ellipsize(Pango.EllipsizeMode.END)
        dialer_group.add(self._import_label)
        body.append(dialer_group)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(body)
        toolbar.set_content(scrolled)
        self.set_child(toolbar)
        self._ready = False
        self.refresh()

    # ------------------------------------------------------------ loading

    def set_resolver(self, resolver) -> None:
        self._resolver = resolver

    def refresh(self) -> None:
        worker = threading.Thread(target=self._load_in_worker, daemon=True)
        worker.start()

    def _load_in_worker(self) -> None:
        try:
            senders = self._store.blocklist_list('sender')
            keywords = self._store.blocklist_list('keyword')
            phrases = self._store.blocklist_list('phrase')
            quarantine = bool(self._store.get_setting(
                blocking_mod.QUARANTINE_SETTING_KEY, False,
            ))
        except Exception:
            return
        GLib.idle_add(
            self._apply_loaded,
            {'sender': senders, 'keyword': keywords, 'phrase': phrases},
            quarantine,
        )

    def _apply_loaded(self, entries: dict, quarantine: bool) -> bool:
        self._ready = False
        try:
            self._quarantine_row.set_active(quarantine)
        except Exception:
            pass
        for kind, pairs in entries.items():
            rows = self._lists[kind]
            while True:
                row = rows.get_row_at_index(0)
                if row is None:
                    break
                rows.remove(row)
            for _kind, value in sorted(pairs, key=lambda pair: pair[1].lower()):
                label = Gtk.Label(label=value)
                label.set_xalign(0.0)
                label.set_hexpand(True)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_max_width_chars(24)
                remove = Gtk.Button(label='Remove')
                remove.connect('clicked', self._on_remove_clicked, kind, value)
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                box.set_hexpand(True)
                box.append(label)
                box.append(remove)
                rows.append(box)
        self._ready = True
        return False

    # ------------------------------------------------------------ mutations

    def _run_store(self, fn) -> None:
        worker = threading.Thread(target=self._store_in_worker, args=(fn,), daemon=True)
        worker.start()

    def _store_in_worker(self, fn) -> None:
        try:
            fn()
        except Exception:
            pass
        try:
            blocking_mod.live_apply_from_store(self._store)
        except Exception:
            pass
        GLib.idle_add(self._refresh_from_idle)

    def _fire_changed(self) -> bool:
        if self._on_changed is not None:
            try:
                self._on_changed()
            except Exception:
                pass
        return False

    def _refresh_from_idle(self) -> bool:
        self.refresh()
        self._fire_changed()
        return False

    def _on_add_clicked(self, _button, kind: str) -> None:
        value = self._inputs[kind].get_text().strip()
        if not value:
            return
        self._inputs[kind].set_text('')
        self._run_store(lambda: self._add_rule(kind, value))

    def _add_rule(self, kind: str, value: str) -> None:
        self._store.blocklist_add(kind, value)
        if kind == 'sender' and self._resolver is not None:
            try:
                self._resolver.set_blocked(value, True)
            except Exception:
                pass

    def _on_remove_clicked(self, _button, kind: str, value: str) -> None:
        self._run_store(lambda: self._remove_rule(kind, value))

    def _remove_rule(self, kind: str, value: str) -> None:
        try:
            self._store.blocklist_remove(kind, value)
        except Exception:
            pass
        if kind == 'sender' and self._resolver is not None:
            try:
                self._resolver.set_blocked(value, False)
            except Exception:
                pass

    def _on_quarantine_changed(self, row, _spec) -> None:
        if not getattr(self, '_ready', False):
            return
        active = bool(row.get_active())
        self._run_store(
            lambda: self._store.set_setting(
                blocking_mod.QUARANTINE_SETTING_KEY, active,
            ),
        )

    def _on_import_clicked(self, _button) -> None:
        self._run_store(self._import_in_worker)

    def _import_in_worker(self) -> None:
        imported, _total = import_dialer_blocks(self._store)
        GLib.idle_add(self._announce_import, imported)

    def _announce_import(self, imported: int) -> bool:
        self._import_label.set_text(
            f'Imported {imported} numbers.' if imported
            else 'No dialer block list found.',
        )
        return False
