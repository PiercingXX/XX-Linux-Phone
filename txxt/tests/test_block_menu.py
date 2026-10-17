"""Conversation-menu Block must write the blocklist (bug_txxt finding 30).

Headless: the page is allocated without ``__init__`` (no display), the
worker body runs inline, and ``GLib.idle_add`` is captured so no dialog
is built.
"""

from gi.repository import GLib

from txxt import blocking as blocking_mod
from txxt import conversations as conv_mod
from txxt.store import Store


class _Resolver:
    def __init__(self):
        self.calls = []

    def set_blocked(self, number, blocked):
        self.calls.append((number, blocked))
        return True


def _page(store, resolver):
    page = conv_mod.ConversationListPage.__new__(conv_mod.ConversationListPage)
    page._store = store
    page._resolver = resolver
    return page


def test_block_menu_writes_sender_blocklist(monkeypatch):
    store = Store.memory()
    resolver = _Resolver()
    page = _page(store, resolver)
    posted = []
    monkeypatch.setattr(GLib, "idle_add", lambda *args: posted.append(args))
    page._block_in_worker("+1555")
    assert ("sender", "+1555") in store.blocklist_list("sender")
    assert resolver.calls == [("+1555", True)]
    assert posted and posted[0][1] == "+1555" and posted[0][2] is True


def test_block_menu_rebuilds_live_filter(monkeypatch):
    store = Store.memory()
    page = _page(store, None)
    monkeypatch.setattr(GLib, "idle_add", lambda *args: None)
    blocking_mod.live_reset_for_test()
    try:
        page._block_in_worker("+15550001111")
        from txxt.blocking import MessageDisposition, live_current

        assert live_current().evaluate("+15550001111", "hi")[0] is MessageDisposition.BLOCK
    finally:
        blocking_mod.live_reset_for_test()


def test_block_menu_without_resolver_still_blocks(monkeypatch):
    store = Store.memory()
    page = _page(store, None)
    posted = []
    monkeypatch.setattr(GLib, "idle_add", lambda *args: posted.append(args))
    page._block_in_worker("+1555")
    assert ("sender", "+1555") in store.blocklist_list("sender")
    assert posted and posted[0][2] is True
