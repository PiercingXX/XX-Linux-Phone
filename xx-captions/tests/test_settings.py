"""Settings helpers and config round-trips (headless, no GTK window)."""
from __future__ import annotations

import time

from xx_captions import config, models
from xx_captions.settings import (
    format_day,
    format_duration,
    format_offset,
    format_time,
    initials_for,
    model_state,
    read_settings,
    short_model_name,
    write_setting,
)


def test_initials_from_names_and_numbers() -> None:
    assert initials_for("Mara Voss") == "MV"
    assert initials_for("Mom") == "M"
    assert initials_for("Ridgeline Dental") == "RD"
    assert initials_for("+1 (614) 555-0199") == "+1"
    assert initials_for("") == "?"
    assert initials_for(None) == "?"


def test_duration_and_offset_formatting() -> None:
    assert format_duration(252) == "4:12"
    assert format_duration(23) == "0:23"
    assert format_duration(0) == "0:00"
    assert format_duration("nope") == "0:00"
    assert format_offset(9800) == "0:09"
    assert format_offset(5400) == "0:05"


def test_day_stamps_are_nonempty() -> None:
    now = int(time.time())
    assert format_day(now).strip() != ""
    assert format_time(now).strip() != ""
    assert format_day("nope") == ""


def test_short_model_names() -> None:
    assert short_model_name(config.VOSK_ID) == "Vosk small English"
    assert short_model_name(config.SHERPA_20M_ID) == (
        "Sherpa streaming Zipformer")
    assert short_model_name("mystery") == "mystery"


def test_read_settings_defaults_on_clean_home(tmp_path) -> None:
    home = tmp_path / "home"
    settings = read_settings(home)
    assert settings["engine"] == "sherpa-onnx"
    assert settings["language"] == "en"
    assert settings["call_transcription_enabled"] is False


def test_write_setting_roundtrip(tmp_path) -> None:
    home = tmp_path / "home"
    write_setting(home, "engine", "vosk")
    write_setting(home, "language", "de")
    write_setting(home, "call_transcription_enabled", True)
    settings = read_settings(home)
    assert settings["engine"] == "vosk"
    assert settings["language"] == "de"
    assert settings["call_transcription_enabled"] is True
    assert config.load(home)["engine"] == "vosk"


def test_read_settings_rejects_unknown_values(tmp_path) -> None:
    home = tmp_path / "home"
    write_setting(home, "engine", "whisper")
    write_setting(home, "language", "xx")
    settings = read_settings(home)
    assert settings["engine"] == "sherpa-onnx"
    assert settings["language"] == "en"


def test_model_state_before_and_after_install(tmp_path) -> None:
    home = tmp_path / "home"
    model_id = config.DEFAULT_CONFIG["model"]
    installed, size_mb = model_state(home, model_id)
    assert installed is False
    assert size_mb > 0
    entry = config.MODEL_CATALOG[model_id]
    for name in entry["files"]:
        target = models.model_dir(model_id, home) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    installed, _ = model_state(home, model_id)
    assert installed is True
