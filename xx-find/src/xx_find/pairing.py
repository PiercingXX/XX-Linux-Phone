"""Pairing bundle: one-time QR payload plus a short numeric check code.

The owner's trusted device calls ``Store.pair_device`` (which returns the
token once and keeps only its hash), renders :func:`build_pairing_payload`
as a QR code, and shows :func:`short_code` beside it. The phone scans the
QR in Settings; the short code is read back over voice to confirm both
sides paired the same token. Pure data: no I/O, no keyring here (secrets
go to the keystore seam after parsing).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re

from xx_find import protocol

PAIRING_VERSION = 1
SHORT_CODE_DIGITS = 6
_DEVICE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


class PairingError(Exception):
    pass


def _check_device_id(device_id: object) -> str:
    if not isinstance(device_id, str) or not _DEVICE_RE.match(device_id):
        raise PairingError("bad device id")
    return device_id


def _check_server_url(url: object) -> str:
    if not isinstance(url, str):
        raise PairingError("bad server url")
    text = url.strip()
    if not (text.startswith("http://") or text.startswith("https://")):
        raise PairingError("bad server url")
    if len(text) > 256 or " " in text:
        raise PairingError("bad server url")
    return text


def _check_verify_key(key_hex: object) -> str:
    if not isinstance(key_hex, str):
        raise PairingError("bad verify key")
    text = key_hex.strip().lower()
    try:
        raw = bytes.fromhex(text)
    except ValueError as exc:
        raise PairingError("bad verify key") from exc
    if len(raw) != 32:
        raise PairingError("bad verify key")
    return text


def _check_token(token: object) -> str:
    if not isinstance(token, str):
        raise PairingError("bad pairing token")
    if len(token) < 8 or len(token) > 256 or not token.strip():
        raise PairingError("bad pairing token")
    if any(ch.isspace() for ch in token):
        raise PairingError("bad pairing token")
    return token


def build_pairing_payload(device_id: str, server_url: str,
                          verify_key_hex: str, token: str) -> str:
    """QR text the phone scans: compact JSON with the full bind bundle."""
    payload = {"v": PAIRING_VERSION,
               "device_id": _check_device_id(device_id),
               "server_url": _check_server_url(server_url),
               "verify_key": _check_verify_key(verify_key_hex),
               "token": _check_token(token)}
    try:
        return json.dumps(payload, sort_keys=True,
                          separators=(",", ":"),
                          ensure_ascii=True,
                          allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PairingError("pairing payload not encodable") from exc


def parse_pairing_payload(text: str) -> dict:
    """Validate scanned QR text; returns the bind bundle or raises."""
    if not isinstance(text, str):
        raise PairingError("bad pairing payload")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise PairingError("bad pairing payload") from exc
    if not isinstance(payload, dict):
        raise PairingError("bad pairing payload")
    if payload.get("v") != PAIRING_VERSION:
        raise PairingError("unsupported pairing version")
    bundle = {"v": PAIRING_VERSION,
              "device_id": _check_device_id(payload.get("device_id")),
              "server_url": _check_server_url(payload.get("server_url")),
              "verify_key": _check_verify_key(payload.get("verify_key")),
              "token": _check_token(payload.get("token"))}
    if set(payload) - set(bundle):
        raise PairingError("unknown pairing field")
    try:
        protocol.canonical(bundle)
    except protocol.ProtocolError as exc:
        raise PairingError("pairing bundle not canonical") from exc
    return bundle


def short_code(token: str, digits: int = SHORT_CODE_DIGITS) -> str:
    """Six-digit confirmation derived from the token hash (not a secret)."""
    _check_token(token)
    if digits < 4 or digits > 10:
        raise PairingError("bad code length")
    digest = hashlib.sha256(b"xx-find-pairing-code:"
                            + token.encode("utf-8")).digest()
    return str(int.from_bytes(digest[:4], "big")
               % (10 ** digits)).zfill(digits)


def verify_short_code(token: str, code: str) -> bool:
    try:
        expect = short_code(token, len(str(code)))
    except PairingError:
        return False
    return hmac.compare_digest(expect, str(code))
