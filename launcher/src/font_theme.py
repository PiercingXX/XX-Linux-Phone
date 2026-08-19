"""
Launcher-wide font (design.md "Fonts"): one display-level provider sets the
configured family on every shell window and popover. Surfaces no longer
hardcode a family; anything needing a deliberate override (e.g. PIN dots)
still declares its own.
"""
from __future__ import annotations

_provider = None


def apply_global_font(family: str) -> None:
    global _provider
    from gi.repository import Gdk, Gtk
    display = Gdk.Display.get_default()
    if display is None:
        return
    if _provider is None:
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _provider.load_from_data(
        f"window, popover {{ font-family: '{family}'; }}".encode())
