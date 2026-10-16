"""Crypto known-vector and round-trip tests (headless, no I/O)."""

from __future__ import annotations

import pytest

from xx_find import crypto

# RFC 8032 section 7.1, TEST 1 (empty message), copied from the RFC text.
RFC8032_SEED = ("9d61b19deffd5a60ba844af492ec2cc4"
                "4449c5697b326919703bac031cae7f60")
RFC8032_PUB = ("d75a980182b10ab7d54bfed3c964073a"
               "0ee172f3daa62325af021a68f707511a")
RFC8032_SIG = ("e5564300c360ac729086e2cc806e828a"
               "84877f1eb8e5d974d873e06522490155"
               "5fb8821590a33bacc61e39701cf9b46b"
               "d25bf5f0595bbe24655141438e7a100b")


def test_ed25519_rfc8032_vector():
    crypto.verify_command(b"", bytes.fromhex(RFC8032_SIG), RFC8032_PUB)
    recovered = crypto.sign_command(b"", RFC8032_SEED)
    assert recovered.hex() == RFC8032_SIG


def test_ed25519_rfc8032_cross_checked_with_cryptography():
    pytest.importorskip("cryptography.hazmat.primitives")
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey)
    Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(RFC8032_PUB)).verify(
            bytes.fromhex(RFC8032_SIG), b"")
    assert Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(RFC8032_SEED)).sign(b"").hex() == RFC8032_SIG
    with pytest.raises(InvalidSignature):
        Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(RFC8032_PUB)).verify(
                bytes.fromhex(RFC8032_SIG), b"tampered")


def test_ed25519_tamper_rejected():
    with pytest.raises(crypto.CryptoError):
        crypto.verify_command(b"tampered", bytes.fromhex(RFC8032_SIG),
                              RFC8032_PUB)


def test_sealed_box_round_trip_and_overhead():
    pub, secret = crypto.generate_owner_box_keypair()
    plaintext = b'{"lat": 1.0}'
    box = crypto.seal_report(plaintext, pub)
    assert len(box) == len(plaintext) + crypto.SEAL_OVERHEAD
    assert crypto.unseal_report(box, secret) == plaintext


def test_sealed_box_wrong_key_rejected():
    pub, _ = crypto.generate_owner_box_keypair()
    _, other_secret = crypto.generate_owner_box_keypair()
    box = crypto.seal_report(b"secret", pub)
    with pytest.raises(crypto.CryptoError):
        crypto.unseal_report(box, other_secret)


def test_sealed_box_tamper_rejected():
    pub, secret = crypto.generate_owner_box_keypair()
    box = bytearray(crypto.seal_report(b"secret", pub))
    box[-1] ^= 1
    with pytest.raises(crypto.CryptoError):
        crypto.unseal_report(bytes(box), secret)


def test_bad_keys_rejected():
    with pytest.raises(crypto.CryptoError):
        crypto.seal_report(b"x", "zz")
    with pytest.raises(crypto.CryptoError):
        crypto.verify_command(b"x", b"\x00" * 64, "zz")
