"""
Back gesture visual feedback: brief arrow overlay that flashes at the triggering edge.

Gesture detection is handled externally by lisgd, which fires IPC commands.
This module only owns the arrow overlay shown after the gesture fires.
"""
from __future__ import annotations

import logging

_log = logging.getLogger('back_gesture')

import gi

try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _HAS_LAYER = True
except ValueError:
    _HAS_LAYER = False

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk

if _HAS_LAYER:
    from gi.repository import Gtk4LayerShell as LayerShell

_EDGE_WIDTH = 28

_ARROW_HOLD_MS          = 280
_ARROW_FADE_STEPS       = 10
_ARROW_FADE_INTERVAL_MS = 20

_ARROW_CSS = b"""
    .piercing-back-arrow {
        background: rgba(20, 20, 20, 0.82);
        border-radius: 40px;
        border: 1.5px solid rgba(255, 255, 255, 0.18);
        color: #ffffff;
        font-family: "Space Mono", monospace;
        font-size: 30px;
        padding: 10px 18px;
    }
"""


class _ArrowOverlay(Gtk.Window):
    """Brief ← flash that appears at the triggering edge after the gesture fires."""

    def __init__(self, left: bool) -> None:
        super().__init__()
        self._anim_src: int | None = None
        self._fade_step = 0

        self.set_decorated(False)
        self.set_resizable(False)

        if _HAS_LAYER and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            edge = LayerShell.Edge.LEFT if left else LayerShell.Edge.RIGHT
            LayerShell.set_anchor(self, edge, True)
            LayerShell.set_exclusive_zone(self, 0)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
            LayerShell.set_margin(self, edge, _EDGE_WIDTH + 12)

        self.set_default_size(76, 64)

        css = Gtk.CssProvider()
        css.load_from_data(_ARROW_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        win_css = Gtk.CssProvider()
        win_css.load_from_data(b'window { background: transparent; }')
        self.get_style_context().add_provider(
            win_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 10,
        )

        label = Gtk.Label(label='←')
        label.add_css_class('piercing-back-arrow')
        box = Gtk.Box()
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.append(label)
        self.set_child(box)

        self.set_opacity(0.0)

    def flash(self) -> None:
        if self._anim_src is not None:
            GLib.source_remove(self._anim_src)
            self._anim_src = None
        self.set_opacity(1.0)
        self.set_visible(True)
        self.present()
        self._anim_src = GLib.timeout_add(_ARROW_HOLD_MS, self._begin_fade)

    def _begin_fade(self) -> bool:
        self._anim_src = None
        self._fade_step = 0
        self._anim_src = GLib.timeout_add(_ARROW_FADE_INTERVAL_MS, self._tick_fade)
        return GLib.SOURCE_REMOVE

    def _tick_fade(self) -> bool:
        self._fade_step += 1
        alpha = 1.0 - self._fade_step / _ARROW_FADE_STEPS
        if alpha <= 0.0:
            self.set_opacity(0.0)
            self.set_visible(False)
            self._anim_src = None
            return GLib.SOURCE_REMOVE
        self.set_opacity(alpha)
        return GLib.SOURCE_CONTINUE


class BackGestureLayer:
    """Arrow overlays for back gesture feedback. Triggered via IPC (lisgd → gesture.back)."""

    def __init__(self) -> None:
        self._left_arrow  = _ArrowOverlay(left=True)
        self._right_arrow = _ArrowOverlay(left=False)

    def set_application(self, app: Gtk.Application) -> None:
        self._left_arrow.set_application(app)
        self._right_arrow.set_application(app)

    def flash_back(self, from_left: bool = True) -> None:
        arrow = self._left_arrow if from_left else self._right_arrow
        arrow.flash()
