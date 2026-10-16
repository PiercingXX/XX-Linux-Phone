"""Broadway driver: builds the REAL overlay window and pumps the loop.

Runs under GDK_BACKEND=broadway with HOME pointed at a temp dir:
first with no model (first-run download screen), then with a seeded
fake model (caption lines, partial->final, theme reload, drag
margin). Any exception fails the run; success prints SMOKE_OK.
"""
from __future__ import annotations

import os
import sys
import traceback

SRC_DIR = sys.argv[1]
HOME = sys.argv[2]

sys.path.insert(0, SRC_DIR)
os.environ["HOME"] = HOME

errors: list[str] = []


def _hook(exc_type, exc, tb):
    errors.append("".join(traceback.format_exception(exc_type, exc, tb)))


sys.excepthook = _hook

from gi.repository import GLib  # noqa: E402

from xx_captions import config  # noqa: E402
from xx_captions.overlay import CaptionWindow, layer_available  # noqa: E402
from xx_captions.theme import preset_for_key  # noqa: E402


def check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def main() -> int:
    check(not layer_available(),
          "layer-shell must fall back under Broadway (no Wayland)")
    home = __import__("pathlib").Path(HOME)
    config.ensure_dirs(home)

    bare = CaptionWindow(home=home)
    bare.present()
    check(bare._model_box.get_visible(), "first run must show model screen")
    check(not bare._caption_box.get_visible(), "captions hidden pre-model")
    check("MB" in bare._model_size.get_text(),
          f"size shown before download, got {bare._model_size.get_text()!r}")

    model_id = config.DEFAULT_CONFIG["model"]
    entry = config.MODEL_CATALOG[model_id]
    for name in entry["files"]:
        target = config.models_dir(home) / model_id / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake-model-bytes")

    window = CaptionWindow(home=home)
    window.present()
    check(window._caption_box.get_visible(), "caption view with model")
    check(not window._model_box.get_visible(), "model screen hidden")

    window.on_text("hello", False)
    check(window._lines[2].get_text() == "hello", "partial on last line")
    check("caption-line-partial" in
          window._lines[2].get_css_classes(), "partial styled muted")
    window.on_text("hello world", True)
    check(window._lines[2].get_text() == "hello world", "final commits")
    check("caption-line-partial" not in
          window._lines[2].get_css_classes(), "final styled solid")
    window.on_text("second line", True)
    window.on_text("third", False)
    texts = [line.get_text() for line in window._lines if line.get_visible()]
    check(texts == ["hello world", "second line", "third"],
          f"older lines scroll up, got {texts!r}")

    window.set_active(True, "playback")
    check("on" in window._status.get_text().lower(), "privacy row shows on")
    check(window._toggle.get_label() == "Stop", "toggle offers Stop")
    window.set_active(False, "")
    check(window._toggle.get_label() == "Start", "toggle offers Start")
    window.set_error("boom")
    check("boom" in window._status.get_text(), "error is user-facing")

    window.apply_theme(preset_for_key("ocean"))
    window.apply_margin(120)
    window._on_drag_begin(None, 0.0, 0.0)

    class _Drag:
        def get_offset(self):
            return (True, 0.0, -40.0)

    window._on_drag_update(_Drag(), 0.0, 0.0)
    check(window._margin == 160,
          f"drag up grows the bottom margin, got {window._margin}")
    window._on_drag_end(None, 0.0, 0.0)
    check(config.load(home)["overlay_margin_bottom"] == 160,
          "dragged position persists")

    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(2, loop.quit)
    loop.run()

    width = window.get_width() if hasattr(window, "get_width") else 0
    check(width <= 360, f"overlay fits 360px phone width, got {width}")
    bare.close()
    window.close()
    if errors:
        raise AssertionError("excepthook caught:\n" + "\n".join(errors))
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
