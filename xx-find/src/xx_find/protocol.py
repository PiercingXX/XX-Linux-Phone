"""Wire protocol: JSON schemas plus signature/expiry/counter validation.

The server only ever sees ciphertext reports. Commands are owner-signed;
the agent accepts one only when the signature verifies, the counter is
newer than anything already acted on (no replay), and the expiry is sane.
Pure functions: no network, no clock beyond an injected `now`."""

from __future__ import annotations

import base64
import json

from xx_find import crypto

PROTOCOL_VERSION = 1
COMMAND_TYPES = frozenset({"locate", "ring", "message", "lock", "erase"})
PRIVILEGED_COMMANDS = frozenset({"erase"})
DEFAULT_MAX_AGE_S = 300
DEFAULT_MAX_SKEW_S = 60


class ProtocolError(Exception):
    pass


def canonical(obj: dict) -> bytes:
    """Canonical bytes commands are signed over.

    ``json.dumps`` with ``sort_keys=True``, ``separators=(",", ":")``,
    ``ensure_ascii=True`` and ``allow_nan=False``, UTF-8 encoded. Key order
    is lexicographic by Unicode code point at every nesting level, with no
    whitespace. Only strings, JS-safe integers, booleans, objects and
    arrays may appear; floats, NaN/Infinity and null are rejected so a
    JavaScript signer produces byte-identical output (see PORT_SPEC.md).
    """
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True,
                          allow_nan=False).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("message is not JSON-serializable") from exc


JS_SAFE_INT = 2 ** 53 - 1


def _check_args_value(value: object) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        if not (-JS_SAFE_INT <= value <= JS_SAFE_INT):
            raise ProtocolError("command arg integer out of JS-safe range")
        return
    if isinstance(value, str):
        return
    if isinstance(value, float):
        raise ProtocolError(
            "command args allow strings, integers and booleans only "
            "(no floats)")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError("command arg keys must be strings")
            _check_args_value(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _check_args_value(item)
        return
    raise ProtocolError(
        "command args allow strings, integers and booleans only")


def check_args(args: object) -> dict:
    if not isinstance(args, dict):
        raise ProtocolError("command args must be an object")
    for key, value in args.items():
        if not isinstance(key, str):
            raise ProtocolError("command arg keys must be strings")
        _check_args_value(value)
    return args


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(text: str) -> bytes:
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ProtocolError("bad base64 field") from exc


def encode_location_plaintext(lat: float, lon: float, accuracy_m: float,
                              epoch: int, battery_pct: int | None,
                              source: str) -> bytes:
    """Plaintext that is sealed on the phone; the server never sees it."""
    if source not in ("geoclue", "modem-gps", "modem-cell", "manual"):
        raise ProtocolError("unknown location source: %r" % (source,))
    try:
        lat_f, lon_f, acc_f = float(lat), float(lon), float(accuracy_m)
        epoch_i = int(epoch)
    except (TypeError, ValueError) as exc:
        raise ProtocolError("bad location fix") from exc
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
        raise ProtocolError("coordinates out of range")
    payload = {"lat": lat_f, "lon": lon_f, "acc_m": acc_f,
               "epoch": epoch_i, "source": source}
    if battery_pct is not None:
        payload["battery_pct"] = int(battery_pct)
    return canonical(payload)


def decode_location_plaintext(raw: bytes) -> dict:
    try:
        payload = json.loads(bytes(raw).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("bad location plaintext") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("bad location plaintext")
    return payload


def build_report_envelope(device_id: str, counter: int, epoch: int,
                          sealed: bytes) -> dict:
    return {"v": PROTOCOL_VERSION, "device_id": device_id,
            "counter": int(counter), "epoch": int(epoch),
            "box": _b64encode(bytes(sealed))}


def build_command(device_id: str, counter: int, expires: int,
                  cmd_type: str, args: dict | None = None) -> dict:
    if cmd_type not in COMMAND_TYPES:
        raise ProtocolError("unknown command type: %r" % (cmd_type,))
    if not device_id:
        raise ProtocolError("command needs a device_id")
    for value in (counter, expires):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError("command needs integer counter/exp")
    checked = check_args(dict(args or {}))
    return {"v": PROTOCOL_VERSION, "device_id": device_id,
            "counter": int(counter), "exp": int(expires),
            "type": cmd_type, "args": checked}


def sign_envelope(command: dict, signing_key_hex: str) -> dict:
    sig = crypto.sign_command(canonical(command), signing_key_hex)
    return {"cmd": command, "sig": _b64encode(sig)}


def _check_envelope_shape(envelope: object) -> tuple[dict, bytes]:
    if not isinstance(envelope, dict):
        raise ProtocolError("envelope must be an object")
    cmd = envelope.get("cmd")
    if not isinstance(cmd, dict):
        raise ProtocolError("envelope needs a cmd object")
    if not isinstance(envelope.get("sig"), str):
        raise ProtocolError("envelope needs a base64 sig")
    return cmd, _b64decode(envelope["sig"])


def validate_command(envelope: dict, verify_key_hex: str,
                     last_counter: int, now: int,
                     max_age_s: int = DEFAULT_MAX_AGE_S,
                     max_skew_s: int = DEFAULT_MAX_SKEW_S) -> dict:
    """Verify signature, freshness and monotonic counter. Returns the inner
    command on success; raises ProtocolError otherwise."""
    cmd, sig = _check_envelope_shape(envelope)
    if cmd.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    if cmd.get("type") not in COMMAND_TYPES:
        raise ProtocolError("unknown command type")
    try:
        crypto.verify_command(canonical(cmd), sig, verify_key_hex)
    except crypto.CryptoError as exc:
        raise ProtocolError("signature rejected") from exc
    counter, exp = cmd.get("counter"), cmd.get("exp")
    if isinstance(counter, bool) or not isinstance(counter, int):
        raise ProtocolError("command needs integer counter/exp")
    if isinstance(exp, bool) or not isinstance(exp, int):
        raise ProtocolError("command needs integer counter/exp")
    if counter <= int(last_counter):
        raise ProtocolError("stale or replayed counter")
    if exp < int(now) - int(max_skew_s):
        raise ProtocolError("command expired")
    if exp > int(now) + int(max_age_s):
        raise ProtocolError("command expiry too far ahead")
    if not isinstance(cmd.get("device_id"), str) or not cmd["device_id"]:
        raise ProtocolError("command needs a device_id")
    check_args(cmd.get("args"))
    return cmd


def validate_report(envelope: dict, last_counter: int,
                    now: int) -> dict:
    """Shape + freshness check for a ciphertext report envelope. The box
    stays opaque here; only the owner can open it."""
    if not isinstance(envelope, dict):
        raise ProtocolError("report must be an object")
    if envelope.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    try:
        counter, epoch = int(envelope["counter"]), int(envelope["epoch"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolError("report needs integer counter/epoch") from exc
    if counter <= int(last_counter):
        raise ProtocolError("stale or replayed report counter")
    if abs(epoch - int(now)) > 7 * 24 * 3600:
        raise ProtocolError("report timestamp out of range")
    if not isinstance(envelope.get("device_id"), str) \
            or not envelope["device_id"]:
        raise ProtocolError("report needs a device_id")
    box = envelope.get("box")
    if not isinstance(box, str) or not box:
        raise ProtocolError("report needs a sealed box")
    raw = _b64decode(box)
    if len(raw) < crypto.SEAL_OVERHEAD + 1:
        raise ProtocolError("sealed box too short")
    return envelope
