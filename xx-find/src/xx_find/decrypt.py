"""Owner-side decrypt helper: open sealed location reports (lane 6).

The server stores ciphertext only; the owner opens a report locally with
the box secret key. Reads one sealed box (base64 text, a report-envelope
JSON file, or a device entry out of a server store file) and prints the
plaintext JSON to stdout. Pure local file/stdout work: no network, no
keyring, no bus. Secrets arrive via argv or files the owner points at;
nothing here stores them.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from pathlib import Path

from xx_find import crypto


def _read_secret(value: str | None, path: str | None) -> bytes:
    if value is not None and path is not None:
        raise ValueError("pass exactly one of --secret-key/--secret-key-file")
    raw = value if value is not None else None
    if path is not None:
        raw = Path(path).read_text(encoding="utf-8")
    if not raw or not raw.strip():
        raise ValueError("owner secret key is required")
    try:
        return bytes.fromhex("".join(raw.split()))
    except ValueError as exc:
        raise ValueError("owner secret key must be hex") from exc


def _read_box(args: argparse.Namespace) -> bytes:
    sources = [args.box is not None, args.box_file is not None,
               args.report_file is not None, args.store is not None]
    if sum(sources) != 1:
        raise ValueError("pass exactly one sealed-box source: "
                         "--box/--box-file/--report-file/--store")
    if args.box is not None:
        text = args.box
    elif args.box_file is not None:
        text = Path(args.box_file).read_text(encoding="utf-8")
    elif args.report_file is not None:
        envelope = json.loads(
            Path(args.report_file).read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or not envelope.get("box"):
            raise ValueError("report file has no sealed box")
        text = envelope["box"]
    else:
        if not args.device:
            raise ValueError("--store needs --device")
        store = json.loads(Path(args.store).read_text(encoding="utf-8"))
        try:
            report = store["devices"][args.device]["last_report"]
        except (KeyError, TypeError) as exc:
            raise ValueError("no such device in store") from exc
        if not isinstance(report, dict) or not report.get("box"):
            raise ValueError("device has no report yet")
        text = report["box"]
    try:
        return base64.b64decode("".join(str(text).split()),
                                validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("sealed box must be base64") from exc


def open_report(box: bytes, secret_key: bytes) -> dict:
    """Unseal one report box; returns the decoded plaintext mapping."""
    raw_secret = bytes(secret_key)
    if len(raw_secret) != 32:
        raise ValueError("owner secret key must be 32 bytes")
    raw = crypto.unseal_report(bytes(box), raw_secret.hex())
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("sealed box did not hold location JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("sealed box did not hold location JSON")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xx-find-decrypt",
        description="Open an xx-find sealed location report locally.")
    key = parser.add_mutually_exclusive_group(required=True)
    key.add_argument("--secret-key", default=None,
                     help="owner box secret key, hex")
    key.add_argument("--secret-key-file", default=None,
                     help="file holding the owner box secret key, hex")
    box = parser.add_mutually_exclusive_group(required=True)
    box.add_argument("--box", default=None, help="sealed box, base64")
    box.add_argument("--box-file", default=None,
                     help="file holding the sealed box, base64")
    box.add_argument("--report-file", default=None,
                     help="report envelope JSON file (has a box field)")
    box.add_argument("--store", default=None,
                     help="server store.json file (needs --device)")
    parser.add_argument("--device", default=None,
                        help="device id when reading --store")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        secret = _read_secret(args.secret_key, args.secret_key_file)
        sealed = _read_box(args)
        payload = open_report(sealed, secret)
    except (ValueError, crypto.CryptoError, OSError) as exc:
        print("xx-find-decrypt: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
