"""Home tab: dual ring, stat row, goal cards.

Pure math lives in core/home + core/metrics; this module only builds widgets.
Rings and dots are Cairo on Gtk.DrawingArea reading the live xx_ui theme and
redrawing on every theme switch, so paper (light) stays legible too.
"""

from __future__ import annotations

from datetime import date

from xx_vitals.core import home as home_model

RING_PX = 248
DOT_PX = 22


def day_summary(metric, units: str, steps_goal: int, effort_goal: int) -> dict:
    steps = metric.steps if metric is not None else None
    effort = metric.effort_points if metric is not None else None
    mask = metric.derived_mask if metric is not None else 0
    return {
        "has_data": home_model.has_data(metric) if metric is not None else False,
        "steps_fraction": _fraction(steps, steps_goal),
        "effort_fraction": _fraction(effort, effort_goal),
        "steps_text": _grouped(steps),
        "effort_text": _grouped(effort),
        "calories": home_model.format_calories(metric.calories_kcal if metric else None),
        "calories_prefix": home_model.derived_prefix(home_model.COLUMN_CALORIES, mask),
        "distance": home_model.format_distance(metric.distance_m if metric else None, units),
        "distance_label": home_model.distance_label(units),
        "distance_prefix": home_model.derived_prefix(home_model.COLUMN_DISTANCE, mask),
        "active_min": home_model.format_active_min(metric.active_min if metric else None),
    }


def _grouped(value) -> str:
    return f"{int(value):,}" if value is not None else "—"


def _fraction(actual, target: int) -> float:
    from xx_vitals.core import metrics

    if actual is None:
        return 0.0
    return metrics.ring_fraction(float(actual), float(target))


def collect_status(config, hardware_available: bool = False,
                   accelerometer_available: bool = False) -> dict:
    from xx_vitals.collect import status as collect_status

    described = collect_status.describe(
        bool(getattr(config, "collect_enabled", False)),
        hardware_available, accelerometer_available,
    )
    return {
        "title": described.title, "detail": described.detail,
        "enabled": described.enabled, "has_source": described.has_source,
    }


def _colors():
    import xx_ui

    active = xx_ui.theme.load()
    return active


def _rgba(hex_color: str):
    from gi.repository import Gdk

    rgba = Gdk.RGBA()
    rgba.parse(hex_color)
    return rgba


def _draw_rings(area, cairo, _width, _height, data) -> None:
    theme = _colors()
    cx = cy = RING_PX / 2
    track, steps_ink, effort_ink = (
        _rgba(theme.surface_alt), _rgba(theme.foreground), _rgba(theme.accent))
    cairo.set_line_width(14)
    cairo.set_line_cap(1)  # round caps
    for radius in (100.0, 78.0):
        cairo.set_source_rgba(*_channels(track))
        cairo.arc(cx, cy, radius, 0, 6.2832)
        cairo.stroke()
    for radius, fraction, ink in (
            (100.0, data["effort_fraction"], effort_ink),
            (78.0, data["steps_fraction"], steps_ink)):
        if fraction <= 0:
            continue
        cairo.set_source_rgba(*_channels(ink))
        cairo.arc(cx, cy, radius, -1.5708, -1.5708 + fraction * 6.2832)
        cairo.stroke()


def _channels(rgba) -> tuple:
    return (rgba.red, rgba.green, rgba.blue, rgba.alpha)


def _draw_dot(area, cairo, _width, _height, data) -> None:
    theme = _colors()
    mode, ink = data
    cx = cy = DOT_PX / 2
    if mode == "hit":
        paint = _rgba(theme.foreground)
        cairo.set_source_rgba(*_channels(paint))
        cairo.arc(cx, cy, 9.0, 0, 6.2832)
        cairo.fill()
    else:
        paint = _rgba(theme.muted if mode == "miss" else theme.border)
        cairo.set_source_rgba(*_channels(paint))
        cairo.set_line_width(2.0)
        cairo.arc(cx, cy, 8.0, 0, 6.2832)
        cairo.stroke()


def _draw_swatch(area, cairo, _width, _height, role: str) -> None:
    theme = _colors()
    paint = _rgba(theme.accent if role == "accent" else theme.foreground)
    cairo.set_source_rgba(*_channels(paint))
    cairo.arc(7.0, 7.0, 5.0, 0, 6.2832)
    cairo.fill()


