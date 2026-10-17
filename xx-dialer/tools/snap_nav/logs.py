# ruff: noqa: F821 -- snap.py execs this with win/app in scope
# Logs refreshes itself at build; the page is shown as built.
view = win.dialer_view
view.show_page("logs")
