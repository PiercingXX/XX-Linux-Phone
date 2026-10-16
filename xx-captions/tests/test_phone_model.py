"""Phone-default model: small streaming Zipformer (mobile-data sized).

The full-size download costs phones 310 MB for 68 MB on disk; the 20M
int8 release downloads 128 MB for 42 MB on disk and stays inside
lane 1's WER ceiling on the fixture clips (see test_real_speech and
the lane-2 report). It is the catalog phone default; the full model
remains for best quality.
"""
from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path
from unittest.mock import patch

from xx_captions import config, models
from xx_captions.engines import sherpa_status


def test_phone_default_is_small_model() -> None:
    assert config.DEFAULT_CONFIG["model"] == config.SHERPA_20M_ID
    assert config.SHERPA_20M_ID in config.MODEL_CATALOG


def test_small_entry_is_int8_only_and_smaller(tmp_path: Path) -> None:
    small = config.MODEL_CATALOG[config.SHERPA_20M_ID]
    full = config.MODEL_CATALOG[config.SHERPA_ID]
    assert small["engine"] == "sherpa-onnx"
    assert small["url"].startswith("https://")
    assert len(small["archive_sha256"]) == 64
    assert int(small["archive_sha256"], 16) >= 0
    assert small["archive_size_bytes"] < full["archive_size_bytes"] // 2
    assert small["size_mb"] < full["size_mb"]
    assert small["files"] == ("encoder.onnx", "decoder.onnx", "joiner.onnx",
                              "tokens.txt")
    members = small["archive_members"]
    assert set(members) == set(small["files"])
    for dest_name, suffix in members.items():
        if dest_name == "tokens.txt":
            assert suffix == "tokens.txt"
        else:
            assert "int8" in suffix, (dest_name, suffix)


def _make_20m_tar() -> bytes:
    entry = config.MODEL_CATALOG[config.SHERPA_20M_ID]
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode="w:bz2") as archive:
        for dest_name, suffix in entry["archive_members"].items():
            payload = f"fake-{dest_name}".encode()
            info = tarfile.TarInfo(f"top/{suffix}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return blob.getvalue()


class _FakeResponse:
    def __init__(self, blob: bytes) -> None:
        self._view = io.BytesIO(blob)
        self._total = len(blob)

    def read(self, size: int = -1) -> bytes:
        return self._view.read(size)

    def getheader(self, name: str) -> str | None:
        if name == "Content-Length":
            return str(self._total)
        return None

    def close(self) -> None:
        pass


def test_small_model_download_installs_int8_files(tmp_path: Path) -> None:
    home = tmp_path / "home"
    blob = _make_20m_tar()
    entry = dict(config.MODEL_CATALOG[config.SHERPA_20M_ID])
    entry["archive_sha256"] = hashlib.sha256(blob).hexdigest()
    entry["archive_size_bytes"] = len(blob)

    def opener(url: str, offset: int):
        assert url.startswith("https://")
        assert offset == 0
        return _FakeResponse(blob)

    with patch.dict(config.MODEL_CATALOG, {config.SHERPA_20M_ID: entry}):
        dest = models.download(config.SHERPA_20M_ID, home, opener=opener)
    assert dest == models.model_dir(config.SHERPA_20M_ID, home)
    assert models.is_installed(config.SHERPA_20M_ID, home) is True
    assert (dest / "encoder.onnx").read_bytes() == b"fake-encoder.onnx"


def test_status_names_default_model_when_absent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    ok, reason = sherpa_status(home)
    assert ok is False
    assert config.SHERPA_20M_ID in reason
