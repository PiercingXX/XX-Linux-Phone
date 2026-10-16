"""Token storage behind a backend interface, plus the refresh cache.

Ports TokenStore.kt / TokenCache.kt (ref/xx-email data/auth/): one
OAuth record per account. On Linux the backend is the Secret Service
via GI Secret (one item per account, attributes
{application: io.piercingxx.XXEmail, account: <email>}), storing the
refresh token + client id + refresh-time metadata as JSON - never the
access token alone, so every stored record stays refreshable. Atomicity
comes from the Secret Service itself. An unreadable/locked store is
flagged, never mistaken for "no accounts" (callers surface re-auth).

The in-memory backend is the test fake; production passes
SecretTokenBackend. TokenCache keeps the Android semantics: in-memory
sessions, per-account serialized refresh, change-detecting
persistence, generation-guarded clear, permanent failures surfaced as
ReauthNeeded for the sign-in-again banner.
"""

from __future__ import annotations

import abc
import concurrent.futures
import json
import threading
import time
from dataclasses import dataclass

from xx_email.auth import ReauthNeeded

APPLICATION_ATTR = "io.piercingxx.XXEmail"


@dataclass
class TokenRecord:
    refresh_token: str
    client_id: str
    updated_at: int = 0
    extra: dict | None = None

    def to_json(self) -> str:
        return json.dumps({
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "updated_at": self.updated_at,
            "extra": self.extra or {},
        }, sort_keys=True)

    @staticmethod
    def from_json(payload: str) -> TokenRecord | None:
        try:
            raw = json.loads(payload)
        except ValueError:
            return None
        if not isinstance(raw, dict) or not raw.get("refresh_token"):
            return None
        client_id = raw.get("client_id")
        return TokenRecord(
            refresh_token=str(raw["refresh_token"]),
            client_id=str(client_id) if isinstance(client_id, str) else "",
            updated_at=raw.get("updated_at", 0)
            if isinstance(raw.get("updated_at"), int) else 0,
            extra=raw.get("extra")
            if isinstance(raw.get("extra"), dict) else {},
        )


