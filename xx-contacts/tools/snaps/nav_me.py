# ruff: noqa: F821 (win/app/Gtk/GLib come from scripts/snap.py)
_open_me = getattr(win, "open_me_card", None)
if _open_me is not None:
    _open_me()
else:
    win._on_me_clicked(None)
