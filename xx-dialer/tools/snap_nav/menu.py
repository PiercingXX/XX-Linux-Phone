# ruff: noqa: F821 -- snap.py execs this with win/app in scope
view = win.dialer_view
view.show_page("recents")
try:
    calls = view.recents.merged
    if calls:
        key = "row:%d" % calls[0].key
        pop = view.recents_view._open_menu(key, view.recents_view._list)
        if pop is not None:
            pop.popdown()
            pop.unparent()
except Exception:
    pass
