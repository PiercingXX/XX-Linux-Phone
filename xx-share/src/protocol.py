"""LocalSend v2.2 message shapes: encode/decode with strict validation.

Pure stdlib, no I/O, no GTK: safe to import anywhere, including headless
tests. See PORT_SPEC.md for the wire semantics.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

MULTICAST_GROUP = "224.0.0.167"
MULTICAST_PORT = 53317
DEFAULT_PORT = 53317
PROTOCOL_VERSION = "2.2"
SUPPORTED_MAJOR = "2"
API_BASE = "/api/localsend/v2"

PATH_INFO = API_BASE + "/info"
PATH_REGISTER = API_BASE + "/register"
PATH_PREPARE_UPLOAD = API_BASE + "/prepare-upload"
PATH_UPLOAD = API_BASE + "/upload"
PATH_CANCEL = API_BASE + "/cancel"

DEVICE_TYPES = ("mobile", "desktop", "web", "headless", "server")


@dataclass(frozen=True)
class DeviceInfo:
    alias: str
    fingerprint: str
    port: int = DEFAULT_PORT
    protocol: str = "https"
    download: bool = False
    version: str = PROTOCOL_VERSION
    device_model: str | None = None
    device_type: str | None = None

    def to_dict(self) -> dict:
        return {
            "alias": self.alias,
            "version": self.version,
            "deviceModel": self.device_model,
            "deviceType": self.device_type,
            "fingerprint": self.fingerprint,
            "port": self.port,
            "protocol": self.protocol,
            "download": self.download,
        }

    def to_register_dict(self) -> dict:
        return {
            "alias": self.alias,
            "version": self.version,
            "deviceModel": self.device_model,
            "deviceType": self.device_type,
            "fingerprint": self.fingerprint,
            "download": self.download,
        }

    @staticmethod
    def from_dict(raw: object) -> "DeviceInfo":
        if not isinstance(raw, dict):
            raise ValueError("device info must be a JSON object")
        alias = raw.get("alias")
        fingerprint = raw.get("fingerprint")
        if not isinstance(alias, str) or not alias:
            raise ValueError("device info needs a non-empty alias")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError("device info needs a fingerprint")
        port = raw.get("port", DEFAULT_PORT)
        if isinstance(port, bool) or not isinstance(port, int):
            raise ValueError("port must be a number")
        if not 1 <= port <= 65535:
            raise ValueError("port out of range")
        protocol = raw.get("protocol", "https")
        if protocol not in ("https", "http"):
            raise ValueError("protocol must be http or https")
        device_type = raw.get("deviceType")
        if device_type is not None and device_type not in DEVICE_TYPES:
            raise ValueError("unknown deviceType")
        model = raw.get("deviceModel")
        if model is not None and not isinstance(model, str):
            raise ValueError("deviceModel must be a string or null")
        version = raw.get("version", PROTOCOL_VERSION)
        if not isinstance(version, str) or not version:
            raise ValueError("version must be a non-empty string")
        return DeviceInfo(
            alias=alias,
            fingerprint=fingerprint,
            port=port,
            protocol=protocol,
            download=bool(raw.get("download", False)),
            version=version,
            device_model=model,
            device_type=device_type,
        )


def encode_announce(info: DeviceInfo, announce: bool) -> bytes:
    payload = info.to_dict()
    payload["announce"] = bool(announce)
    return json.dumps(payload).encode("utf-8")


def decode_announce(data: bytes) -> tuple[DeviceInfo, bool]:
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("not a JSON announce message") from error
    if not isinstance(raw, dict):
        raise ValueError("announce message must be a JSON object")
    info = DeviceInfo.from_dict(raw)
    return info, bool(raw.get("announce", False))


def is_supported_version(version: object) -> bool:
    if not isinstance(version, str) or not version:
        return False
    return version.split(".")[0] == SUPPORTED_MAJOR


def fingerprint_of_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


@dataclass(frozen=True)
class UploadFile:
    id: str
    file_name: str
    size: int
    file_type: str
    sha256: str | None = None
    preview: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "fileName": self.file_name,
            "size": self.size,
            "fileType": self.file_type,
            "sha256": self.sha256,
            "preview": self.preview,
        }

    @staticmethod
    def from_dict(raw: object) -> "UploadFile":
        if not isinstance(raw, dict):
            raise ValueError("file entry must be an object")
        file_id = raw.get("id")
        name = raw.get("fileName")
        size = raw.get("size")
        file_type = raw.get("fileType")
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("file entry needs an id")
        if not isinstance(name, str) or not name:
            raise ValueError("file entry needs a fileName")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("file entry needs a non-negative size")
        if not isinstance(file_type, str) or not file_type:
            raise ValueError("file entry needs a fileType")
        sha256 = raw.get("sha256")
        if sha256 is not None and not isinstance(sha256, str):
            raise ValueError("sha256 must be a string or null")
        preview = raw.get("preview")
        if preview is not None and not isinstance(preview, str):
            raise ValueError("preview must be a string or null")
        return UploadFile(
            id=file_id,
            file_name=name,
            size=size,
            file_type=file_type,
            sha256=sha256,
            preview=preview,
        )


def build_prepare_upload(sender: DeviceInfo, files: list[UploadFile]) -> dict:
    return {
        "info": sender.to_dict(),
        "files": {item.id: item.to_dict() for item in files},
    }


def prepare_upload_path(pin: str | None = None) -> str:
    if pin:
        from urllib.parse import quote

        return f"{PATH_PREPARE_UPLOAD}?pin={quote(pin)}"
    return PATH_PREPARE_UPLOAD


def parse_prepare_upload(raw: object) -> tuple[DeviceInfo, list[UploadFile]]:
    if not isinstance(raw, dict):
        raise ValueError("prepare-upload body must be an object")
    sender = DeviceInfo.from_dict(raw.get("info"))
    files_raw = raw.get("files")
    if not isinstance(files_raw, dict) or not files_raw:
        raise ValueError("prepare-upload needs a non-empty files map")
    return sender, [UploadFile.from_dict(entry) for entry in files_raw.values()]


def build_prepare_response(session_id: str, tokens: dict[str, str]) -> dict:
    return {"sessionId": session_id, "files": dict(tokens)}


def parse_prepare_response(raw: object) -> tuple[str, dict[str, str]]:
    if not isinstance(raw, dict):
        raise ValueError("prepare-upload response must be an object")
    session_id = raw.get("sessionId")
    files = raw.get("files")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("response needs a sessionId")
    if not isinstance(files, dict) or not files:
        raise ValueError("response needs a non-empty files map")
    for key, token in files.items():
        if not isinstance(token, str) or not token:
            raise ValueError(f"token for {key!r} must be a string")
    return session_id, dict(files)
