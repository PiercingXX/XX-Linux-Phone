from __future__ import annotations

import hashlib
import io
import stat
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from xx_captions import config, models

VOSK_MEMBERS = ("am/final.mdl", "graph/HCLr.fst", "graph/Gr.fst",
                "conf/model.conf")


def _make_vosk_zip() -> bytes:
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        for name in VOSK_MEMBERS:
            archive.writestr(f"{config.VOSK_ID}/{name}",
                             f"fake-{name}".encode())
    return blob.getvalue()


def _make_sherpa_tar() -> bytes:
    entry = config.MODEL_CATALOG[config.SHERPA_ID]
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode="w:bz2") as archive:
        for dest_name, suffix in entry["archive_members"].items():
            payload = f"fake-{dest_name}".encode()
            info = tarfile.TarInfo(f"top/{suffix}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return blob.getvalue()


class _FakeResponse:
    def __init__(self, blob: bytes, total: int | None = None) -> None:
        self._view = io.BytesIO(blob)
        self._total = len(blob) if total is None else total

    def read(self, size: int = -1) -> bytes:
        return self._view.read(size)

    def getheader(self, name: str) -> str | None:
        if name == "Content-Length":
            return str(self._total)
        return None

    def close(self) -> None:
        pass


def _opener(blob: bytes, seen: dict, total: int | None = None):
    def open_url(url: str, offset: int):
        seen["url"] = url
        seen["offset"] = offset
        assert url.startswith("https://")
        return _FakeResponse(blob[offset:], len(blob) if total is None else total)
    return open_url


def _patch_entry(model_id: str, blob: bytes, **overrides):
    entry = dict(config.MODEL_CATALOG[model_id])
    entry["archive_sha256"] = hashlib.sha256(blob).hexdigest()
    entry["archive_size_bytes"] = len(blob)
    entry.update(overrides)
    return patch.dict(config.MODEL_CATALOG, {model_id: entry})


def test_probes_touch_nothing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert models.is_installed(config.SHERPA_ID, home) is False
    assert models.is_installed(config.VOSK_ID, home) is False
    assert models.is_installed("no-such-model", home) is False
    assert models.list_installed(home) == []
    assert not (home / ".local").exists()


def test_vosk_download_installs_and_lists(tmp_path: Path) -> None:
    home = tmp_path / "home"
    blob = _make_vosk_zip()
    seen: dict = {}
    calls: list = []
    with _patch_entry(config.VOSK_ID, blob):
        dest = models.download(config.VOSK_ID, home,
                               progress=lambda d, t: calls.append((d, t)),
                               opener=_opener(blob, seen))
    assert dest == models.model_dir(config.VOSK_ID, home)
    assert models.is_installed(config.VOSK_ID, home) is True
    assert models.list_installed(home) == [config.VOSK_ID]
    assert seen["url"] == config.MODEL_CATALOG[config.VOSK_ID]["url"]
    assert seen["offset"] == 0
    assert calls and calls[-1][0] == len(blob)
    assert [c[0] for c in calls] == sorted(c[0] for c in calls)
    payload = (dest / "am" / "final.mdl").read_bytes()
    assert payload == b"fake-am/final.mdl"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o700


def test_sherpa_download_renames_members(tmp_path: Path) -> None:
    home = tmp_path / "home"
    blob = _make_sherpa_tar()
    with _patch_entry(config.SHERPA_ID, blob):
        dest = models.download(config.SHERPA_ID, home,
                               opener=_opener(blob, {}))
    for name in config.MODEL_CATALOG[config.SHERPA_ID]["files"]:
        assert (dest / name).is_file()
    assert (dest / "encoder.onnx").read_bytes() == b"fake-encoder.onnx"


def test_resume_continues_partial_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    blob = _make_vosk_zip()
    part = models.model_dir(config.VOSK_ID, home).parent / (
        config.VOSK_ID + ".zip.part")
    part.parent.mkdir(parents=True)
    half = len(blob) // 2
    part.write_bytes(blob[:half])
    seen: dict = {}
    with _patch_entry(config.VOSK_ID, blob):
        models.download(config.VOSK_ID, home, opener=_opener(blob, seen))
    assert seen["offset"] == half
    assert models.is_installed(config.VOSK_ID, home) is True


def test_checksum_mismatch_leaves_nothing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    blob = _make_vosk_zip()
    entry = dict(config.MODEL_CATALOG[config.VOSK_ID])
    entry["archive_sha256"] = "0" * 64
    entry["archive_size_bytes"] = len(blob)
    with patch.dict(config.MODEL_CATALOG, {config.VOSK_ID: entry}):
        with pytest.raises(models.ModelError):
            models.download(config.VOSK_ID, home, opener=_opener(blob, {}))
    assert models.is_installed(config.VOSK_ID, home) is False
    assert list(models.model_dir(config.VOSK_ID, home).parent.glob("*.part")) == []


def test_rejects_non_https_and_unknown_id(tmp_path: Path) -> None:
    home = tmp_path / "home"
    with _patch_entry(config.VOSK_ID, _make_vosk_zip(),
                      url="http://example.com/m.zip"):
        with pytest.raises(models.ModelError):
            models.download(config.VOSK_ID, home, opener=_opener(b"", {}))
    with pytest.raises(models.ModelError):
        models.download("no-such-model", home, opener=_opener(b"", {}))


def test_no_checksum_means_no_download(tmp_path: Path) -> None:
    home = tmp_path / "home"
    with _patch_entry(config.VOSK_ID, _make_vosk_zip()) as mocked:
        del mocked[config.VOSK_ID]["archive_sha256"]
        with pytest.raises(models.ModelError):
            models.download(config.VOSK_ID, home, opener=_opener(b"", {}))


def test_disk_space_checked_before_download(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(models, "_free_bytes", lambda path: 0)
    with _patch_entry(config.VOSK_ID, _make_vosk_zip()):
        with pytest.raises(models.ModelError, match="disk space"):
            models.download(config.VOSK_ID, home, opener=_opener(b"", {}))


def test_delete_roundtrip(tmp_path: Path) -> None:
    home = tmp_path / "home"
    blob = _make_vosk_zip()
    assert models.delete(config.VOSK_ID, home) is False
    with _patch_entry(config.VOSK_ID, blob):
        models.download(config.VOSK_ID, home, opener=_opener(blob, {}))
    assert models.installed_size_bytes(config.VOSK_ID, home) > 0
    assert models.delete(config.VOSK_ID, home) is True
    assert models.is_installed(config.VOSK_ID, home) is False
    assert models.delete(config.VOSK_ID, home) is False
    with pytest.raises(models.ModelError):
        models.delete("no-such-model", home)
