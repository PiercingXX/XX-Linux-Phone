"""First-run model downloads: resumable, verified, never automatic.

Nothing here runs without an explicit download() call: engines and
the service only read what is already installed. Archives download
over HTTPS with Range resume, are SHA-256 verified against the
catalog before use, and land atomically in
~/.local/share/xx-captions/models/<id> (dirs 0700).
"""
from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from xx_captions import config

ProgressFn = Callable[[int, int | None], None]


class ModelError(RuntimeError):
    pass


def model_dir(model_id: str, home: Path | None = None) -> Path:
    return config.models_dir(home) / model_id


def is_installed(model_id: str, home: Path | None = None) -> bool:
    """Every catalog file present, no network, no side effects."""
    entry = config.MODEL_CATALOG.get(model_id)
    if entry is None:
        return False
    root = model_dir(model_id, home)
    return all((root / name).is_file() for name in entry["files"])


def list_installed(home: Path | None = None) -> list[str]:
    """Catalog ids fully installed, oldest first by directory mtime."""
    found = [mid for mid in config.MODEL_CATALOG if is_installed(mid, home)]
    found.sort(key=lambda mid: model_dir(mid, home).stat().st_mtime)
    return found


def delete(model_id: str, home: Path | None = None) -> bool:
    """Remove an installed model; True when something was removed."""
    if model_id not in config.MODEL_CATALOG:
        raise ModelError(f"unknown model {model_id!r}")
    root = model_dir(model_id, home)
    if not root.exists() and not root.is_symlink():
        return False
    if root.is_symlink() or root.is_file():
        root.unlink()
    else:
        shutil.rmtree(root)
    return True


def installed_size_bytes(model_id: str, home: Path | None = None) -> int:
    total = 0
    for path in model_dir(model_id, home).rglob("*"):
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
    return total


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _default_opener(url: str, offset: int):
    request = urllib.request.Request(url)
    if offset:
        request.add_header("Range", f"bytes={offset}-")
    return urllib.request.urlopen(request, timeout=60)


def _archive_name(model_id: str, url: str) -> str:
    suffix = ".tar.bz2" if url.endswith(".tar.bz2") else ".zip"
    return model_id + suffix


def download(model_id: str, home: Path | None = None,
             progress: ProgressFn | None = None,
             opener=None) -> Path:
    """Fetch, verify, and install a catalog model; returns its directory."""
    entry = config.MODEL_CATALOG.get(model_id)
    if entry is None:
        raise ModelError(f"unknown model {model_id!r}")
    url = entry["url"]
    if not url.startswith("https://"):
        raise ModelError("model URLs must be https")
    expected = entry.get("archive_sha256")
    if not expected:
        raise ModelError(f"no checksum on file for {model_id!r}: refusing")
    needed = int(entry.get("archive_size_bytes") or 0)
    dest = model_dir(model_id, home)
    config.ensure_dirs(home)
    if _free_bytes(dest.parent) < needed + (needed // 2) + (64 << 20):
        raise ModelError(f"not enough disk space for {model_id!r}")
    tmpdir = Path(tempfile.mkdtemp(prefix="xx-captions-dl-",
                                   dir=str(dest.parent)))
    part = dest.parent / (_archive_name(model_id, url) + ".part")
    try:
        _fetch(url, part, progress, opener or _default_opener)
        if not config.verify_model(part, expected):
            part.unlink(missing_ok=True)
            raise ModelError(f"checksum mismatch for {model_id!r}")
        archive = tmpdir / _archive_name(model_id, url)
        os.replace(part, archive)
        staged = tmpdir / "staged"
        staged.mkdir()
        _install_files(archive, entry, staged)
        for path in entry["files"]:
            if not (staged / path).is_file():
                raise ModelError(f"archive for {model_id!r} lacks {path}")
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest)
        elif dest.is_symlink() or dest.exists():
            dest.unlink()
        os.replace(staged, dest)
        for root, dirs, _files in os.walk(dest):
            for name in [root, *(os.path.join(root, d) for d in dirs)]:
                try:
                    os.chmod(name, 0o700)
                except OSError:
                    pass
        return dest
    except Exception:
        if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
            dest.unlink()
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _fetch(url: str, part: Path, progress: ProgressFn | None,
           opener) -> None:
    offset = part.stat().st_size if part.exists() else 0
    if offset:
        mode = "ab"
    else:
        mode = "wb"
    response = opener(url, offset)
    try:
        total: int | None = None
        getheader = getattr(response, "getheader", None)
        if callable(getheader):
            raw_total = getheader("Content-Length")
            if raw_total is not None:
                try:
                    total = int(raw_total) + offset
                except ValueError:
                    total = None
        with open(part, mode) as handle:
            os.chmod(part, 0o600)
            done = offset
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(part, 0o600)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _install_files(archive: Path, entry: dict, staged: Path) -> None:
    members = entry.get("archive_members")
    if members:
        with tarfile.open(archive, "r:*") as tar:
            for dest_name, suffix in members.items():
                member = next((m for m in tar.getmembers()
                               if m.name.endswith(suffix)
                               and tar.extractfile(m) is not None), None)
                if member is None:
                    raise ModelError(f"archive lacks {suffix}")
                source = tar.extractfile(member)
                assert source is not None
                target = staged / dest_name
                with open(target, "wb") as handle:
                    shutil.copyfileobj(source, handle)
        return
    with zipfile.ZipFile(archive) as archive_zip:
        names = archive_zip.namelist()
        top = names[0].split("/")[0] + "/" if names and "/" in names[0] else ""
        for name in names:
            if name.endswith("/"):
                continue
            rel = name[len(top):] if top and name.startswith(top) else name
            target = staged / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive_zip.open(name) as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)


__all__ = ["ModelError", "delete", "download", "installed_size_bytes",
           "is_installed", "list_installed", "model_dir"]
