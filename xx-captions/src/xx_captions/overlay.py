"""Caption overlay: its own small GTK4 process (`xx-captions --overlay`).

Layer OVERLAY, keyboard NONE, anchored bottom with the persisted
margin above the gesture area, exclusive zone 0. Taps pass through
everywhere except the drag handle and the close button (empty input
region plus those two rects). Partials replace the last line in
place; the matching final commits it (CaptionBuffer), so labels only
ever change text, never rebuild. Colors come from the XX-WM preset
with hot-reload; the recording dot is the shell's danger red, the one
fixed color allowed. Engine and download work stays on worker threads
and posts back with GLib.idle_add, so the main loop never blocks.

Without layer-shell (Broadway, test VMs) the same window falls back
to a normal undecorated window; without GTK the module still imports
and run_overlay() returns 2.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from xx_captions import config, models
from xx_captions.client import CaptionsClient
from xx_captions.overlay_state import (
    CaptionBuffer,
    clamp_margin,
    download_label,
    load_margin,
    model_missing,
    phone_default_model,
    progress_fraction,
    save_margin,
)
from xx_captions.theme import CaptionPalette, caption_css, resolve_palette

_log = logging.getLogger("captions-overlay")

APPLICATION_ID = "io.piercingxx.Captions"

RECORD_RED = "#ff6b6b"

_HANDLE_HEIGHT = 48

_OVERLAY_CSS_EXTRA = """
.caption-status {
    font-size: 9pt;
    letter-spacing: 0.08em;
}
.caption-rec-on {
    color: #ff6b6b;
}
.caption-toggle, .caption-close {
    font-size: 10pt;
}
.caption-model-size {
    font-size: 11pt;
}
.caption-error {
    font-size: 10pt;
}
"""

_HAS_GTK = True
try:
    import gi
except ImportError:
    _HAS_GTK = False

if _HAS_GTK:
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        _HAS_LAYER = True
    except (ValueError, ImportError):
        _HAS_LAYER = False

    gi.require_version("Gtk", "4.0")
    try:
        gi.require_version("Pango", "1.0")
    except (ValueError, ImportError):
        pass
    from gi.repository import Gio, GLib, Gtk, Pango

    if _HAS_LAYER:
        from gi.repository import Gtk4LayerShell as LayerShell


def layer_available() -> bool:
    """True when the layer-shell seam can own this window."""
    if not _HAS_GTK or not _HAS_LAYER:
        return False
    try:
        return bool(LayerShell.is_supported())
    except Exception:
        return False


class DownloadCancelled(Exception):
    pass


if _HAS_GTK:
    class CaptionWindow(Gtk.Window):
        """Bottom overlay: caption lines, or the first-run model screen."""

        def __init__(self, home=None, client: CaptionsClient | None = None,
                     model_id: str | None = None) -> None:
            super().__init__()
            self._home = home
            self._model_id = model_id or phone_default_model()
            self._buffer = CaptionBuffer()
            self._margin = load_margin(home)
            self._client = client
            self._downloading = False
            self._cancel_download = threading.Event()
            self._drag_start_margin = self._margin
            self._theme_file_monitor = None

            self.set_decorated(False)
            self.set_resizable(False)

            if layer_available():
                LayerShell.init_for_window(self)
                LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
                LayerShell.set_anchor(self, LayerShell.Edge.TOP, False)
                LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, True)
                LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
                LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
                LayerShell.set_exclusive_zone(self, 0)
                LayerShell.set_keyboard_mode(
                    self, LayerShell.KeyboardMode.NONE)
                LayerShell.set_margin(
                    self, LayerShell.Edge.BOTTOM, self._margin)

            self._theme_provider = Gtk.CssProvider()
            self._theme_provider.load_from_data(
                self._sheet(resolve_palette(home)).encode("utf-8"))
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(), self._theme_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            handle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                             spacing=8)
            handle.add_css_class("caption-rec")
            self._status = Gtk.Label(label="Captions off")
            self._status.add_css_class("caption-status")
            self._status.set_halign(Gtk.Align.START)
            self._status.set_hexpand(True)
            self._status.set_ellipsize(Pango.EllipsizeMode.END)
            handle.append(self._status)
            self._toggle = Gtk.Button(label="Start")
            self._toggle.add_css_class("caption-toggle")
            self._toggle.connect("clicked", self._on_toggle_clicked)
            handle.append(self._toggle)
            self._close = Gtk.Button(label="Close")
            self._close.add_css_class("caption-close")
            self._close.connect("clicked", self._on_close_clicked)
            handle.append(self._close)
            self._handle = handle

            drag = Gtk.GestureDrag.new()
            drag.connect("drag-begin", self._on_drag_begin)
            drag.connect("drag-update", self._on_drag_update)
            drag.connect("drag-end", self._on_drag_end)
            self._status.add_controller(drag)

            self._lines: list = []
            caption_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=2)
            for _ in range(3):
                line = Gtk.Label(label="")
                line.add_css_class("caption-line")
                line.set_halign(Gtk.Align.START)
                line.set_xalign(0.0)
                line.set_wrap(True)
                line.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                line.set_ellipsize(Pango.EllipsizeMode.END)
                line.set_max_width_chars(38)
                line.set_visible(False)
                caption_box.append(line)
                self._lines.append(line)
            self._caption_box = caption_box

            self._model_title = Gtk.Label(label="Captions need a model")
            self._model_title.add_css_class("caption-line")
            self._model_title.set_wrap(True)
            self._model_title.set_xalign(0.0)
            self._model_size = Gtk.Label(label="")
            self._model_size.add_css_class("caption-model-size")
            self._model_size.set_wrap(True)
            self._model_size.set_xalign(0.0)
            self._progress = Gtk.ProgressBar()
            self._progress.set_show_text(False)
            self._progress.set_visible(False)
            self._download = Gtk.Button(label="Download")
            self._download.connect("clicked", self._on_download_clicked)
            self._cancel = Gtk.Button(label="Cancel")
            self._cancel.connect("clicked", self._on_cancel_clicked)
            self._cancel.set_visible(False)
            self._model_error = Gtk.Label(label="")
            self._model_error.add_css_class("caption-error")
            self._model_error.set_wrap(True)
            self._model_error.set_xalign(0.0)
            self._model_error.set_visible(False)
            model_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=8)
            model_box.append(self._model_title)
            model_box.append(self._model_size)
            model_box.append(self._progress)
            model_box.append(self._download)
            model_box.append(self._cancel)
            model_box.append(self._model_error)
            self._model_box = model_box

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            root.add_css_class("caption-root")
            root.set_size_request(336, -1)
            root.set_halign(Gtk.Align.CENTER)
            root.append(handle)
            root.append(caption_box)
            root.append(model_box)
            self.set_child(root)

            self.connect("realize", self._on_realize)
            self._watch_shell_theme()
            if model_missing(home):
                self.show_model_view()
            else:
                self.show_caption_view()

        def _sheet(self, palette: CaptionPalette) -> str:
            return caption_css(palette) + _OVERLAY_CSS_EXTRA

        def apply_theme(self, palette: CaptionPalette | None = None) -> None:
            """Reload the display sheet from the current shell preset."""
            data = self._sheet(palette or resolve_palette(self._home))
            self._theme_provider.load_from_data(data.encode("utf-8"))

        def _watch_shell_theme(self) -> None:
            home = self._home or Path.home()
            watched = home / ".config" / "xx-wm" / "config.json"
            try:
                watched.parent.mkdir(parents=True, exist_ok=True)
                monitor = Gio.File.new_for_path(str(watched)).monitor_file(
                    Gio.FileMonitorFlags.NONE, None)
                monitor.connect("changed", self._on_shell_theme_changed)
                self._theme_file_monitor = monitor
            except Exception:
                self._theme_file_monitor = None

        def _on_shell_theme_changed(self, _monitor, _file, _other,
                                    _event) -> None:
            try:
                self.apply_theme()
            except Exception:
                pass

        def show_caption_view(self) -> None:
            self._model_box.set_visible(False)
            self._caption_box.set_visible(True)
            self.render()

        def show_model_view(self) -> None:
            self._caption_box.set_visible(False)
            self._model_box.set_visible(True)
            self._model_size.set_text(download_label(self._model_id))
            self._progress.set_visible(False)
            self._cancel.set_visible(False)
            self._download.set_visible(True)
            self._model_error.set_visible(False)

        def on_text(self, text: str, final: bool) -> None:
            if final:
                self._buffer.push_final(text)
            else:
                self._buffer.push_partial(text)
            self.render()

        def render(self) -> None:
            texts = list(self._buffer.lines()[-3:])
            partial = self._buffer.live_partial()
            slot = [""] * (3 - len(texts)) + texts
            for pos, (label, text) in enumerate(zip(self._lines, slot)):
                if label.get_text() != text:
                    label.set_text(text)
                label.set_visible(bool(text))
                is_partial = bool(partial) and pos == 2 and text == partial
                if is_partial:
                    label.add_css_class("caption-line-partial")
                else:
                    label.remove_css_class("caption-line-partial")
            GLib.idle_add(self._refresh_input_region, priority=GLib.PRIORITY_LOW)

        def set_active(self, active: bool, _source: str = "") -> None:
            if active:
                self._status.set_text("\u25cf Captions on")
                self._status.add_css_class("caption-rec-on")
                self._toggle.set_label("Stop")
            else:
                self._status.set_text("Captions off")
                self._status.remove_css_class("caption-rec-on")
                self._toggle.set_label("Start")

        def set_starting(self) -> None:
            self._status.set_text("Starting captions\u2026")
            self._status.remove_css_class("caption-rec-on")

        def set_error(self, message: str) -> None:
            self._status.set_text(f"Captions error: {message}")
            self._status.remove_css_class("caption-rec-on")
            self._status.set_ellipsize(Pango.EllipsizeMode.END)

        def _on_toggle_clicked(self, _button) -> None:
            client = self._client
            if client is None or not client.connected:
                self.set_error("service unavailable")
                return
            if client.active:
                client.stop(done=self._on_toggle_done)
            else:
                self.set_starting()
                client.start("playback", done=self._on_toggle_done)

        def _on_toggle_done(self, error: str | None) -> None:
            if error:
                GLib.idle_add(self.set_error, error)
            else:
                GLib.idle_add(self._refresh_props_idle)

        def _refresh_props_idle(self) -> bool:
            try:
                if self._client is not None:
                    self._client.refresh_props()
            except Exception:
                pass
            return False

        def _on_close_clicked(self, _button) -> None:
            try:
                if self._client is not None and self._client.active:
                    self._client.stop()
            except Exception:
                pass
            self.close()

        def _on_download_clicked(self, _button) -> None:
            if self._downloading:
                return
            self._downloading = True
            self._cancel_download.clear()
            self._model_error.set_visible(False)
            self._progress.set_visible(True)
            self._progress.set_fraction(0.0)
            self._cancel.set_visible(True)
            self._download.set_visible(False)
            thread = threading.Thread(target=self._download_worker,
                                      daemon=True)
            thread.start()

        def _on_cancel_clicked(self, _button) -> None:
            self._cancel_download.set()

        def _download_progress(self, done: int, total: int | None) -> None:
            if self._cancel_download.is_set():
                raise DownloadCancelled("cancelled")
            fraction = progress_fraction(done, total)
            GLib.idle_add(self._progress.set_fraction, fraction)

        def _download_worker(self) -> None:
            error: str | None = None
            try:
                models.download(self._model_id, self._home,
                                progress=self._download_progress)
            except DownloadCancelled:
                error = "Download cancelled"
            except Exception as exc:
                _log.warning("model download failed: %s", exc)
                error = "model download failed"
            GLib.idle_add(self._on_download_finished, error)

        def _on_download_finished(self, error: str | None) -> bool:
            self._downloading = False
            self._cancel.set_visible(False)
            if error is None:
                self.show_caption_view()
            else:
                self._download.set_visible(True)
                self._progress.set_visible(False)
                self._model_error.set_text(error)
                self._model_error.set_visible(True)
            return False

        def apply_margin(self, margin: int) -> int:
            clamped = clamp_margin(margin)
            self._margin = clamped
            if layer_available():
                try:
                    LayerShell.set_margin(
                        self, LayerShell.Edge.BOTTOM, clamped)
                except Exception:
                    pass
            return clamped

        def _on_drag_begin(self, _gesture, _x: float, _y: float) -> None:
            self._drag_start_margin = self._margin

        def _on_drag_update(self, gesture, _x: float, _y: float) -> None:
            try:
                _ok, _offset_x, offset_y = gesture.get_offset()
            except Exception:
                return
            self.apply_margin(int(self._drag_start_margin - offset_y))

        def _on_drag_end(self, _gesture, _x: float, _y: float) -> None:
            try:
                save_margin(self._margin, self._home)
            except Exception:
                pass

        def _on_realize(self, _widget) -> None:
            self._refresh_input_region()

        def _refresh_input_region(self) -> bool:
            """Pass taps through except on the handle and close button."""
            try:
                import cairo
            except ImportError:
                return False
            try:
                surface = self.get_surface()
                if surface is None:
                    return False
                region = cairo.Region()
                for widget in (self._handle, self._close):
                    alloc = widget.get_allocation()
                    rect = cairo.RectangleInt(alloc.x, alloc.y,
                                             alloc.width, alloc.height)
                    region = region.union_rectangle(rect)
                surface.set_input_region(region)
            except Exception:
                pass
            return False

    class CaptionApp(Gtk.Application):
        """Owns the window and the service client for one overlay."""

        def __init__(self, home=None) -> None:
            super().__init__(application_id=APPLICATION_ID)
            self._home = home
            self._window: CaptionWindow | None = None

        def do_activate(self) -> None:
            if self._window is None:
                client = CaptionsClient(on_text=self._on_text,
                                        on_props=self._on_props)
                window = CaptionWindow(home=self._home, client=client)
                client._on_text = window.on_text
                client._on_props = window.set_active
                client.connect()
                client.refresh_props()
                window.set_application(self)
                window.present()
                self._window = window
            else:
                self._window.present()

        def _on_text(self, _text: str, _final: bool) -> None:
            pass

        def _on_props(self, _active: bool, _source: str) -> None:
            pass


def run_overlay(home=None) -> int:
    """Show the overlay; 2 when GTK is unavailable (never crashes)."""
    if not _HAS_GTK:
        _log.error("overlay needs GTK4; not available")
        return 2
    config.ensure_dirs(home)
    app = CaptionApp(home)
    return int(app.run([]))


__all__ = [
    "APPLICATION_ID",
    "RECORD_RED",
    "CaptionApp",
    "CaptionWindow",
    "DownloadCancelled",
    "layer_available",
    "run_overlay",
]
