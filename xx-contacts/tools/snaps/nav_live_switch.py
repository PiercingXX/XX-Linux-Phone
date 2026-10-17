# ruff: noqa: F821 (win/app/Gtk/GLib come from scripts/snap.py)
"""Prove live theme switching: flip amoled to paper after launch."""
import json
import os


def _switch():
    cfg = os.path.join(os.environ["SEED_HOME"], ".config", "xx-wm",
                       "config.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump({"theme": "paper"}, fh)
    return False


GLib.timeout_add(1200, _switch)
