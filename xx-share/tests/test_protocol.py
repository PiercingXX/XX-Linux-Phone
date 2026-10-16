import json

import pytest

from protocol import (
    DeviceInfo,
    UploadFile,
    build_prepare_response,
    build_prepare_upload,
    decode_announce,
    encode_announce,
    fingerprint_of_der,
    parse_prepare_response,
    parse_prepare_upload,
    prepare_upload_path,
)


def _info(**overrides):
    base = {"alias": "phone", "fingerprint": "ab" * 32}
    base.update(overrides)
    return DeviceInfo.from_dict(base)


def test_announce_roundtrip():
    info = _info()
    for flag in (True, False):
        raw = encode_announce(info, flag)
        back, announce = decode_announce(raw)
        assert back == info
        assert announce is flag


def test_announce_rejects_garbage():
    for bad in (b"not json", b"[1,2]", b'{"alias": "x"}',
                b'{"alias": "", "fingerprint": "ab"}',
                b'{"alias": "x", "fingerprint": "ab", "port": 99999}'):
        with pytest.raises(ValueError):
            decode_announce(bad)


def test_device_info_defaults_and_validation():
    info = _info()
    assert info.port == 53317
    assert info.protocol == "https"
    assert info.version == "2.2"
    with pytest.raises(ValueError):
        DeviceInfo.from_dict({"alias": "x", "fingerprint": "f",
                              "deviceType": "toaster"})
    with pytest.raises(ValueError):
        DeviceInfo.from_dict({"alias": "x", "fingerprint": "f",
                              "protocol": "ftp"})
    wire = info.to_dict()
    assert json.loads(json.dumps(wire))["alias"] == "phone"


def test_prepare_upload_body_carries_no_pin():
    sender = _info(alias="sender")
    files = [UploadFile(id="a", file_name="x.jpg", size=10,
                        file_type="image/jpeg")]
    body = build_prepare_upload(sender, files)
    assert "pin" not in body
    assert set(body) == {"info", "files"}
    back_sender, back_files = parse_prepare_upload(body)
    assert back_sender == sender
    assert back_files == files


def test_prepare_upload_pin_goes_in_query():
    assert prepare_upload_path() == "/api/localsend/v2/prepare-upload"
    assert prepare_upload_path("123456") == (
        "/api/localsend/v2/prepare-upload?pin=123456")


def test_register_body_has_no_port_or_protocol():
    info = _info()
    assert set(info.to_register_dict()) == {
        "alias", "version", "deviceModel", "deviceType",
        "fingerprint", "download",
    }
    assert "port" not in info.to_register_dict()
    assert "protocol" not in info.to_register_dict()


def test_prepare_upload_rejects_empty_files():
    sender = _info()
    with pytest.raises(ValueError):
        parse_prepare_upload({"info": sender.to_dict(), "files": {}})


def test_prepare_response_roundtrip():
    session, tokens = parse_prepare_response(
        build_prepare_response("sid", {"a": "tok"}))
    assert (session, tokens) == ("sid", {"a": "tok"})
    with pytest.raises(ValueError):
        parse_prepare_response({"sessionId": "", "files": {"a": "t"}})


def test_fingerprint_is_sha256_hex():
    assert fingerprint_of_der(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


def test_advertises_2_2_and_accepts_2x():
    from protocol import (
        PROTOCOL_VERSION,
        DeviceInfo,
        decode_announce,
        encode_announce,
        is_supported_version,
    )

    assert PROTOCOL_VERSION == "2.2"
    assert is_supported_version("2.1") is True
    assert is_supported_version("2.2") is True
    assert is_supported_version("1.0") is False
    for version in ("2.1", "2.2"):
        info = DeviceInfo(alias="p", fingerprint="ab" * 32, version=version)
        back, _ = decode_announce(encode_announce(info, True))
        assert back.version == version
        assert is_supported_version(back.version) is True
