"""Navigation driver for scripts/snap.py (run with ``win`` in scope).

SNAP_TARGET selects the surface: home, journal, browse, detail, profile,
add-activity, add-weight or menu. Windows are captured by snap.py itself;
dialogs and popovers are separate surfaces, so those targets snapshot the
fresh widget straight to DIALOG_OUT while snap.py writes a dummy.
"""

import os

TARGET = os.environ.get("SNAP_TARGET", "home")


def snap_widget(widget, path: str) -> bool:
    import gi

    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene, Gtk

    width, height = widget.get_width(), widget.get_height()
    if width <= 0 or height <= 0:
        return True  # not allocated yet; retry on the next tick
    snap = Gtk.Snapshot()
    snap.scale(2, 2)
    Gtk.WidgetPaintable.new(widget).snapshot(snap, width, height)
    node = snap.to_node()
    rect = Graphene.Rect().init(0, 0, width * 2, height * 2)
    native = widget.get_native()
    if native is None:
        return True
    texture = native.get_renderer().render_texture(node, rect)
    if texture is not None:
        texture.save_to_png(path)
        print("snap: wrote", path, flush=True)
        return False
    return True


nav = win._vitals_nav  # noqa: F821

if TARGET in ("home", "journal", "browse", "profile"):
    nav["show"](TARGET)
elif TARGET == "detail":
    nav["show"]("browse")
    nav["open_metric"]("steps")
elif TARGET in ("add-activity", "add-weight"):
    out = os.environ["DIALOG_OUT"]
    if TARGET == "add-activity":
        dialog = nav["add_activity"]()
    else:
        dialog = nav["add_weight"]()
    # Poll fast and keep the first allocated frame; snap.py's own capture
    # (at 2x --delay) bounds the run.
    GLib.timeout_add(200, lambda: snap_widget(dialog, out))  # noqa: F821
elif TARGET == "menu":
    out = os.environ["DIALOG_OUT"]
    page = os.environ.get("SNAP_MENU_PAGE", "home")
    nav["show"](page)
    popover = nav["open_menu"](page)
    GLib.timeout_add(200, lambda: snap_widget(popover, out))  # noqa: F821
