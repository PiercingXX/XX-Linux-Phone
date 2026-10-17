# ruff: noqa: F821 (win/app/Gtk/GLib come from scripts/snap.py)
import json
import os

with open(os.path.join(os.environ["SEED_HOME"], "seed_uids.json"),
          encoding="utf-8") as _fh:
    _uids = json.load(_fh)
from xx_contacts.editor_ui import ContactEditorPage

_page = ContactEditorPage(
    win._navigation, win, win._store, _uids["ada_uid"], win._db_path,
    win._photos_dir, config=win._config, config_path=win._config_path,
    default_region=win._config.default_region)
win._navigation.push(_page)
