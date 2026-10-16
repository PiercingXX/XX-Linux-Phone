"""Browse tab: search over six categories with honest "not tracked" rows.

Category data lives in core/browse; series math in core/detail. Metric lists
and the batch/sample rows render through Gtk.ListView. Charts are Cairo
sparklines on Gtk.DrawingArea (no new dependencies).
"""

from __future__ import annotations

from xx_vitals.core import browse as browse_model
from xx_vitals.core import detail as detail_model


def visible_rows(query: str) -> list:
    return browse_model.search_rows(query)


def detail_series(dailies, zoom_name: str, metric: str, zone=None):
    zoom = detail_model.Zoom(zoom_name)
    extractor, accumulates = _metric_extractor(metric, zone)
    return detail_model.aggregate(dailies, zoom, extractor, accumulates=accumulates)


def _metric_extractor(metric: str, zone=None):
    if metric == "steps":
        return lambda row: float(row.steps) if row.steps is not None else None, True
    if metric == "distance":
        return lambda row: row.distance_m, True
    if metric == "active_min":
        return lambda row: float(row.active_min) if row.active_min is not None else None, True
    if metric == "effort":
        return lambda row: float(row.effort_points) if row.effort_points is not None else None, True
    if metric == "weight":
        return lambda row: row.weight_avg_kg, False
    if metric == "bmi":
        return _bmi_extractor(), False
    return lambda row: None, True


def _bmi_extractor():
    def extract(row):
        height = getattr(row, "height_metres", None)
        weight = row.weight_avg_kg
        if height is None or not height or weight is None:
            return None
        return weight / (height * height)

    return extract


def weight_rows(samples, zone):
    return detail_model.daily_weight_projection(samples, zone)


def build_browse_page(db) -> object:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, Gtk

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_margin_start(12)
    box.set_margin_end(12)
    search = Gtk.SearchEntry(placeholder_text="Search metrics")
    search.set_hexpand(True)
    _defer_focus_until_tapped(search)
    box.append(search)
    store = Gio.ListStore.new(Gtk.Widget)
    factory = Gtk.SignalListItemFactory()

    def setup(_factory, item) -> None:
        label = Gtk.Label(label="")
        label.set_wrap(True)
        label.set_xalign(0.0)
        item.set_child(label)

    def bind(_factory, item) -> None:
        row = item.get_item()
        item.get_child().set_label(str(row))

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    list_view = Gtk.ListView.new(Gtk.NoSelection.new(store), factory)
    list_view.set_single_click_activate(True)
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_vexpand(True)
    scrolled.set_child(list_view)
    clamp = Adw.Clamp(maximum_size=480)
    clamp.set_child(scrolled)
    box.append(clamp)

    def refresh() -> None:
        store.remove_all()
        for category in browse_model.search_rows(search.get_text()):
            row = Gtk.Label(label=_category_line(category))
            row.set_wrap(True)
            row.set_xalign(0.0)
            store.append(row)

    search.connect("search-changed", lambda _entry: refresh())
    refresh()
    return box


def _defer_focus_until_tapped(entry) -> None:
    try:
        from gi.repository import Gtk
    except ImportError:
        return
    try:
        entry.set_can_focus(False)
        tap = Gtk.GestureClick.new()
        tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        tap.connect("pressed", lambda *_args: entry.set_can_focus(True))
        entry.add_controller(tap)
    except AttributeError:
        pass


def _category_line(category) -> str:
    if category.tracked:
        return f"{category.glyph} {category.label}"
    return f"{category.glyph} {category.label} — {browse_model.NOT_TRACKED_TAG}"
