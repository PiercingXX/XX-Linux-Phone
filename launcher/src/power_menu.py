from __future__ import annotations

import subprocess
from typing import Callable

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

_CSS = b"""
.power-menu-scrim {
    background: rgba(0, 0, 0, 0.72);
}
.power-menu-btn {
    font-family: 'Space Mono', monospace;
    font-size: 18pt;
    font-weight: 300;
    min-height: 80px;
    min-width: 260px;
    border-radius: 8px;
    border: 1.5px solid rgba(255,255,255,0.12);
    background: rgba(30, 30, 30, 0.95);
    color: #f4f4f4;
    padding: 0 24px;
}
.power-menu-btn:hover, .power-menu-btn:active {
    background: rgba(50, 50, 50, 0.95);
}
.power-menu-cancel {
    color: #9a9a9a;
    border-color: rgba(255,255,255,0.06);
    background: rgba(10,10,10,0.95);
}
"""


class PowerMenu(Gtk.Window):
    """
    Full-screen semi-transparent overlay with Power off / Restart / Cancel.
    Triggered by long-pressing the power button (≥600ms).
    """

    def __init__(self) -> None:
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

        css = Gtk.CssProvider()
        css.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 5,
        )

        self.set_child(self._build())

        # Tap outside the card → cancel
        click = Gtk.GestureClick.new()
        click.connect('released', self._on_scrim_tap)
        self.get_child().add_controller(click)

        # Escape key → cancel
        key = Gtk.EventControllerKey.new()
        key.connect('key-pressed', self._on_key)
        self.add_controller(key)

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

        poweroff_btn = Gtk.Button(label='Power off')
        poweroff_btn.add_css_class('power-menu-btn')
        poweroff_btn.connect('clicked', lambda _: self._action('poweroff'))

        restart_btn = Gtk.Button(label='Restart')
        restart_btn.add_css_class('power-menu-btn')
        restart_btn.connect('clicked', lambda _: self._action('reboot'))

        cancel_btn = Gtk.Button(label='Cancel')
        cancel_btn.add_css_class('power-menu-btn')
        cancel_btn.add_css_class('power-menu-cancel')
        cancel_btn.connect('clicked', lambda _: self._dismiss())

        card.append(poweroff_btn)
        card.append(restart_btn)
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
