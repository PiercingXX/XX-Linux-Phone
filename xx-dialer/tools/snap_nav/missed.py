# ruff: noqa: F821 -- snap.py execs this with win/app in scope
view = win.dialer_view
view.show_page("recents")
try:
    view.recents.filter_name = "missed"
    view.recents_view.refresh()
except Exception:
    pass
