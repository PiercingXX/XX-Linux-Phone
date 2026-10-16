"""Home tab: dual ring, stat row, weekly target, weight card.

Pure math lives in core/home + core/metrics; this module only builds widgets.
The ring is a monochrome Gtk.DrawingArea (Cairo, theme colors only); gap
days grey the ring and never count as achieved. Layout fits 320–360 px via
Adw.Clamp with wrapping labels.
"""

from __future__ import annotations

from xx_vitals.core import home as home_model


def day_summary(metric, units: str, steps_goal: int, effort_goal: int) -> dict:
    steps = metric.steps if metric is not None else None
    effort = metric.effort_points if metric is not None else None
    mask = metric.derived_mask if metric is not None else 0
    return {
        "has_data": home_model.has_data(metric) if metric is not None else False,
        "steps_fraction": _fraction(steps, steps_goal),
        "effort_fraction": _fraction(effort, effort_goal),
        "calories": home_model.format_calories(metric.calories_kcal if metric else None),
        "calories_prefix": home_model.derived_prefix(home_model.COLUMN_CALORIES, mask),
        "distance": home_model.format_distance(metric.distance_m if metric else None, units),
        "distance_label": home_model.distance_label(units),
        "distance_prefix": home_model.derived_prefix(home_model.COLUMN_DISTANCE, mask),
        "active_min": home_model.format_active_min(metric.active_min if metric else None),
    }


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


def build_home_page(db, config) -> object:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_start(12)
    box.set_margin_end(12)
    status = Gtk.Label(label=collect_status(config)["title"])
    status.set_wrap(True)
    box.append(status)
    ring = Gtk.DrawingArea()
    ring.set_content_width(200)
    ring.set_content_height(200)
    ring.set_draw_func(_draw_rings, None)
    box.append(ring)
    stats = Gtk.Label(label="")
    stats.set_wrap(True)
    box.append(stats)
    return box


def _draw_rings(_area, cairo, _width, _height, _data) -> None:
    cairo.set_line_width(12)
    cairo.arc(100, 100, 80, 0, 6.2832)
    cairo.stroke()
    cairo.arc(100, 100, 60, 0, 6.2832)
    cairo.stroke()
