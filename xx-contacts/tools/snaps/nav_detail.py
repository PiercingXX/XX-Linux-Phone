# ruff: noqa: F821 (win/app/Gtk/GLib come from scripts/snap.py)
import json
import os

with open(os.path.join(os.environ["SEED_HOME"], "seed_uids.json"),
          encoding="utf-8") as _fh:
    _uids = json.load(_fh)
win._open_detail(_uids["ada_uid"])
