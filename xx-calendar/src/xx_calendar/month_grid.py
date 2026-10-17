"""Cairo month grid: theme-token drawing that redraws on theme change."""
from __future__ import annotations

import datetime as _datetime

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

CELL_H = 48.0
DISC_R = 14.0
DOT_R = 2.2


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    text = (value or "#ffffff").strip().lstrip("#")
    if len(text) != 6:
        return (1.0, 1.0, 1.0)
    try:
        return (int(text[0:2], 16) / 255.0, int(text[2:4], 16) / 255.0,
                int(text[4:6], 16) / 255.0)
    except ValueError:
        return (1.0, 1.0, 1.0)


class MonthGrid(Gtk.DrawingArea):
    """7-column month grid drawn with cairo from theme tokens."""

    def __init__(self, on_select=None) -> None:
        super().__init__()
        self._weeks: list = []
        self._counts: dict = {}
        self._today: _datetime.date | None = None
        self._selected: _datetime.date | None = None
        self._on_select = on_select
        self._theme = None
        self.set_hexpand(True)
        self.set_vexpand(False)
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)
        try:
            import xx_ui

            self._theme = xx_ui.theme.load()
            self._unsub = xx_ui.on_change(self._on_theme)
        except Exception:
            self._theme = None
            self._unsub = None

    def _on_theme(self, theme) -> None:
        self._theme = theme
        self.queue_draw()

    def set_data(self, weeks: list, counts: dict,
                 today: _datetime.date | None,
                 selected: _datetime.date | None) -> None:
        self._weeks = list(weeks)
        self._counts = dict(counts)
        self._today = today
        self._selected = selected
        rows = max(1, len(self._weeks))
        self.set_content_height(int(rows * CELL_H))
        self.queue_draw()

    @property
    def weeks(self) -> list:
        return self._weeks

    def _colors(self) -> tuple:
        try:
            import xx_ui

            theme = self._theme or xx_ui.theme.load()
        except Exception:
            theme = None
        if theme is None:
            return ((1, 1, 1), (0.6, 0.6, 0.6), (0, 0, 0))
        return (_hex_to_rgb(theme.foreground), _hex_to_rgb(theme.muted),
                _hex_to_rgb(theme.background))

    def _on_pressed(self, _gesture, _n, x: float, y: float) -> None:
        if not self._weeks or self._on_select is None:
            return
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        rows = len(self._weeks)
        if width <= 0 or height <= 0 or rows <= 0:
            return
        col = min(6, max(0, int(x / (width / 7.0))))
        row = min(rows - 1, max(0, int(y / (height / rows))))
        try:
            day, _in = self._weeks[row][col]
        except IndexError:
            return
        self._on_select(day)

    def _draw(self, _area, ctx, width: int, height: int) -> None:
        if not self._weeks:
            return
        fg, muted, bg = self._colors()
        rows = len(self._weeks)
        cell_w = width / 7.0
        cell_h = height / rows if rows else CELL_H
        for row, week in enumerate(self._weeks):
            for col, (day, in_month) in enumerate(week):
                cx = col * cell_w + cell_w / 2.0
                cy = row * cell_h + cell_h / 2.0 - 4.0
                is_today = self._today is not None and day == self._today
                is_sel = self._selected is not None and day == self._selected
                weekend = day.weekday() >= 5
                dim = (not in_month) or weekend
                if is_today:
                    ctx.set_source_rgb(*fg)
                    ctx.arc(cx, cy, DISC_R, 0, 6.28318)
                    ctx.fill()
                    ctx.set_source_rgb(*bg)
                    self._draw_numeral(ctx, str(day.day), cx, cy)
                    ctx.set_source_rgb(*fg)
                else:
                    if is_sel:
                        ctx.set_source_rgb(*fg)
                        ctx.set_line_width(1.5)
                        ctx.arc(cx, cy, DISC_R, 0, 6.28318)
                        ctx.stroke()
                    ctx.set_source_rgb(*(muted if dim else fg))
                    self._draw_numeral(ctx, str(day.day), cx, cy)
                count = int(self._counts.get(day, 0))
                if count > 0:
                    dots = min(count, 3)
                    ctx.set_source_rgb(*(muted if (dim and not is_today) else fg))
                    total_w = (dots - 1) * 8.0
                    for i in range(dots):
                        dx = cx - total_w / 2.0 + i * 8.0
                        dy = cy + 16.0
                        ctx.arc(dx, dy, DOT_R, 0, 6.28318)
                        ctx.fill()

    def _draw_numeral(self, ctx, text: str, cx: float, cy: float) -> None:
        layout = self.create_pango_layout(text)
        try:
            from gi.repository import Pango

            desc = Pango.FontDescription.from_string("12")
            layout.set_font_description(desc)
        except Exception:
            pass
        try:
            _ink, logical = layout.get_pixel_extents()
            tw, th = logical.width, logical.height
        except Exception:
            tw, th = (12, 14)
        ctx.move_to(cx - tw / 2.0, cy - th / 2.0)
        try:
            from gi.repository import PangoCairo

            PangoCairo.show_layout(ctx, layout)
        except Exception:
            ctx.show_text(text)
