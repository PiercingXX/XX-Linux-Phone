#!/usr/bin/env python3
"""Navigation payload for scripts/snap.py: drive xx-share to one screen.

Runs with ``win`` (the main window) and ``app`` in scope. SCREEN picks
the screen: share, history, favorites, settings, receive, sending.
Peers are network state, so they are injected through the window's real
peer-render path from the demo_seed roster.
"""

import gc
import os
import sys
from pathlib import Path

SCREEN = os.environ.get("SCREEN", "share")

sys.path.insert(0, str(Path(os.environ["SNAP_SCRIPT"]).parent))
from demo_seed import PEERS  # noqa: E402


def _share_window():
    for obj in gc.get_objects():
        if type(obj).__name__ == "ShareWindow":
            return obj
    raise RuntimeError("ShareWindow not found")


PEER_DICTS = [
    {"alias": alias, "fingerprint": fingerprint, "addr": addr,
     "device_type": kind, "port": 53317}
    for alias, fingerprint, addr, kind in PEERS
]

sw = _share_window()
sw._peers = [dict(peer) for peer in PEER_DICTS]
sw._render_peers()
sw.refresh_favorites()
sw._select_peer(PEER_DICTS[0]["fingerprint"])

if SCREEN in ("history", "favorites", "settings"):
    sw._show_tab(SCREEN)
elif SCREEN == "receive":
    sw.show_incoming("sess-demo", "Mara's Pixel",
                     ["IMG_20240612_184410.jpg", "birthday-dinner.mp4"],
                     50725158)
    sw.window.hide()
elif SCREEN == "sending":
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib as _GLib

    sw._peers = [dict(PEER_DICTS[0])]
    sw._render_peers()
    sw.refresh_favorites()
    sw._select_peer(PEER_DICTS[0]["fingerprint"])
    sw.files = sw.files[:1]
    sw.refresh_files()
    sw.current_transfer = "tx-demo"
    sw._idle_bus_progress("tx-demo", 12582912, 29360128)
    sw.cancel_button.set_sensitive(True)
    page = sw.stack.get_child_by_name("share")

    def _scroll(box):
        child = box.get_first_child()
        while child is not None:
            if type(child).__name__ == "ScrolledWindow":
                adj = child.get_vadjustment()
                adj.set_value(adj.get_upper() - adj.get_page_size())
            _scroll(child)
            child = child.get_next_sibling()

    def _late():
        _scroll(page)
        return False

    _GLib.timeout_add(900, _late)
