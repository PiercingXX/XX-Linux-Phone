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


def sanitize_filename(name: object) -> str:
    if not isinstance(name, str):
        return _FALLBACK_NAME
    base = name.replace("\\", "/").split("/")[-1].strip()
    base = _CONTROL_RE.sub("", base)
    base = base.lstrip(".").strip()
    if not base or base in (".", ".."):
        return _FALLBACK_NAME
    if len(base) > 255:
        stem, dot, ext = base.rpartition(".")
        if dot and len(ext) <= 32:
            keep = 255 - len(ext) - 1
            base = stem[:keep] + dot + ext
        else:
            base = base[:255]
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
