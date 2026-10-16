"""Pairing QR/OTC payload + keystore seam. No bus, keyring or network."""

from __future__ import annotations

import sys

import pytest

from xx_find import config, crypto, keystore, pairing


def _bundle(token="tok-abc-12345"):
    verify, _ = crypto.generate_owner_signing_keypair()
    return ("phone-1", "http://100.64.0.1:8080", verify, token)


def test_pairing_round_trip():
    device, url, verify, token = _bundle()
    text = pairing.build_pairing_payload(device, url, verify, token)
    assert pairing.parse_pairing_payload(text) == {
        "v": 1, "device_id": device, "server_url": url,
        "verify_key": verify, "token": token}


def test_pairing_rejects_tampering():
    device, url, verify, token = _bundle()
    text = pairing.build_pairing_payload(device, url, verify, token)
    with pytest.raises(pairing.PairingError):
        pairing.parse_pairing_payload(text + "x")
    with pytest.raises(pairing.PairingError):
        pairing.parse_pairing_payload("not json")
    bad_url = text.replace("http://", "ftp://")
    with pytest.raises(pairing.PairingError):
        pairing.parse_pairing_payload(bad_url)
    bad_key = text.replace(verify[:8], "zzzzzzzz")
    with pytest.raises(pairing.PairingError):
        pairing.parse_pairing_payload(bad_key)
    with pytest.raises(pairing.PairingError):
        pairing.build_pairing_payload("bad id!", url, verify, token)
    with pytest.raises(pairing.PairingError):
        pairing.build_pairing_payload(device, "gopher://x", verify,
                                      token)
    with pytest.raises(pairing.PairingError):
        pairing.build_pairing_payload(device, url, "zz", token)
    with pytest.raises(pairing.PairingError):
        pairing.build_pairing_payload(device, url, verify, "  ")


def test_short_code_confirms_same_token():
    _, _, _, token = _bundle()
    code = pairing.short_code(token)
    assert len(code) == 6 and code.isdigit()
    assert pairing.verify_short_code(token, code) is True
    wrong = "000000" if code != "000000" else "111111"
    assert pairing.verify_short_code(token, wrong) is False
    assert pairing.verify_short_code("other-token-12345", code) is False


def test_memory_keystore_fake_round_trip():
    store = keystore.MemoryKeystore()
    assert store.get(keystore.ACCOUNT_DEVICE_TOKEN) is None
    assert store.has(keystore.ACCOUNT_DEVICE_TOKEN) is False
    store.put(keystore.ACCOUNT_DEVICE_TOKEN, b"tok-123")
    assert store.get(keystore.ACCOUNT_DEVICE_TOKEN) == b"tok-123"
    assert store.has(keystore.ACCOUNT_DEVICE_TOKEN) is True
    store.delete(keystore.ACCOUNT_DEVICE_TOKEN)
    assert store.get(keystore.ACCOUNT_DEVICE_TOKEN) is None
    with pytest.raises(keystore.KeystoreError):
        store.put("", b"x")


def test_secret_ref_helpers_use_injected_store():
    store = keystore.MemoryKeystore()
    ref = config.SecretRef(account=keystore.ACCOUNT_DEVICE_TOKEN,
                           store=store)
    assert keystore.get_secret(ref) is None
    keystore.put_secret(ref, b"tok-123")
    assert ref.present is True
    assert keystore.get_secret(ref) == b"tok-123"
    assert keystore.get_secret(
        config.SecretRef(account="x", store=None)) is None
    with pytest.raises(keystore.KeystoreError):
        keystore.put_secret(config.SecretRef(account="", store=store),
                            b"x")


def test_libsecret_backend_is_lazy_and_never_touched():
    import pathlib
    src = pathlib.Path(
        __file__).resolve().parent.parent.joinpath(
            "src", "xx_find", "keystore.py").read_text(encoding="utf-8")
    top = [line for line in src.splitlines()
           if line.startswith("import ") or line.startswith("from ")]
    assert not any("secretstorage" in line or line.startswith("import gi")
                   for line in top)
    backend = keystore.LibsecretKeystore()
    assert "secretstorage" not in sys.modules
    del backend
