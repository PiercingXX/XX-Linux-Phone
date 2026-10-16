"""xx-captions command line (headless-safe; GTK only enters in lane 3)."""
from __future__ import annotations

import argparse
import os
import sys

from xx_captions import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xx-captions",
        description="On-device live captions and call transcription.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--serve", action="store_true",
                        help="run the io.piercingxx.Captions1 session bus"
                        " service (default with no arguments)")
    parser.add_argument("--overlay", action="store_true",
                        help="run the caption overlay window"
                        " (application id io.piercingxx.Captions)")
    return parser


def find_layer_shell_lib() -> str | None:
    """Path of libgtk4-layer-shell.so.0, or None when not installed."""
    if "libgtk4-layer-shell" in os.environ.get("LD_PRELOAD", ""):
        return None
    import glob
    import shutil
    import subprocess

    try:
        ldconfig = shutil.which("ldconfig")
        if ldconfig is not None:
            proc = subprocess.run(
                [ldconfig, "-p"], capture_output=True, text=True,
                timeout=10)
            for line in proc.stdout.splitlines():
                if "libgtk4-layer-shell.so.0" in line:
                    candidate = line.split("=>")[-1].strip().split()[0]
                    if os.path.exists(candidate):
                        return candidate
    except (OSError, ValueError):
        pass
    for candidate in glob.glob("/usr/lib/*/libgtk4-layer-shell.so.0"):
        if os.path.exists(candidate):
            return candidate
    if os.path.exists("/usr/lib/libgtk4-layer-shell.so.0"):
        return "/usr/lib/libgtk4-layer-shell.so.0"
    return None


def ensure_layer_shell_preload() -> None:
    """Re-exec with the layer-shell lib preloaded, like xx-wm.in does.

    gtk4-layer-shell must load before libgtk-4 or every layer surface
    silently falls back to a normal window. Python imports GTK first,
    so the overlay restarts itself once with LD_PRELOAD set; the
    marker env var keeps this to a single re-exec.
    """
    if os.environ.get("XX_CAPTIONS_LAYER_PRELOADED") == "1":
        return
    lib = find_layer_shell_lib()
    if lib is None:
        return
    preload = lib
    if os.environ.get("LD_PRELOAD"):
        preload = os.environ["LD_PRELOAD"] + ":" + lib
    env = dict(os.environ)
    env["LD_PRELOAD"] = preload
    env["XX_CAPTIONS_LAYER_PRELOADED"] = "1"
    os.execvpe(sys.argv[0], sys.argv, env)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.overlay:
        ensure_layer_shell_preload()
        from xx_captions.overlay import run_overlay

        return run_overlay()
    if args.serve or not argv:
        from xx_captions.service import serve_forever

        serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