def _drawing(px: int, draw, data=None):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    import xx_ui

    area = Gtk.DrawingArea()
    area.set_content_width(px)
    area.set_content_height(px)
    area.set_draw_func(draw, data)
    area.set_halign(Gtk.Align.CENTER)
    xx_ui.on_change(lambda _theme: area.queue_draw())
    return area


def build_home_page(db, config, nav, live: dict | None = None) -> object:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from xx_ui import widgets

    from xx_vitals.ui import shell as shell_page

    live = live if live is not None else {}
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
    state: dict = {"overflow": None}
    header_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)

    def menu() -> object:
        if state["overflow"] is not None:
            state["overflow"].popup()
            return state["overflow"]
        anchor = header_box.get_first_child()
        popover = shell_page.open_overflow(anchor, [
            ("Log weight", nav.add_weight),
            ("Import Takeout", nav.import_takeout),
            ("Settings", lambda: nav.show("profile")),
        ])
        state["overflow"] = popover
        popover.connect("closed", lambda _p: state.update(overflow=None))
        return popover

    def refresh() -> None:
        child = body.get_first_child()
        while child is not None:
            after = child.get_next_sibling()
            body.remove(child)
            child = after
        _fill_body(body, db, config, nav)
        while True:
            first = header_box.get_first_child()
            if first is None:
                break
            header_box.remove(first)
        header_box.append(widgets.page_header(
            "Vitals", _today_caption(), overflow_cb=menu))

    live["refresh"] = refresh
    live["menu"] = menu
    refresh()
    body.add_css_class("xx-page-body")
    return widgets.page(header_box, body, None, widgets.fab(nav.add_activity))


def _today_caption() -> str:
    today = date.today()
    return f"{today.strftime('%A, %B')} {today.day}"


def _fill_body(body, db, config, nav) -> None:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from xx_ui import widgets

    try:
        dailies = db.list_dailies(limit=30)
    except (OSError, AttributeError):
        dailies = []
    today = date.today()
    latest = None
    for row in dailies:
        try:
            if date.fromisoformat(row.date[:10]) <= today and (
                    latest is None or row.date > latest.date):
                latest = row
        except (ValueError, AttributeError):
            continue
    summary = day_summary(latest, config.units, config.steps_goal,
                          config.effort_goal)

    ring_wrap = Gtk.Overlay()
    ring_wrap.set_halign(Gtk.Align.CENTER)
    ring_wrap.set_child(_drawing(RING_PX, _draw_rings, summary))
    center = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    center.set_halign(Gtk.Align.CENTER)
    center.set_valign(Gtk.Align.CENTER)
    effort_label = widgets.label(summary["effort_text"], "xx-display-large")
    effort_label.add_css_class("xx-accent-text")
    effort_label.set_xalign(0.5)
    steps_label = widgets.label(summary["steps_text"], "xx-title")
    steps_label.set_xalign(0.5)
    center.append(effort_label)
    center.append(steps_label)
    ring_wrap.add_overlay(center)
    body.append(ring_wrap)
    body.append(_legend_row())
    body.append(_stat_row(summary))

    if not getattr(config, "collect_enabled", False):
        off = widgets.card()
        off.append(widgets.caption("Step counting is off"))
        off.append(widgets.wrap_label(collect_status(config)["detail"]))
        off.append(widgets.text_button(
            "Open settings", lambda: nav.show("profile")))
        body.append(off)

    body.append(_goals_card(dailies, today, config, nav))
    body.append(_weekly_card(dailies, today, config, nav))
    weight_card = _weight_card(db, config, nav)
    if weight_card is not None:
        body.append(weight_card)


def _legend_row():
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Pango

    from xx_ui import widgets

    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    row.set_homogeneous(True)
    for text, role in (("Effort", "accent"), ("Steps", "foreground")):
        cell = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
        cell.set_halign(Gtk.Align.CENTER)
        cell.set_valign(Gtk.Align.CENTER)
        swatch = _drawing(14, _draw_swatch, role)
        cell.append(swatch)
        tag = widgets.caption(text)
        # Allocated exactly its natural width: an ellipsis would misfire
        # on letter-spacing rounding, a sub-pixel clip stays invisible.
        tag.set_ellipsize(Pango.EllipsizeMode.NONE)
        cell.append(tag)
        row.append(cell)
    return row


