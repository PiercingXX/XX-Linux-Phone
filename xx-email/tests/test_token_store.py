"""Token store/cache tests.

Ports TokenCacheTest (JVM unit) and the headless-portable parts of
TokenStoreTest (androidTest): per-account round trip, targeted removal,
failed persist keeping the previous good content, and the
unreadable-vs-empty distinction. The Android KeyStore encryption
becomes the Secret Service backend, which has no bus here, so the
memory backend stands in and SecretTokenBackend is only smoke-checked
for its unavailable-service contract.
"""

import sys
import threading

import pytest

from xx_email.auth import ReauthNeeded
from xx_email.token_store import (
    AuthError,
    MemoryTokenBackend,
    SecretBackendError,
    SecretTokenBackend,
    TokenCache,
    TokenFailed,
    TokenReauthNeeded,
    TokenRecord,
    TokenSession,
    TokenStore,
    TokenStoreWorker,
    TokenSuccess,
)


def test_pure_modules_import_no_gi():
    assert "gi" not in sys.modules


class FakeSession(TokenSession):
    """Scripted refresh outcomes; a held token makes later calls cache-hot."""

    def __init__(self, snapshot, outcomes, rotate_on_success=True):
        self._snapshot = snapshot
        self._outcomes = list(outcomes)
        self._rotate = rotate_on_success
        self._held = None
        self.acquires = 0
        self.refresh_attempts = 0

    def snapshot(self):
        return self._snapshot

    def acquire(self):
        self.acquires += 1
        if self._held is not None:
            return TokenSuccess(self._held)
        self.refresh_attempts += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, TokenSuccess):
            self._held = outcome.access_token
            if self._rotate:
                self._snapshot += "+rotated"
        return outcome


class Harness:
    def __init__(self):
        self.sessions = []
        self.persisted = []
        self.loads = 0

        def load_session(email):
            self.loads += 1
            assert self.sessions, "no session queued"
            return self.sessions.pop(0)

        self.cache = TokenCache(load_session=load_session,
                                persist=self._persist)

    def _persist(self, email, snapshot):
        self.persisted.append((email, snapshot))


class BlockingSession(TokenSession):
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.started = threading.Event()
        self.release = threading.Event()
        self.acquires = 0

    def snapshot(self):
        return self._snapshot

    def acquire(self):
        self.acquires += 1
        self.started.set()
        assert self.release.wait(timeout=10)
        self._snapshot += "+rotated"
        return TokenSuccess("tok-stale")


def test_expired_token_refreshes_once_and_persists_rotation():
    harness = Harness()
    session = FakeSession("snap-1", [TokenSuccess("tok-1")])
    harness.sessions.append(session)
    assert harness.cache.with_access_token("a@x", lambda tok: tok) == "tok-1"
    assert session.acquires == 1
    assert session.refresh_attempts == 1
    assert harness.persisted == [("a@x", "snap-1+rotated")]


def test_second_call_hits_memory_without_second_refresh():
    harness = Harness()
    session = FakeSession("snap-1", [TokenSuccess("tok-1")])
    harness.sessions.append(session)
    harness.cache.with_access_token("a@x", lambda tok: tok)
    second = harness.cache.with_access_token("a@x", lambda tok: tok)
    assert harness.loads == 1
    assert session.acquires == 2
    assert session.refresh_attempts == 1
    assert len(harness.persisted) == 1
    assert second == "tok-1"


def test_unchanged_snapshot_is_not_persisted():
    harness = Harness()
    session = FakeSession("snap-1", [TokenSuccess("tok-1")],
                          rotate_on_success=False)
    harness.sessions.append(session)
    harness.cache.with_access_token("a@x", lambda tok: tok)
    assert session.refresh_attempts == 1
    assert harness.persisted == []


def test_reauth_throws_flags_account_and_evicts_session():
    harness = Harness()
    harness.sessions.append(
        FakeSession("snap-1", [TokenReauthNeeded("invalid_grant")]))
    with pytest.raises(ReauthNeeded):
        harness.cache.with_access_token("a@x", lambda tok: tok)
    assert harness.cache.reauth_needed == {"a@x"}
    assert harness.persisted == []
    harness.sessions.append(FakeSession("snap-2", [TokenSuccess("tok-2")]))
    assert harness.cache.with_access_token("a@x", lambda tok: tok) == "tok-2"
    assert "a@x" not in harness.cache.reauth_needed


