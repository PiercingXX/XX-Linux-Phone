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

from config import ShellConfig, ThemePreset

_EDGE_WIDTH = 28

_ARROW_HOLD_MS          = 280
_ARROW_FADE_STEPS       = 10
_ARROW_FADE_INTERVAL_MS = 20


def theme_css(preset: ThemePreset) -> str:
    return f"""
    .piercing-back-arrow {{
        background: alpha({preset.surface}, 0.82);
        border-radius: 40px;
        border: 1.5px solid alpha({preset.foreground}, 0.18);
        color: {preset.foreground};
        font-size: 30px;
        padding: 10px 18px;
    }}
"""


class _ArrowOverlay(Gtk.Window):
    """Brief ← flash that appears at the triggering edge after the gesture fires."""

    def __init__(self, left: bool, config: ShellConfig | None = None) -> None:
        super().__init__()
        self._anim_src: int | None = None
        self._fade_step = 0
        # The shell threads its LIVE config so theme == 'custom' renders the
        # derived palette; the fallback keeps direct construction working
        # (pre-existing snapshot behavior).
        self._config = config if config is not None else ShellConfig()

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

        self._theme_provider = Gtk.CssProvider()
        self._theme_provider.load_from_data(
            theme_css(self._display_preset()).encode('utf-8'))
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
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

    def _display_preset(self) -> ThemePreset:
        """Preset this overlay renders with: window.resolve_theme over the
        injected config, so theme == 'custom' derives its palette instead of
        falling back to the default preset (W1-B). Lazy import: window.py
        sits above this module in the shell stack, and a module-level import
        would drag its GTK requirements into headless contexts that import
        back_gesture for its pure theme_css sheet.
        """
        from window import resolve_theme
        return resolve_theme(self._config)

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        data = theme_css(preset if preset is not None else self._display_preset())
        self._theme_provider.load_from_data(data.encode('utf-8'))

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

    def __init__(self, config: ShellConfig | None = None) -> None:
        # Live ShellConfig when the shell threads it; a fresh snapshot keeps
        # direct no-arg construction working. Both arrows share it so their
        # sheets resolve through window.resolve_theme.
        self._config = config if config is not None else ShellConfig()
        self._left_arrow  = _ArrowOverlay(left=True, config=self._config)
        self._right_arrow = _ArrowOverlay(left=False, config=self._config)

    def set_application(self, app: Gtk.Application) -> None:
        self._left_arrow.set_application(app)
        self._right_arrow.set_application(app)

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        self._left_arrow.apply_theme(preset)
        self._right_arrow.apply_theme(preset)

    def flash_back(self, from_left: bool = True) -> None:
        arrow = self._left_arrow if from_left else self._right_arrow
        arrow.flash()
