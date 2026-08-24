from __future__ import annotations

import subprocess

import gi

try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _HAS_LAYER = True
except ValueError:
    _HAS_LAYER = False

gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gtk

if _HAS_LAYER:
    from gi.repository import Gtk4LayerShell as LayerShell

from config import ShellConfig, ThemePreset


def theme_css(preset: ThemePreset) -> str:
    return f"""
.power-menu-scrim {{
    background: alpha({preset.background}, 0.72);
}}
.power-menu-btn {{
    font-size: 18pt;
    font-weight: 300;
    min-height: 80px;
    min-width: 260px;
    border-radius: 8px;
    border: 1.5px solid alpha({preset.foreground}, 0.12);
    background: alpha({preset.surface}, 0.95);
    color: {preset.foreground};
    padding: 0 24px;
}}
.power-menu-btn:hover, .power-menu-btn:active {{
    background: alpha({preset.surface_alt}, 0.95);
    border-color: alpha({preset.accent}, 0.6);
}}
.power-menu-cancel {{
    color: {preset.muted};
    border-color: alpha({preset.foreground}, 0.06);
    background: alpha({preset.background}, 0.95);
}}
"""


class PowerMenu(Gtk.Window):
    """
    Full-screen semi-transparent overlay with Power off / Restart / Cancel.
    Triggered by long-pressing the power button (≥600ms).
    """

    def __init__(self, config: ShellConfig | None = None) -> None:
        super().__init__()
        self.set_decorated(False)
        self.set_resizable(False)

        if _HAS_LAYER and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, True)
            LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_exclusive_zone(self, -1)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        # The shell window threads its LIVE config so hot reloads reach this
        # surface; the fallback keeps direct no-arg construction working.
        self._config = config if config is not None else ShellConfig()
        self._theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 5,
        )
        self.apply_theme()

        self.set_child(self._build())

        # Tap outside the card → cancel
        click = Gtk.GestureClick.new()
        click.connect('released', self._on_scrim_tap)
        self.get_child().add_controller(click)

        # Escape key → cancel
        key = Gtk.EventControllerKey.new()
        key.connect('key-pressed', self._on_key)
        self.add_controller(key)

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """(Re)load this surface's display-level sheet. The shell window
        passes the freshly resolved preset on hot-reload fan-out; standalone
        falls back to the injected config's own preset."""
        data = theme_css(preset if preset is not None else self._config.theme)
        self._theme_provider.load_from_data(data.encode('utf-8'))

    def _build(self) -> Gtk.Widget:
        scrim = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrim.add_css_class('power-menu-scrim')
        scrim.set_halign(Gtk.Align.FILL)
        scrim.set_valign(Gtk.Align.FILL)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        card.set_halign(Gtk.Align.CENTER)
        card.set_valign(Gtk.Align.CENTER)
        card.set_margin_start(32)
        card.set_margin_end(32)

        suspend_btn = Gtk.Button(label='Suspend')
        suspend_btn.add_css_class('power-menu-btn')
        suspend_btn.connect('clicked', lambda _: self._action('suspend'))

        lock_btn = Gtk.Button(label='Log out')
        lock_btn.add_css_class('power-menu-btn')
        lock_btn.connect('clicked', lambda _: self._logout())

        restart_btn = Gtk.Button(label='Restart')
        restart_btn.add_css_class('power-menu-btn')
        restart_btn.connect('clicked', lambda _: self._action('reboot'))

        poweroff_btn = Gtk.Button(label='Power off')
        poweroff_btn.add_css_class('power-menu-btn')
        poweroff_btn.connect('clicked', lambda _: self._action('poweroff'))

        cancel_btn = Gtk.Button(label='Cancel')
        cancel_btn.add_css_class('power-menu-btn')
        cancel_btn.add_css_class('power-menu-cancel')
        cancel_btn.connect('clicked', lambda _: self._dismiss())

        card.append(suspend_btn)
        card.append(lock_btn)
        card.append(restart_btn)
        card.append(poweroff_btn)
        card.append(cancel_btn)
        scrim.append(card)
        return scrim

    def show_menu(self) -> None:
        self.set_visible(True)
        self.present()

    def _dismiss(self) -> None:
        self.set_visible(False)

    def _action(self, cmd: str) -> None:
        self._dismiss()
        try:
            subprocess.Popen(['systemctl', cmd], close_fds=True)
        except FileNotFoundError:
            pass

    def _logout(self) -> None:
        # Quitting the shell ends the phoc session (phoc -E child exits),
        # returning to the display manager — the shell's "log out".
        self._dismiss()
        app = self.get_application()
        if app is not None:
            app.quit()

    def _on_scrim_tap(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        # Dismiss if tap lands outside the card widget
        card = self.get_child().get_first_child()
        alloc = card.get_allocation()
        scrim_alloc = self.get_child().get_allocation()
        # card is centered — compute its bounding box relative to scrim
        card_x = (scrim_alloc.width - alloc.width) / 2
        card_y = (scrim_alloc.height - alloc.height) / 2
        if not (card_x <= x <= card_x + alloc.width and card_y <= y <= card_y + alloc.height):
            self._dismiss()

    def _on_key(self, _ctrl: Gtk.EventControllerKey, keyval: int, _code: int, _state: object) -> bool:
        if keyval == Gdk.KEY_Escape:
            self._dismiss()
            return True
        return False
