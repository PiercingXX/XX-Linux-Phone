"""Finding 4 repro: publish must never touch the default address book.

``EBookClient()`` falls back to ``ref_default_address_book()`` and
``sync_to_eds`` deletes every UID there that is not in xx-contacts, so
enabling publishing wipes the user's real (e.g. CardDAV) contacts. The
publisher must resolve the dedicated "XX Contacts" source instead, and
the flush must not run blocking EDS calls on the calling (GTK) thread.
"""

import sys
import threading
import types

from xx_contacts import eds as eds_module


class FakeSource:
    def __init__(self, display_name, uid):
        self._display_name = display_name
        self.uid = uid

    def get_display_name(self):
        return self._display_name


class FakeRegistry:
    """Duck-typed EDS source registry; the default book is fatal."""

    def __init__(self, sources=()):
        self._sources = list(sources)
        self.default_used = False
        self.committed = []

    def list_sources(self, _extension):
        return list(self._sources)

    def ref_default_address_book(self):
        self.default_used = True
        raise AssertionError("publish must never open the default book")

    def ref_source(self, uid):
        for source in self._sources:
            if source.uid == uid:
                return source
        raise LookupError(uid)

    def commit_source_sync(self, source, _cancellable):
        self.committed.append(source)
        self._sources.append(source)


def _install_fake_gi(monkeypatch, registry):
    connected = {}

    class FakeBookClient:
        @staticmethod
        def connect_sync(source, _cancellable):
            connected["source"] = source
            return object()

    fake_ebook = types.SimpleNamespace(
        SourceRegistry=types.SimpleNamespace(
            new_sync=lambda _cancellable: registry),
        BookClient=FakeBookClient,
    )
    fake_eds = types.SimpleNamespace(
        SOURCE_EXTENSION_ADDRESS_BOOK="Address Book",
        Source=types.SimpleNamespace(
            new=lambda _a, _b: FakeSource("", "new-uid")),
    )
    fake_repo = types.SimpleNamespace(EBook=fake_ebook,
                                      EDataServer=fake_eds)
    fake_gi = types.SimpleNamespace(require_version=lambda *_a: None,
                                    repository=fake_repo)
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)
    return connected


def test_publish_client_uses_dedicated_source_not_default(monkeypatch):
    registry = FakeRegistry([FakeSource("Personal", "personal"),
                             FakeSource("XX Contacts", "xx")])
    connected = _install_fake_gi(monkeypatch, registry)
    monkeypatch.setattr(eds_module, "is_available", lambda: True)
    eds_module.make_publish_client()
    assert connected["source"].uid == "xx"
    assert registry.default_used is False


def test_publish_client_creates_dedicated_source(monkeypatch):
    registry = FakeRegistry([FakeSource("Personal", "personal")])
    connected = _install_fake_gi(monkeypatch, registry)
    monkeypatch.setattr(eds_module, "is_available", lambda: True)

    created = FakeSource("XX Contacts", "fresh")
    fake_source_cls = types.SimpleNamespace(new=lambda _a, _b: created)

    import gi  # noqa: F401  (fake, installed above)

    monkeypatch.setattr(gi.repository.EDataServer, "Source",
                        fake_source_cls, raising=False)
    created.get_display_name = lambda: "XX Contacts"
    created.set_display_name = lambda _name: None
    created.get_extension = lambda _ext: types.SimpleNamespace(
        set_backend_name=lambda _b: None)
    eds_module.make_publish_client()
    assert registry.default_used is False
    assert connected["source"].get_display_name() == "XX Contacts"


def test_publish_flush_runs_off_the_calling_thread(monkeypatch):
    main_thread = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    seen = {}
    real_sync = eds_module.sync_to_eds

    def _blocking_sync(records, client):
        seen["thread"] = threading.get_ident()
        entered.set()
        assert release.wait(10)
        try:
            return real_sync(records, client)
        finally:
            finished.set()

    monkeypatch.setattr(eds_module, "sync_to_eds", _blocking_sync)
    publisher = eds_module.EdsPublisher(
        eds_module.FakeEBookClient, lambda: [], enabled=True)
    publisher._flush()
    assert entered.wait(10), "flush must hand work to a worker thread"
    assert seen.get("thread") not in (None, main_thread)
    release.set()
    assert finished.wait(10)
