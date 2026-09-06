"""
In-shell volume/brightness HUD (plan T2).

A brief GTK4 layer-shell OVERLAY window that flashes the current volume or
brightness level after a hardware key press. The shell owns every surface it
draws (see design.md "System surfaces"), so this replaces the external `wob`
daemon: no separate process, no IPC socket — just a short-lived overlay that
auto-hides after ~1s.

Headless-safe: on a host without PyGObject (or without the gtk4-layer-shell
compositor seam), the overlay is never created and every public method
silently no-ops, so wiring volume/brightness through this module can never
break on a device where the HUD cannot init.
"""
from __future__ import annotations

import logging

_log = logging.getLogger('hud')

from config import ShellConfig, ThemePreset

_HAS_GTK = True
try:
    import gi
except ImportError:
    _HAS_GTK = False

if _HAS_GTK:
    try:
        gi.require_version('Gtk4LayerShell', '1.0')
        _HAS_LAYER = True
    except (ValueError, ImportError):
        _HAS_LAYER = False

    gi.require_version('Gtk', '4.0')
    from gi.repository import GLib, Gtk

    if _HAS_LAYER:
        from gi.repository import Gtk4LayerShell as LayerShell

_HUD_HOLD_MS          = 1000
_HUD_FADE_STEPS       = 10
_HUD_FADE_INTERVAL_MS = 20

def theme_css(preset: ThemePreset) -> str:
    return f"""
    .piercing-hud-box {{
        background: alpha({preset.surface}, 0.82);
        border-radius: 16px;
        border: 1.5px solid alpha({preset.foreground}, 0.18);
        padding: 14px 22px;
    }}
    .piercing-hud-label {{
        color: {preset.foreground};
        font-size: 40px;
        font-weight: 700;
    }}
    .piercing-hud-level {{
        min-width: 180px;
        min-height: 8px;
        border-radius: 4px;
        background: alpha({preset.foreground}, 0.18);
    }}
    .piercing-hud-level > trough > progress {{
        background: {preset.foreground};
        border-radius: 4px;
    }}
"""


if _HAS_GTK:

    class _HudWindow(Gtk.Window):
        """Centered overlay that flashes a level (volume or brightness) then fades."""

        def __init__(self, config: ShellConfig | None = None) -> None:
            super().__init__()
            self._anim_src: int | None = None
            self._fade_step = 0
            # The shell threads its LIVE config so theme == 'custom' renders
            # the derived palette; the fallback keeps direct construction
            # working (pre-existing snapshot behavior).
            self._config = config if config is not None else ShellConfig()

            self.set_decorated(False)
            self.set_resizable(False)

            if _HAS_LAYER and LayerShell.is_supported():
                LayerShell.init_for_window(self)
                LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
                LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
                LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, True)
                LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
                LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
                LayerShell.set_exclusive_zone(self, 0)
                LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
                LayerShell.set_margin(self, LayerShell.Edge.TOP, 96)

            self._css = Gtk.CssProvider()
            self._css.load_from_data(theme_css(self._display_preset()).encode('utf-8'))
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(), self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

            win_css = Gtk.CssProvider()
            win_css.load_from_data(b'window { background: transparent; }')
            self.get_style_context().add_provider(
                win_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 10,
            )

            self._label = Gtk.Label(label='50%')
            self._label.add_css_class('piercing-hud-label')

            self._level = Gtk.ProgressBar()
            self._level.add_css_class('piercing-hud-level')
            self._level.set_fraction(0.5)
            self._level.set_show_text(False)

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            box.add_css_class('piercing-hud-box')
            box.set_halign(Gtk.Align.CENTER)
            box.set_valign(Gtk.Align.CENTER)
            box.append(self._label)
            box.append(self._level)
            self.set_child(box)

            self.set_opacity(0.0)

        def _display_preset(self) -> ThemePreset:
            """Preset this overlay renders with: window.resolve_theme over
            the injected config, so theme == 'custom' derives its palette
            instead of falling back to the default preset (W1-B). Lazy
            import: window.py sits above this module in the shell stack,
            and a module-level import would drag GTK requirements into
            headless contexts that import hud for its silent-absence path.
            """
            from window import resolve_theme
            return resolve_theme(self._config)

        def apply_theme(self, preset: ThemePreset | None = None) -> None:
            data = theme_css(preset if preset is not None else self._display_preset())
            self._css.load_from_data(data.encode('utf-8'))

        def show_level(self, pct: int) -> None:
            """Display a clamped 0-100 level and schedule the auto-hide fade."""
            pct = max(0, min(100, int(pct)))
            self._label.set_text(f'{pct}%')
            self._level.set_fraction(pct / 100.0)

            if self._anim_src is not None:
                GLib.source_remove(self._anim_src)
                self._anim_src = None
            self.set_opacity(1.0)
            self.set_visible(True)
            self.present()
            self._anim_src = GLib.timeout_add(_HUD_HOLD_MS, self._begin_fade)

        def _begin_fade(self) -> bool:
            self._anim_src = None
            self._fade_step = 0
            self._anim_src = GLib.timeout_add(_HUD_FADE_INTERVAL_MS, self._tick_fade)
            return GLib.SOURCE_REMOVE

        def _tick_fade(self) -> bool:
            self._fade_step += 1
            alpha = 1.0 - self._fade_step / _HUD_FADE_STEPS
            if alpha <= 0.0:
                self.set_opacity(0.0)
                self.set_visible(False)
                self._anim_src = None
                return GLib.SOURCE_REMOVE
            self.set_opacity(alpha)
            return GLib.SOURCE_CONTINUE


class Hud:
    """Volume/brightness HUD overlay. Silent no-op when GTK or the layer shell is absent."""

    def __init__(self, app=None, config: ShellConfig | None = None) -> None:
        # Live ShellConfig when the shell threads it; a fresh snapshot keeps
        # direct no-arg construction working. Held here and handed to the
        # overlay so its sheet resolves through window.resolve_theme.
        self._config = config if config is not None else ShellConfig()
        self._window = _HudWindow(self._config) if _HAS_GTK else None
        if self._window is None:
            _log.info('HUD disabled: PyGObject unavailable')
        if app is not None:
            self.set_application(app)

    def set_application(self, app) -> None:
        if self._window is not None:
            self._window.set_application(app)

    def show_volume(self, pct: int) -> None:
        if self._window is not None:
            self._window.show_level(pct)

    def show_brightness(self, pct: int) -> None:
        if self._window is not None:
            self._window.show_level(pct)

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        if self._window is not None:
            self._window.apply_theme(preset)