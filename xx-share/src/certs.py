"""Device certificate: mint via openssl, fingerprint via hashlib.

Python's stdlib ssl can serve a certificate but cannot mint one, so the
self-signed certificate is created once with the system openssl binary
(present on every target image) and then only read back. Generation runs
on a worker thread, never on the GTK main loop.
"""
from __future__ import annotations

import shutil
import ssl
import subprocess
from pathlib import Path

from protocol import fingerprint_of_der

CERT_FILENAME = "xx-share-cert.pem"
KEY_FILENAME = "xx-share-key.pem"


def openssl_available() -> bool:
    return shutil.which("openssl") is not None


def ensure_certpair(directory: str | Path) -> tuple[Path, Path]:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    cert_path = target / CERT_FILENAME
    key_path = target / KEY_FILENAME
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    if not openssl_available():
        raise RuntimeError("openssl is required to create the device certificate")
    command = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(key_path), "-out", str(cert_path),
        "-days", "8250", "-nodes", "-subj", "/CN=xx-share",
    ]
    try:
        subprocess.run(command, capture_output=True, text=True,
                       timeout=120, check=True)
    except (subprocess.SubprocessError, OSError) as error:
        raise RuntimeError(f"openssl failed to mint certificate: {error}") from error
    return cert_path, key_path


def cert_fingerprint(cert_path: str | Path) -> str:
    try:
        der = ssl.PEM_cert_to_DER_cert(Path(cert_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, ssl.SSLError) as error:
        raise ValueError(f"unreadable certificate: {error}") from error
    return fingerprint_of_der(der)
