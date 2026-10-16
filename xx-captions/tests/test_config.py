from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from xx_captions import config


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    return fake


def test_defaults_when_no_file(home: Path) -> None:
    data = config.load(home)
    assert data["engine"] == "sherpa-onnx"
    assert data["fallback_engine"] == "vosk"
    assert data["live_captions_enabled"] is False
    assert data["call_transcription_enabled"] is False


def test_roundtrip_preserves_values(home: Path) -> None:
    config.ensure_dirs(home)
    data = config.load(home)
    data["language"] = "de"
    data["announce_tone"] = True
    config.save(data, home)
    assert config.load(home)["language"] == "de"
    assert config.load(home)["announce_tone"] is True


def test_corrupt_file_falls_back_to_defaults(home: Path) -> None:
    config.ensure_dirs(home)
    config.config_path(home).write_text("{not json", encoding="utf-8")
    assert config.load(home) == dict(config.DEFAULT_CONFIG)


def test_non_dict_file_falls_back_to_defaults(home: Path) -> None:
    config.ensure_dirs(home)
    config.config_path(home).write_text("[1, 2]", encoding="utf-8")
    assert config.load(home) == dict(config.DEFAULT_CONFIG)


def test_wrong_types_keep_defaults(home: Path) -> None:
    config.ensure_dirs(home)
    config.config_path(home).write_text(
        json.dumps({"engine": 42, "live_captions_enabled": "yes"}),
        encoding="utf-8",
    )
    data = config.load(home)
    assert data["engine"] == "sherpa-onnx"
    assert data["live_captions_enabled"] is False


def test_ensure_dirs_mode_0700(home: Path) -> None:
    paths = config.ensure_dirs(home)
    for path in paths.values():
        assert path.is_dir()
        assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_save_mode_0600_and_models_dir(home: Path) -> None:
    config.ensure_dirs(home)
    target = config.save(config.load(home), home)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert config.models_dir(home).is_dir()
    assert config.transcripts_dir(home).is_dir()


def test_verify_model_roundtrip(tmp_path: Path) -> None:
    blob = tmp_path / "encoder.onnx"
    blob.write_bytes(b"fake-model-bytes")
    expected = config.sha256_file(blob)
    assert config.verify_model(blob, expected) is True
    assert config.verify_model(blob, "0" * 64) is False
    assert config.verify_model(tmp_path / "missing", expected) is False
    assert config.verify_model(blob, "") is False


def test_catalog_lists_default_model() -> None:
    entry = config.MODEL_CATALOG["sherpa-onnx-streaming-zipformer-en-2023-06-26"]
    assert entry["size_mb"] > 0
    assert entry["url"].startswith("https://")
    assert "encoder.onnx" in entry["files"]
    assert "vosk-model-small-en-us-0.15" in config.MODEL_CATALOG
