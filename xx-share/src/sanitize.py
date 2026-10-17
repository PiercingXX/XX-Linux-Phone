"""Filename sanitization: stray path components never reach the disk.

Received fileName values are sender-controlled, so every one is reduced
to a single safe basename: no directories, no hidden dotfiles, no
control characters, and collision-safe renames when the name is taken.
"""
from __future__ import annotations

import re
from pathlib import Path

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_FALLBACK_NAME = "file"


def _truncate_bytes(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    cut = raw[:limit]
    while cut:
        try:
            return cut.decode("utf-8")
        except UnicodeDecodeError:
            cut = cut[:-1]
    return _FALLBACK_NAME


def sanitize_filename(name: object) -> str:
    if not isinstance(name, str):
        return _FALLBACK_NAME
    base = name.replace("\\", "/").split("/")[-1].strip()
    base = _CONTROL_RE.sub("", base)
    base = base.lstrip(".").strip()
    if not base or base in (".", ".."):
        return _FALLBACK_NAME
    if len(base.encode("utf-8")) > 255:
        stem, dot, ext = base.rpartition(".")
        if dot and len(ext.encode("utf-8")) <= 32:
            keep = 255 - len(ext.encode("utf-8")) - 1
            base = _truncate_bytes(stem, keep) + dot + ext
        else:
            base = _truncate_bytes(base, 255)
    return base


def unique_path(directory: str | Path, name: str) -> Path:
    target_dir = Path(directory)
    safe = sanitize_filename(name)
    candidate = target_dir / safe
    if not candidate.exists():
        return candidate
    stem, dot, ext = safe.rpartition(".")
    if not dot:
        stem, ext = safe, ""
    else:
        ext = dot + ext
    counter = 1
    while True:
        candidate = target_dir / f"{stem} ({counter}){ext}"
        if not candidate.exists():
            return candidate
        counter += 1