def test_transient_failure_throws_without_reauth_flag():
    harness = Harness()
    harness.sessions.append(
        FakeSession("snap-1", [TokenFailed("network down")]))
    with pytest.raises(AuthError):
        harness.cache.with_access_token("a@x", lambda tok: tok)
    assert harness.cache.reauth_needed == set()
    assert harness.loads == 1


def test_refresh_after_clear_neither_persists_nor_recaches():
    harness = Harness()
    stale = BlockingSession("snap-stale")
    harness.sessions.append(stale)
    worker = threading.Thread(
        target=harness.cache.with_access_token,
        args=("a@x", lambda tok: tok))
    worker.start()
    assert stale.started.wait(timeout=10)
    harness.cache.clear("a@x")
    stale.release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert harness.persisted == []
    harness.sessions.append(
        FakeSession("snap-fresh", [TokenSuccess("tok-fresh")]))
    assert harness.cache.with_access_token("a@x", lambda tok: tok) == \
        "tok-fresh"
    assert harness.loads == 2
    assert harness.persisted == [("a@x", "snap-fresh+rotated")]


def test_missing_account_raises_without_flagging():
    cache = TokenCache(load_session=lambda email: None,
                       persist=lambda email, snapshot: None)
    with pytest.raises(AuthError, match="not authorized"):
        cache.with_access_token("ghost@x", lambda tok: tok)
    assert cache.reauth_needed == set()


def make_store():
    return TokenStore(MemoryTokenBackend())


def test_save_then_load_round_trips_per_account():
    store = make_store()
    store.save("alice@dev.xxemail",
               TokenRecord(refresh_token="rt-alice", client_id="cid",
                           updated_at=1700000000000))
    store.save("bob@dev.xxemail",
               TokenRecord(refresh_token="rt-bob", client_id="cid"))
    assert set(store.all_emails()) == {"alice@dev.xxemail",
                                       "bob@dev.xxemail"}
    loaded = store.load("alice@dev.xxemail")
    assert loaded is not None
    assert loaded.refresh_token == "rt-alice"
    assert loaded.client_id == "cid"
    assert loaded.updated_at == 1700000000000
    assert store.load("bob@dev.xxemail").refresh_token == "rt-bob"
    assert store.unreadable is False


def test_remove_deletes_only_targeted_account():
    store = make_store()
    store.save("alice@dev.xxemail",
               TokenRecord(refresh_token="rt-a", client_id="c"))
    store.save("bob@dev.xxemail",
               TokenRecord(refresh_token="rt-b", client_id="c"))
    store.remove("alice@dev.xxemail")
    assert store.all_emails() == ["bob@dev.xxemail"]
    assert store.load("alice@dev.xxemail") is None
    assert store.load("bob@dev.xxemail") is not None


def test_failed_persist_throws_and_keeps_previous_content():
    backend = MemoryTokenBackend()
    store = TokenStore(backend)
    store.save("alice@dev.xxemail",
               TokenRecord(refresh_token="rt-a", client_id="c"))
    good = dict(backend._items)
    backend.fail_on_save = IOError("store unwritable")
    with pytest.raises(IOError):
        store.save("bob@dev.xxemail",
                   TokenRecord(refresh_token="rt-b", client_id="c"))
    assert dict(backend._items) == good
    backend.fail_on_save = None
    assert store.load("alice@dev.xxemail").refresh_token == "rt-a"
    assert store.all_emails() == ["alice@dev.xxemail"]
    assert store.unreadable is False


def test_locked_store_flagged_never_mistaken_for_empty():
    backend = MemoryTokenBackend()
    store = TokenStore(backend)
    store.save("alice@dev.xxemail",
               TokenRecord(refresh_token="rt-a", client_id="c"))
    backend.lock()
    assert store.unreadable is True
    assert store.load("alice@dev.xxemail") is None
    assert store.all_emails() == []
    backend.unlock()
    assert store.unreadable is False
    assert store.load("alice@dev.xxemail") is not None


def test_corrupt_or_tokenless_payload_loads_as_none():
    store = make_store()
    store.save_serialized("alice@dev.xxemail", "{nope")
    store.save_serialized("bob@dev.xxemail", '{"client_id": "c"}')
    assert store.load("alice@dev.xxemail") is None
    assert store.load("bob@dev.xxemail") is None


