"""Crypto interface: sealed boxes for reports, signatures for commands.

Backend is PyNaCl (libsodium): distro-packaged as `python3-nacl`
(Debian/Mobian), `python-nacl` (Arch), `py3-nacl` (Alpine/postmarketOS),
so no pip is needed on the phone. `cryptography` stays a fallback, not a
second dependency. All inputs/outputs are bytes and hex strings; no I/O,
no keyring access here (keys live in libsecret, see PORT_SPEC.md).
"""

from __future__ import annotations

SEAL_OVERHEAD = 48
ED25519_KEY_BYTES = 32
ED25519_SIG_BYTES = 64


class CryptoError(Exception):
    pass


def _require_nacl():
    try:
        from nacl import public as _public
        from nacl import signing as _signing
        from nacl.exceptions import BadSignatureError as _BadSig
    except ImportError as exc:
        raise CryptoError(
            "PyNaCl is required (Debian/Mobian: python3-nacl, "
            "Arch: python-nacl, Alpine/pmOS: py3-nacl)") from exc
    return _public, _signing, _BadSig


def generate_owner_signing_keypair() -> tuple[str, str]:
    """New Ed25519 pair for signing commands. Returns
    (verify_key_hex, signing_key_hex); the signing key stays on the
    owner's other device(s), the verify key is shipped to the phone."""
    _, signing, _ = _require_nacl()
    seed = signing.SigningKey.generate()
    return (seed.verify_key.encode().hex(), seed.encode().hex())


def generate_owner_box_keypair() -> tuple[str, str]:
    """New X25519 pair for sealed location reports. Returns
    (public_key_hex, secret_key_hex); the secret never leaves the owner."""
    public, _, _ = _require_nacl()
    secret = public.PrivateKey.generate()
    return (secret.public_key.encode().hex(), secret.encode().hex())


def seal_report(plaintext: bytes, owner_public_key_hex: str) -> bytes:
    """Encrypt a location payload to the owner. Anonymous sender: anyone
    can produce a box, only the owner can open it."""
    public, _, _ = _require_nacl()
    try:
        key = public.PublicKey(bytes.fromhex(owner_public_key_hex))
    except ValueError as exc:
        raise CryptoError("bad owner public key") from exc
    return public.SealedBox(key).encrypt(bytes(plaintext))


def unseal_report(box: bytes, owner_secret_key_hex: str) -> bytes:
    """Owner-side decrypt. Raises CryptoError on tamper or wrong key."""
    public, _, _ = _require_nacl()
    try:
        key = public.PrivateKey(bytes.fromhex(owner_secret_key_hex))
        return public.SealedBox(key).decrypt(bytes(box))
    except ValueError as exc:
        raise CryptoError("bad owner secret key") from exc
    except Exception as exc:
        raise CryptoError("sealed box failed to open") from exc


def sign_command(canonical: bytes, signing_key_hex: str) -> bytes:
    public, signing, _ = _require_nacl()
    del public
    try:
        key = signing.SigningKey(bytes.fromhex(signing_key_hex))
    except ValueError as exc:
        raise CryptoError("bad signing key") from exc
    return key.sign(bytes(canonical)).signature


def verify_command(canonical: bytes, signature: bytes,
                   verify_key_hex: str) -> None:
    """Raises CryptoError when the signature is invalid."""
    _, signing, _BadSig = _require_nacl()
    try:
        key = signing.VerifyKey(bytes.fromhex(verify_key_hex))
    except ValueError as exc:
        raise CryptoError("bad verify key") from exc
    try:
        key.verify(bytes(canonical), bytes(signature))
    except (_BadSig, ValueError, TypeError) as exc:
        raise CryptoError("bad command signature") from exc
