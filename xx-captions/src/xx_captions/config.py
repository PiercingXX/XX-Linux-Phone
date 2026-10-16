"""Pure configuration and first-run storage for xx-captions.

No D-Bus, no GTK, no network: load/save JSON plus the model-download
checksum helpers. XDG directories are created mode 0700 before any file
inside them is opened, so a clean device never hits "unable to open"
on first launch.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

APP_NAME = "xx-captions"
CONFIG_VERSION = 1

SHERPA_ID = "sherpa-onnx-streaming-zipformer-en-2023-06-26"
SHERPA_20M_ID = "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17"
VOSK_ID = "vosk-model-small-en-us-0.15"

DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "engine": "sherpa-onnx",
    "fallback_engine": "vosk",
    "model": SHERPA_20M_ID,
    "language": "en",
    "live_captions_enabled": False,
    "call_transcription_enabled": False,
    "announce_tone": False,
    "overlay_margin_bottom": 72,
}

MODEL_CATALOG = {
    SHERPA_ID: {
        "engine": "sherpa-onnx",
        "url": (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "asr-models/sherpa-onnx-streaming-zipformer-en-2023-06-26.tar.bz2"
        ),
        "archive_sha256": (
            "639e25b578e9e997131402199419c13a941f8e4e198e2da1ce57dbf5cf401282"
        ),
        "archive_size_bytes": 310414022,
        "size_mb": 68,
        "files": ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt"),
        "archive_members": {
            "encoder.onnx":
                "encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
            "decoder.onnx":
                "decoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
            "joiner.onnx":
                "joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
            "tokens.txt": "tokens.txt",
        },
    },
    SHERPA_20M_ID: {
        "engine": "sherpa-onnx",
        "url": (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "asr-models/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17"
            ".tar.bz2"
        ),
        "archive_sha256": (
            "9c559283e8498d3fe95913c79ca1cb454bb26281ac2b102b41306c7d752765d9"
        ),
        "archive_size_bytes": 127887156,
        "size_mb": 42,
        "files": ("encoder.onnx", "decoder.onnx", "joiner.onnx",
                  "tokens.txt"),
        "archive_members": {
            "encoder.onnx": "encoder-epoch-99-avg-1.int8.onnx",
            "decoder.onnx": "decoder-epoch-99-avg-1.int8.onnx",
            "joiner.onnx": "joiner-epoch-99-avg-1.int8.onnx",
            "tokens.txt": "tokens.txt",
        },
    },
    VOSK_ID: {
        "engine": "vosk",
        "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        "archive_sha256": (
            "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"
        ),
        "archive_size_bytes": 41205931,
        "size_mb": 68,
        "files": ("am/final.mdl", "graph/HCLr.fst", "graph/Gr.fst",
                  "conf/model.conf"),
    },
}


def _home() -> Path:
    return Path.home()


def config_dir(home: Path | None = None) -> Path:
    return (home or _home()) / ".config" / APP_NAME


def data_dir(home: Path | None = None) -> Path:
    return (home or _home()) / ".local" / "share" / APP_NAME


def models_dir(home: Path | None = None) -> Path:
    return data_dir(home) / "models"


def transcripts_dir(home: Path | None = None) -> Path:
    return data_dir(home) / "transcripts"


def config_path(home: Path | None = None) -> Path:
    return config_dir(home) / "config.json"


def ensure_dirs(home: Path | None = None) -> dict[str, Path]:
    """Create every directory this service touches, mode 0700."""
    paths = {
        "config": config_dir(home),
        "data": data_dir(home),
        "models": models_dir(home),
        "transcripts": transcripts_dir(home),
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return paths


def load(home: Path | None = None) -> dict:
    """Fail-soft load: missing/corrupt/foreign-shaped files yield defaults."""
    data = dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(config_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if not isinstance(raw, dict):
        return data
    for key, default in DEFAULT_CONFIG.items():
        value = raw.get(key, default)
        if isinstance(default, bool) and isinstance(value, bool):
            data[key] = value
        elif isinstance(default, int) and isinstance(value, int):
            data[key] = value
        elif isinstance(default, str) and isinstance(value, str):
            data[key] = value
    return data


def save(data: dict, home: Path | None = None) -> Path:
    """Atomic write, file mode 0600, eMMC-friendly (skip identical)."""
    target = config_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({k: data.get(k, v) for k, v in DEFAULT_CONFIG.items()},
                         indent=2) + "\n"
    try:
        if target.read_text(encoding="utf-8") == payload:
            return target
    except (OSError, ValueError):
        pass
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    os.chmod(target, 0o600)
    return target


def sha256_file(path: Path, chunk_bytes: int = 65536) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model(path: Path, expected_hex: str) -> bool:
    """Fail closed: missing file, bad digest, or bad expectation all fail."""
    if not expected_hex or not isinstance(expected_hex, str):
        return False
    try:
        actual = sha256_file(path)
    except OSError:
        return False
    return hmac.compare_digest(actual.lower(), expected_hex.lower())