def test_record_round_trips_through_json():
    record = TokenRecord(refresh_token="rt", client_id="cid",
                         updated_at=42, extra={"k": "v"})
    assert TokenRecord.from_json(record.to_json()) == record
    assert TokenRecord.from_json("{}") is None


def _install_secret_stub(monkeypatch, lookup=None, store_ok=True):
    """Inject a fake Secret module so no test can reach a real keyring.

    The previous version of this test called the real Secret Service
    when one existed, querying the owner's login keyring. The stub
    answers every libsecret call in-process; the session bus is never
    consulted (conftest also poisons DBUS_SESSION_BUS_ADDRESS).
    """
    import sys
    import types

    calls = []

    class SecretFailure(Exception):
        pass

    secret = types.ModuleType("Secret")
    secret.COLLECTION_DEFAULT = "default"

    class _Schema:
        @staticmethod
        def new(name, flags, attrs):
            calls.append(("schema", name))
            return ("schema", name)

    class _Flags:
        NONE = 0

    class _AttrType:
        STRING = "string"

    secret.Schema = _Schema
    secret.SchemaFlags = _Flags
    secret.SchemaAttributeType = _AttrType
    outcome = {"lookup": lookup, "store_ok": store_ok}

    def lookup_sync(schema, attrs, cancellable):
        calls.append(("lookup", dict(attrs)))
        if outcome["lookup"] is None:
            raise SecretFailure("cannot connect: no bus in tests")
        return outcome["lookup"]

    def store_sync(schema, attrs, collection, label, value,
                   cancellable):
        calls.append(("store", dict(attrs), label, value))
        return outcome["store_ok"]

    def clear_sync(schema, attrs, cancellable):
        calls.append(("clear", dict(attrs)))
        return True

    secret.password_lookup_sync = lookup_sync
    secret.password_store_sync = store_sync
    secret.password_clear_sync = clear_sync

    gi_stub = types.ModuleType("gi")
    gi_stub.require_version = lambda *args: None
    repository = types.ModuleType("gi.repository")
    repository.Secret = secret
    monkeypatch.setitem(sys.modules, "gi", gi_stub)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    return calls


def test_secret_backend_lookup_failure_reads_as_unreadable(monkeypatch):
    calls = _install_secret_stub(monkeypatch, lookup=None)
    backend = SecretTokenBackend()
    assert backend.load("alice@dev.xxemail") is None
    assert backend.is_unreadable() is True
    assert ("lookup", {"application": "io.piercingxx.XXEmail",
                       "account": "alice@dev.xxemail"}) in calls


def test_secret_backend_round_trip_never_touches_a_bus(monkeypatch):
    payload = TokenRecord(refresh_token="rt", client_id="cid").to_json()
    calls = _install_secret_stub(monkeypatch, lookup=payload)
    backend = SecretTokenBackend()
    backend.save("alice@dev.xxemail", payload)
    assert backend.load("alice@dev.xxemail") == payload
    assert backend.is_unreadable() is False
    backend.remove("alice@dev.xxemail")
    assert ("store", {"application": "io.piercingxx.XXEmail",
                      "account": "alice@dev.xxemail"},
            "XX Email (alice@dev.xxemail)", payload) in calls
    assert ("clear", {"application": "io.piercingxx.XXEmail",
                      "account": "alice@dev.xxemail"}) in calls


def test_secret_backend_missing_gi_is_an_error(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "gi", None)
    monkeypatch.setitem(sys.modules, "gi.repository", None)
    with pytest.raises(SecretBackendError):
        SecretTokenBackend().save("alice@dev.xxemail", "{}")


def test_token_store_worker_runs_backend_off_the_caller():
    import threading

    caller = threading.get_ident()
    idents = []

    class IdentBackend(MemoryTokenBackend):
        def load(self, account):
            idents.append(threading.get_ident())
            return super().load(account)

        def list_accounts(self):
            idents.append(threading.get_ident())
            return super().list_accounts()

    worker = TokenStoreWorker(TokenStore(IdentBackend()))
    try:
        worker.save("alice@dev.xxemail",
                    TokenRecord(refresh_token="rt", client_id="c"))
        assert worker.load("alice@dev.xxemail").refresh_token == "rt"
        assert worker.all_emails() == ["alice@dev.xxemail"]
        assert worker.unreadable is False
    finally:
        worker.close()
    assert idents, "backend was never consulted"
    assert all(ident != caller for ident in idents)
    assert len(set(idents)) == 1, "worker must use one thread"
