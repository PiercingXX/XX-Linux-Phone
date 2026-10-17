# ruff: noqa: F821 -- snap.py execs this with win/app in scope
# Lower half of the Rules page (patterns, blocked, test, tallies, log).
# The page lays out on its own schedule after first show, so poll until
# the adjustment has a range and keep pinning the bottom while it grows.
# A settled bottom long before the snapshot keeps the capture reliable.
view = win.dialer_view
view.show_page("rules")
state = {"done": False, "stable": 0, "upper": 0.0}


def _poll():
    try:
        page = view.rules.widget
        kid = page.get_first_child()
        while kid is not None:
            if isinstance(kid, Gtk.ScrolledWindow):
                adj = kid.get_vadjustment()
                upper = adj.get_upper()
                if upper > adj.get_page_size() > 0:
                    adj.set_value(upper - adj.get_page_size())
                    if upper == state["upper"]:
                        state["stable"] += 1
                    else:
                        state["upper"] = upper
                        state["stable"] = 0
                    if state["stable"] >= 5:
                        return False
                return True
            kid = kid.get_next_sibling()
    except Exception:
        pass
    return True


GLib.timeout_add(50, _poll)