def _stat_row(summary: dict):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from xx_ui import widgets

    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    row.set_homogeneous(True)
    cells = [
        (f"{summary['calories_prefix']}{summary['calories']}", "Cal"),
        (f"{summary['distance_prefix']}{summary['distance']}",
         summary["distance_label"]),
        (summary["active_min"], "Active Min"),
    ]
    for value, unit in cells:
        cell = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        value_label = widgets.label(value, "xx-title")
        value_label.set_xalign(0.5)
        unit_label = widgets.caption(unit)
        unit_label.set_xalign(0.5)
        cell.append(value_label)
        cell.append(unit_label)
        row.append(cell)
    return row


def _goals_card(dailies, today: date, config, nav):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Pango

    from xx_ui import widgets

    card = widgets.card()
    title_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    title_label = widgets.label("Your daily goals", "xx-title")
    title_label.set_hexpand(True)
    title_row.append(title_label)
    title_row.append(widgets.icon_image("go-next-symbolic"))
    title_button = Gtk.Button()
    title_button.add_css_class("xx-btn")
    title_button.set_child(title_row)
    title_button.connect("clicked", lambda _b: nav.show("profile"))
    card.append(title_button)
    card.append(widgets.caption("Last 7 days"))
    slots = home_model.last_seven_days(dailies, today)
    hits = sum(
        1 for slot in slots
        if slot.metric is not None
        and (slot.metric.effort_points or 0) >= config.effort_goal)
    strip = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    count_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    count_box.set_valign(Gtk.Align.CENTER)
    count_box.append(widgets.label(f"{hits}/7", "xx-display"))
    achieved = widgets.caption("Achieved")
    achieved.set_ellipsize(Pango.EllipsizeMode.NONE)
    count_box.append(achieved)
    strip.append(count_box)
    dots = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
    dots.set_hexpand(True)
    dots.set_halign(Gtk.Align.END)
    dots.set_valign(Gtk.Align.CENTER)
    for slot in slots:
        cell = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        if slot.metric is None:
            mode = "gap"
        elif (slot.metric.effort_points or 0) >= config.effort_goal:
            mode = "hit"
        else:
            mode = "miss"
        cell.append(_drawing(DOT_PX, _draw_dot, (mode, None)))
        letter = widgets.caption(home_model.weekday_letter(slot.date))
        letter.set_xalign(0.5)
        cell.append(letter)
        dots.append(cell)
    strip.append(dots)
    card.append(strip)
    return card


def _weekly_card(dailies, today: date, config, nav):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from xx_ui import widgets

    card = widgets.card()
    title_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    title_label = widgets.label("Your weekly target", "xx-title")
    title_label.set_hexpand(True)
    title_row.append(title_label)
    title_row.append(widgets.icon_image("go-next-symbolic"))
    title_button = Gtk.Button()
    title_button.add_css_class("xx-btn")
    title_button.set_child(title_row)
    title_button.connect("clicked", lambda _b: nav.show("profile"))
    card.append(title_button)
    card.append(widgets.caption(home_model.iso_week_range_label(today)))
    current = home_model.current_iso_week_effort(dailies, today)
    card.append(widgets.label(
        home_model.weekly_progress_line(current, config.weekly_effort_goal),
        "xx-display"))
    bar = Gtk.ProgressBar()
    goal = max(1, config.weekly_effort_goal)
    bar.set_fraction(min(1.0, current / goal))
    bar.set_show_text(False)
    card.append(bar)
    card.append(widgets.wrap_label(
        "Scoring 150 Heart Points a week can help you live longer."))
    return card


def _weight_card(db, config, nav):
    from xx_ui import widgets

    try:
        samples = db.list_weights(limit=1)
    except (OSError, AttributeError):
        return None
    if not samples:
        return None
    from xx_vitals.core import format as format_model

    card = widgets.card()
    card.append(widgets.caption("Latest weigh-in"))
    card.append(widgets.label(
        format_model.format_weight(samples[0].weight_kg, config.units),
        "xx-display"))
    card.append(widgets.outline_button("Log weight", nav.add_weight))
    return card
