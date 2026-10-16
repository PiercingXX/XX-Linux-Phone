"""Protocol validation: signatures, counters, expiry (no clock but `now`)."""

from __future__ import annotations

import pytest

from xx_find import crypto, protocol

NOW = 1_800_000_000


@pytest.fixture
def keys():
    verify_hex, signing_hex = crypto.generate_owner_signing_keypair()
    return verify_hex, signing_hex


def _signed(keys, device="phone-1", counter=1, exp=NOW + 120,
            cmd_type="locate", args=None):
    verify_hex, signing_hex = keys
    cmd = protocol.build_command(device, counter, exp, cmd_type, args)
    envelope = protocol.sign_envelope(cmd, signing_hex)
    return verify_hex, envelope, cmd


def test_valid_command_accepted(keys):
    verify_hex, envelope, cmd = _signed(keys)
    assert protocol.validate_command(envelope, verify_hex, 0, NOW) == cmd


def test_replay_rejected(keys):
    verify_hex, envelope, _ = _signed(keys, counter=5)
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command(envelope, verify_hex, 5, NOW)
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command(envelope, verify_hex, 9, NOW)


def test_tampered_command_rejected(keys):
    verify_hex, envelope, _ = _signed(keys)
    envelope["cmd"]["counter"] = 2
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command(envelope, verify_hex, 0, NOW)


def test_wrong_key_rejected(keys):
    other_verify, _ = crypto.generate_owner_signing_keypair()
    _, envelope, _ = _signed(keys)
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command(envelope, other_verify, 0, NOW)


def test_expired_and_far_future_rejected(keys):
    verify_hex, old, _ = _signed(keys, counter=1, exp=NOW - 3600)
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command(old, verify_hex, 0, NOW)
    _, future, _ = _signed(keys, counter=2, exp=NOW + 3600)
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command(future, verify_hex, 0, NOW)


def test_unknown_type_rejected():
    with pytest.raises(protocol.ProtocolError):
        protocol.build_command("phone-1", 1, NOW + 60, "self-destruct")


def test_message_args_preserved(keys):
    verify_hex, envelope, _ = _signed(
        keys, cmd_type="message",
        args={"text": "Call me", "callback": "+123"})
    cmd = protocol.validate_command(envelope, verify_hex, 0, NOW)
    assert cmd["args"]["callback"] == "+123"


def test_location_plaintext_round_trip():
    raw = protocol.encode_location_plaintext(47.37, 8.54, 12.5, NOW, 42,
                                             "geoclue")
    assert protocol.decode_location_plaintext(raw)["battery_pct"] == 42
    with pytest.raises(protocol.ProtocolError):
        protocol.encode_location_plaintext(91.0, 0.0, 1.0, NOW, None,
                                           "geoclue")
    with pytest.raises(protocol.ProtocolError):
        protocol.encode_location_plaintext(0.0, 0.0, 1.0, NOW, None,
                                           "wifi")


def test_args_reject_floats_at_build(keys):
    del keys
    cases = [{"v": 1.5}, {"v": float("nan")}, {"v": float("inf")},
             {"v": None}, {"nested": 1.5}, {"v": [1.5]}, {"a": None}]
    for args in cases:
        with pytest.raises(protocol.ProtocolError):
            protocol.build_command("phone-1", 1, NOW + 60, "lock", args)
    with pytest.raises(protocol.ProtocolError):
        protocol.build_command("phone-1", 1.5, NOW + 60, "lock")
    with pytest.raises(protocol.ProtocolError):
        protocol.build_command("phone-1", True, NOW + 60, "lock")


def test_args_reject_floats_at_validate(keys):
    verify_hex, signing_hex = keys
    cmd = protocol.build_command("phone-1", 1, NOW + 120, "lock")
    cmd["args"] = {"v": 1.5}
    envelope = protocol.sign_envelope(cmd, signing_hex)
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command(envelope, verify_hex, 0, NOW)


def test_float_counter_rejected_at_validate(keys):
    verify_hex, signing_hex = keys
    cmd = {"v": 1, "device_id": "phone-1", "counter": 1.5,
           "exp": NOW + 120, "type": "lock", "args": {}}
    envelope = protocol.sign_envelope(cmd, signing_hex)
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_command(envelope, verify_hex, 0, NOW)


def test_args_allow_int_bool_str_and_nesting(keys):
    verify_hex, _ = keys
    cmd = protocol.build_command(
        "phone-1", 1, NOW + 120, "lock",
        {"reason": "test", "retries": 3, "urgent": True,
         "deep": {"n": 1}, "items": [1, "two", False]})
    assert protocol.validate_command(
        {"cmd": cmd, "sig": protocol.sign_envelope(
            cmd, keys[1])["sig"]}, verify_hex, 0, NOW) == cmd


def test_canonical_rejects_nan():
    with pytest.raises(protocol.ProtocolError):
        protocol.canonical({"x": float("nan")})


RFC_SEED = ("9d61b19deffd5a60ba844af492ec2cc4"
            "4449c5697b326919703bac031cae7f60")
RFC_PUB = ("d75a980182b10ab7d54bfed3c964073a"
           "0ee172f3daa62325af021a68f707511a")

CANONICAL_VECTORS = [
    (b'{"args":{"callback":"+123","text":"Call me"},"counter":7,'
     b'"device_id":"phone-1","exp":1800000120,"type":"message","v":1}',
     "9d1455ae67e04451d9ce3ccf7dc7e879781c83199dcde3d9c360714f3d524f9"
     "8f9ec7a8c1ba2338b62d8e88ac56344864d71ee66bcd997437c51441f8ea4950a"),
    (b'{"args":{"reason":"test","retries":3,"urgent":true},"counter":8,'
     b'"device_id":"phone-1","exp":1800000120,"type":"lock","v":1}',
     "da05e0b7550364e8f8c783b7288b3045704fc0c82bc745c28ba8e5a8437345"
     "86273f6e17849fa5d4da68789f3712f4b2fb8b07add331ccc3cc38b349ba89f503"),
    (b'{"args":{"text":"H\\u00e9llo \\u2713"},"counter":9,'
     b'"device_id":"phone-1","exp":1800000120,"type":"message","v":1}',
     "5cdfd103e0a71bca7e09e78dec0dd78ce6b72d495d9173e0ee37e8cd7040734"
     "763db349f2d8cd43b14f008644326d5bd8a0093addaf1ca37380db6e090a5340c"),
]


def test_canonical_vectors_pin_bytes_and_signature():
    for canonical_bytes, sig_hex in CANONICAL_VECTORS:
        assert crypto.sign_command(canonical_bytes, RFC_SEED).hex() \
            == sig_hex
        crypto.verify_command(canonical_bytes, bytes.fromhex(sig_hex),
                              RFC_PUB)


def test_report_envelope_validated(keys):
    pub, secret = crypto.generate_owner_box_keypair()
    del secret
    sealed = crypto.seal_report(b'{"lat": 1.0}', pub)
    envelope = protocol.build_report_envelope("phone-1", 3, NOW, sealed)
    assert protocol.validate_report(envelope, 2, NOW) == envelope
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_report(envelope, 3, NOW)
    short = dict(envelope, box="AA==")
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_report(short, 0, NOW)
