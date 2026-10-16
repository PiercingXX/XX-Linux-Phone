"""Journal tab: activities grouped into local days, newest first.

Grouping and totals live in core/journal; long day lists render through
Gtk.ListView. The collapsed gap band ("— no data —") covers the days between
the newest data and today; gap days never render as zero rings.
"""

from __future__ import annotations

from xx_vitals.core import journal as journal_model

NO_DATA_BAND = "— no data —"


def journal_rows(activities, zone, effort_target: int, dailies=()) -> list:
    return journal_model.group_by_day(activities, zone, effort_target, dailies)


def gap_band_text(groups, today) -> str | None:
    if not groups:
        return None
    newest = max(group.date for group in groups)
    gap = journal_model.no_data_gap(newest, today)
    if gap > 0:
        return f"{NO_DATA_BAND} ({gap}d)"
    return None


def build_journal_page(db, effort_target: int) -> object:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, Gtk

    store = Gio.ListStore.new(Gtk.Widget)
    factory = Gtk.SignalListItemFactory()

    def setup(_factory, item) -> None:
        label = Gtk.Label(label="")
        label.set_wrap(True)
        label.set_xalign(0.0)
        item.set_child(label)

    def bind(_factory, item) -> None:
        row = item.get_item()
        label = item.get_child()
        label.set_label(str(row))

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    selection = Gtk.NoSelection.new(store)
    list_view = Gtk.ListView.new(selection, factory)
    list_view.set_single_click_activate(True)
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_child(list_view)
    clamp = Adw.Clamp(maximum_size=480)
    clamp.set_child(scrolled)
    return clamp
