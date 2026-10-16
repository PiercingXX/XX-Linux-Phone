"""Phone key storage seam: libsecret on the device, memory fake in tests.

The device pairing token and the owner verify key live in the keyring,
never in ``agent.conf``. Production uses :class:`LibsecretKeystore`
(SecretStorage over D-Bus, imported lazily so module import never touches
the bus); tests inject :class:`MemoryKeystore`. ``SecretRef`` in
``config.py`` is the pointer: ``account`` names the item, ``store`` holds
the backend.
"""

from __future__ import annotations

ACCOUNT_DEVICE_TOKEN = "xx-find/device-token"
ACCOUNT_OWNER_VERIFY_KEY = "xx-find/owner-verify-key"


class KeystoreError(Exception):
    pass


class Keystore:
    """Backend interface. ``get`` returns bytes or None; ``put`` stores."""

    def get(self, account: str) -> bytes | None:
        raise NotImplementedError

    def put(self, account: str, secret: bytes) -> None:
        raise NotImplementedError

    def delete(self, account: str) -> None:
        raise NotImplementedError

    def has(self, account: str) -> bool:
        return self.get(account) is not None


class MemoryKeystore(Keystore):
    """Dict-backed fake with the same method shapes as the real backend."""

    def __init__(self) -> None:
        self._items: dict[str, bytes] = {}

    def get(self, account: str) -> bytes | None:
        value = self._items.get(account)
        return bytes(value) if value is not None else None

    def put(self, account: str, secret: bytes) -> None:
        if not account or not isinstance(account, str):
            raise KeystoreError("bad account")
        self._items[account] = bytes(secret)

    def delete(self, account: str) -> None:
        self._items.pop(account, None)


class LibsecretKeystore(Keystore):
    """libsecret backend via SecretStorage (D-Bus).

    Nothing here runs at import or construction time: the SecretStorage
    import and the bus connection happen inside each method, so unit
    tests and disabled installs never touch the keyring.
    """

    SERVICE = "xx-find"
    COLLECTION = "default"

    def _collection(self):
        try:
            import secretstorage as _secretstorage
        except ImportError as exc:
            raise KeystoreError(
                "SecretStorage is required on the phone") from exc
        try:
            bus = _secretstorage.dbus_init()
            return _secretstorage.get_default_collection(bus)
        except Exception as exc:
            raise KeystoreError("keyring unavailable") from exc

    @staticmethod
    def _attributes(account: str) -> dict:
        return {"service": LibsecretKeystore.SERVICE,
                "account": account}

    def get(self, account: str) -> bytes | None:
        collection = self._collection()
        try:
            items = list(collection.search_items(
                self._attributes(account)))
        except Exception as exc:
            raise KeystoreError("keyring read failed") from exc
        for item in items:
            try:
                return bytes(item.get_secret())
            except Exception as exc:
                raise KeystoreError("keyring read failed") from exc
        return None

    def put(self, account: str, secret: bytes) -> None:
        if not account or not isinstance(account, str):
            raise KeystoreError("bad account")
        collection = self._collection()
        try:
            collection.create_item(
                "xx-find/%s" % account, self._attributes(account),
                bytes(secret), replace=True)
        except Exception as exc:
            raise KeystoreError("keyring write failed") from exc

    def delete(self, account: str) -> None:
        collection = self._collection()
        try:
            items = list(collection.search_items(
                self._attributes(account)))
        except Exception as exc:
            raise KeystoreError("keyring read failed") from exc
        for item in items:
            try:
                item.delete()
            except Exception as exc:
                raise KeystoreError("keyring delete failed") from exc


def put_secret(ref, secret: bytes) -> None:
    """Store through a ``SecretRef`` pointer (``ref.store`` is a Keystore)."""
    if ref is None or not getattr(ref, "account", ""):
        raise KeystoreError("bad secret reference")
    store = getattr(ref, "store", None)
    if not isinstance(store, Keystore):
        raise KeystoreError("no keystore backend")
    store.put(ref.account, bytes(secret))
    try:
        ref.present = True
    except AttributeError:
        pass


def get_secret(ref) -> bytes | None:
    """Read through a ``SecretRef`` pointer; None when absent."""
    if ref is None or not getattr(ref, "account", ""):
        return None
    store = getattr(ref, "store", None)
    if not isinstance(store, Keystore):
        return None
    return store.get(ref.account)
