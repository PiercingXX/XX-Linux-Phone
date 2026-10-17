"""Contact avatars: a round photo with an initials fallback.

Import-safe without a display: constructing widgets needs one, but
importing this module must not, so pytest never instantiates it. The
fallback disc uses the ``xx-avatar`` class from the design system, so no
hardcoded palette sneaks in; the one app-specific rule (round photos,
flat search) lives in ``app.css`` next to this module.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from pathlib import Path  # noqa: E402

from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

from xx_contacts.names import initials  # noqa: E402

_CSS_LOADED = False


def ensure_app_css() -> None:
    """Install the app-specific rules once; no-op without a display."""
    global _CSS_LOADED
    if _CSS_LOADED:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    try:
        css = (Path(__file__).with_name("app.css")
               .read_text(encoding="utf-8"))
    except OSError:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(css.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _CSS_LOADED = True


def build_avatar(size_px: int):
    """A fixed-size round avatar: ``(box, image, fallback)``.

    ``image`` shows the photo when one loads; ``fallback`` shows
    initials otherwise. Call :func:`set_avatar` to switch between them.
    """
    ensure_app_css()
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    box.set_size_request(size_px, size_px)
    box.add_css_class("xx-contact-photo")
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    fallback = Gtk.Label()
    fallback.add_css_class("xx-avatar")
    fallback.set_size_request(size_px, size_px)
    fallback.set_hexpand(True)
    fallback.set_vexpand(True)
    fallback.set_halign(Gtk.Align.CENTER)
    fallback.set_valign(Gtk.Align.CENTER)
    image = Gtk.Image()
    image.set_pixel_size(size_px)
    image.set_hexpand(True)
    image.set_vexpand(True)
    box.append(fallback)
    box.append(image)
    image.set_visible(False)
    return box, image, fallback


def pixbuf_from_bytes(raw: bytes, size_px: int):
    """Decode image bytes to a square-ish pixbuf fitting ``size_px``."""
    from gi.repository import GLib

    loader = GdkPixbuf.PixbufLoader.new()
    try:
        loader.write_bytes(GLib.Bytes(raw))
    except AttributeError:
        loader.write(raw)
    loader.close()
    pixbuf = loader.get_pixbuf()
    if pixbuf is None:
        return None
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    longest = max(width, height)
    if longest <= size_px:
        return pixbuf
    scale = size_px / longest
    return pixbuf.scale_simple(max(1, round(width * scale)),
                               max(1, round(height * scale)),
                               GdkPixbuf.InterpType.BILINEAR)


def set_avatar(image: Gtk.Image, fallback: Gtk.Label, display_name: str,
               photo_path: str | None, show_photos: bool,
               size_px: int) -> None:
    """Show the photo when allowed and loadable, else initials."""
    ensure_app_css()
    if show_photos and photo_path:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                photo_path, size_px, size_px, True)
        except Exception:
            pixbuf = None
        if pixbuf is not None:
            image.set_from_pixbuf(pixbuf)
            image.set_visible(True)
            fallback.set_visible(False)
            return
    fallback.set_text(initials(display_name) or "?")
    fallback.set_visible(True)
    image.set_visible(False)