class TokenBackend(abc.ABC):
    """Credential persistence. load() returns the stored JSON or None."""

    @abc.abstractmethod
    def save(self, account: str, payload_json: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def load(self, account: str) -> str | None:
        raise NotImplementedError

    @abc.abstractmethod
    def remove(self, account: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def list_accounts(self) -> list[str]:
        raise NotImplementedError

    @abc.abstractmethod
    def is_unreadable(self) -> bool:
        """True when the store exists but cannot be read (locked/offline).

        Callers must check this before treating an empty account list as
        "no accounts".
        """
        raise NotImplementedError


class MemoryTokenBackend(TokenBackend):
    """In-memory fake for tests. lock() simulates a locked collection."""

    def __init__(self) -> None:
        self._items: dict[str, str] = {}
        self._lock = threading.Lock()
        self._locked = False
        self.fail_on_save: Exception | None = None

    def lock(self) -> None:
        with self._lock:
            self._locked = True

    def unlock(self) -> None:
        with self._lock:
            self._locked = False

    def save(self, account: str, payload_json: str) -> None:
        if self.fail_on_save is not None:
            raise self.fail_on_save
        with self._lock:
            self._items[account] = payload_json

    def load(self, account: str) -> str | None:
        with self._lock:
            if self._locked:
                return None
            return self._items.get(account)

    def remove(self, account: str) -> None:
        with self._lock:
            self._items.pop(account, None)

    def list_accounts(self) -> list[str]:
        with self._lock:
            if self._locked:
                return []
            return sorted(self._items)

    def is_unreadable(self) -> bool:
        with self._lock:
            return self._locked


class SecretTokenBackend(TokenBackend):
    """Secret Service backend via GI Secret (lazy import: no GI at import).

    Raises SecretBackendError when the Secret Service is unreachable, so a
    locked or missing collection reads as unreadable - never as empty.
    """

    def __init__(self, collection_alias: str = "default") -> None:
        self._collection_alias = collection_alias
        self._unreadable = False
        self._schema = None

    def _secret(self):
        try:
            import gi
            gi.require_version("Secret", "1")
            from gi.repository import Secret
        except (ImportError, ValueError) as exc:
            raise SecretBackendError(
                "Secret Service unavailable (no GI Secret)") from exc
        if self._schema is None:
            self._schema = Secret.Schema.new(
                APPLICATION_ATTR, Secret.SchemaFlags.NONE,
                {"application": Secret.SchemaAttributeType.STRING,
                 "account": Secret.SchemaAttributeType.STRING})
        return Secret

    def _attributes(self, account: str) -> dict:
        return {"application": APPLICATION_ATTR, "account": account}

    def save(self, account: str, payload_json: str) -> None:
        Secret = self._secret()
        ok = Secret.password_store_sync(
            self._schema, self._attributes(account),
            Secret.COLLECTION_DEFAULT, f"XX Email ({account})",
            payload_json, None)
        if not ok:
            raise SecretBackendError(f"Could not store token for {account}")
        self._unreadable = False

    def load(self, account: str) -> str | None:
        try:
            Secret = self._secret()
            value = Secret.password_lookup_sync(self._schema,
                                                self._attributes(account),
                                                None)
        except Exception:
            self._unreadable = True
            return None
        if value is None:
            return None
        self._unreadable = False
        return value

    def remove(self, account: str) -> None:
        Secret = self._secret()
        Secret.password_clear_sync(self._schema, self._attributes(account),
                                   None)

    def list_accounts(self) -> list[str]:
        return []

    def is_unreadable(self) -> bool:
        return self._unreadable


class SecretBackendError(Exception):
    pass


class TokenStore:
    """One TokenRecord per account over a TokenBackend (thread-safe)."""

    def __init__(self, backend: TokenBackend) -> None:
        self._backend = backend
        self._lock = threading.Lock()

    @property
    def unreadable(self) -> bool:
        return self._backend.is_unreadable()

    def save(self, account: str, record: TokenRecord) -> None:
        if not record.updated_at:
            record.updated_at = int(time.time() * 1000)
        with self._lock:
            self._backend.save(account, record.to_json())

    def save_serialized(self, account: str, payload_json: str) -> None:
        with self._lock:
            self._backend.save(account, payload_json)

    def load(self, account: str) -> TokenRecord | None:
        with self._lock:
            payload = self._backend.load(account)
        if payload is None:
            return None
        return TokenRecord.from_json(payload)

    def remove(self, account: str) -> None:
        with self._lock:
            self._backend.remove(account)

    def all_emails(self) -> list[str]:
        with self._lock:
            return self._backend.list_accounts()


@dataclass(frozen=True)
class TokenSuccess:
    access_token: str


@dataclass(frozen=True)
class TokenReauthNeeded:
    error: str | None = None


@dataclass(frozen=True)
class TokenFailed:
    reason: str


TokenAcquisition = TokenSuccess | TokenReauthNeeded | TokenFailed


class TokenSession(abc.ABC):
    """Minimal session surface the cache coordinates around.

    Production wraps the OAuth state + refresh call; tests substitute
    fakes, keeping this class free of network types. acquire() completes
    with exactly one TokenAcquisition (sync adaptation of the callback
    API in TokenCache.kt).
    """

    @abc.abstractmethod
    def snapshot(self) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def acquire(self) -> TokenAcquisition:
        raise NotImplementedError


class TokenCache:
    """In-memory per-account cache with change-detecting persistence.

    First use loads the session once via load_session; later calls hit
    memory. Acquisitions are serialized per account. A changed snapshot
    after refresh is persisted. Every clear() bumps a per-account
    generation so a stale in-flight refresh never overwrites fresh
    tokens. Permanent failures surface in reauth_needed.
    """

    def __init__(self, load_session, persist) -> None:
        self._load_session = load_session
        self._persist = persist
        self._guard = threading.Lock()
        self._sessions: dict[str, TokenSession] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._generations: dict[str, int] = {}
        self._reauth_needed: set[str] = set()

    @property
    def reauth_needed(self) -> set[str]:
        with self._guard:
            return set(self._reauth_needed)

    def clear(self, email: str) -> None:
        with self._guard:
            self._sessions.pop(email, None)
            self._generations[email] = self._generations.get(email, 0) + 1
            self._reauth_needed.discard(email)

    def with_access_token(self, email: str, block):
        acquisition, changed = self._acquire_fresh(email)
        if isinstance(acquisition, TokenSuccess):
            with self._guard:
                self._reauth_needed.discard(email)
            if changed is not None:
                self._persist(email, changed)
            return block(acquisition.access_token)
        if isinstance(acquisition, TokenReauthNeeded):
            raise ReauthNeeded(
                f"Session expired ({acquisition.error});"
                f" sign in again to reconnect {email}.")
        raise AuthError(acquisition.reason)

    def _lock_for(self, email: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(email)
            if lock is None:
                lock = threading.Lock()
                self._locks[email] = lock
            return lock

    def _acquire_fresh(self, email: str):
        lock = self._lock_for(email)
        with lock:
            with self._guard:
                generation = self._generations.get(email, 0)
                session = self._sessions.get(email)
            if session is None:
                session = self._load_session(email)
                if session is None:
                    raise AuthError(f"Account not authorized: {email}")
                with self._guard:
                    self._sessions[email] = session
            before = session.snapshot()
            acquisition = session.acquire()
            after = session.snapshot()
            with self._guard:
                if self._generations.get(email, 0) != generation:
                    self._sessions.pop(email, None)
                    return acquisition, None
                if isinstance(acquisition, TokenReauthNeeded):
                    self._sessions.pop(email, None)
                    self._reauth_needed.add(email)
            return acquisition, after if after != before else None


class AuthError(Exception):
    """Transient token failure (network, unknown): retry later, no re-auth."""


class TokenStoreWorker:
    """Runs a synchronous TokenStore on a dedicated worker thread.

    libsecret ``*_sync`` calls can block on a keyring unlock prompt, so
    they must never run on the GTK main loop. TokenStore stays
    synchronous by design; the sync engine and UI lanes reach it only
    through this wrapper, which hops every backend call onto one
    single-thread executor and blocks the caller for the result.
    """

    def __init__(self, store: TokenStore,
                 executor: concurrent.futures.Executor | None = None,
                 ) -> None:
        self._store = store
        self._owned = executor is None
        self._executor = executor or concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="xxemail-token")

    def _run(self, func, *args):
        return self._executor.submit(func, *args).result()

    @property
    def unreadable(self) -> bool:
        return self._run(self._store._backend.is_unreadable)

    def save(self, account: str, record: TokenRecord) -> None:
        self._run(self._store.save, account, record)

    def save_serialized(self, account: str, payload_json: str) -> None:
        self._run(self._store.save_serialized, account, payload_json)

    def load(self, account: str) -> TokenRecord | None:
        return self._run(self._store.load, account)

    def remove(self, account: str) -> None:
        self._run(self._store.remove, account)

    def all_emails(self) -> list[str]:
        return self._run(self._store.all_emails)

    def close(self) -> None:
        if self._owned:
            self._executor.shutdown(wait=True)
