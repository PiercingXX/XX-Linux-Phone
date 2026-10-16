from __future__ import annotations

from pathlib import Path

from xx_captions import config, overlay_state
from xx_captions.overlay_state import CaptionBuffer


def test_partial_replaces_last_line_in_place() -> None:
    buf = CaptionBuffer()
    buf.push_partial("hello")
    assert buf.lines() == ("hello",)
    buf.push_partial("hello world")
    assert buf.lines() == ("hello world",)


def test_final_commits_and_clears_partial() -> None:
    buf = CaptionBuffer()
    buf.push_partial("hello")
    buf.push_final("hello world")
    assert buf.lines() == ("hello world",)
    assert buf.live_partial() == ""


def test_final_after_partial_keeps_both() -> None:
    buf = CaptionBuffer()
    buf.push_final("first line")
    buf.push_partial("second")
    assert buf.lines() == ("first line", "second")


def test_scroll_keeps_newest_three() -> None:
    buf = CaptionBuffer()
    for text in ("one", "two", "three", "four"):
        buf.push_final(text)
    assert buf.lines() == ("two", "three", "four")


def test_partial_plus_full_window_scrolls() -> None:
    buf = CaptionBuffer()
    for text in ("one", "two", "three"):
        buf.push_final(text)
    buf.push_partial("four")
    assert buf.lines() == ("two", "three", "four")


def test_empty_text_never_creates_blank_lines() -> None:
    buf = CaptionBuffer()
    assert buf.push_partial("") is False
    assert buf.push_partial("   ") is False
    assert buf.push_final("") is False
    assert buf.lines() == ()
    buf.push_final("kept")
    buf.push_partial("")
    assert buf.lines() == ("kept",)


def test_clear_resets() -> None:
    buf = CaptionBuffer()
    buf.push_final("a")
    buf.push_partial("b")
    buf.clear()
    assert buf.lines() == ()
    assert buf.live_partial() == ""


def test_tile_state_precedence() -> None:
    assert overlay_state.tile_state(False) == "off"
    assert overlay_state.tile_state(False, starting=True) == "starting"
    assert overlay_state.tile_state(True) == "on"
    assert overlay_state.tile_state(True, starting=True) == "on"
    assert overlay_state.tile_state(False, installed=False) == "no-model"
    assert overlay_state.tile_state(True, installed=False) == "no-model"
    assert overlay_state.tile_state(False, error=True) == "error"
    assert overlay_state.tile_state(False, installed=False,
                                    error=True) == "error"
    assert set(overlay_state.TILE_STATES) == {
        "off", "starting", "on", "no-model", "error"}


def test_margin_clamps_to_viewport() -> None:
    assert overlay_state.clamp_margin(-5) == 0
    assert overlay_state.clamp_margin(120) == 120
    assert overlay_state.clamp_margin(99999) <= 512
    assert overlay_state.clamp_margin("junk") == (
        overlay_state.DEFAULT_BOTTOM_MARGIN)


def test_margin_roundtrips_through_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    assert overlay_state.load_margin(home) == (
        overlay_state.DEFAULT_BOTTOM_MARGIN)
    kept = overlay_state.save_margin(140, home)
    assert kept == 140
    assert overlay_state.load_margin(home) == 140
    assert config.load(home)["overlay_margin_bottom"] == 140
    assert (config.config_path(home).stat().st_mode & 0o777) == 0o600


def test_phone_default_is_small_streaming_model() -> None:
    assert overlay_state.phone_default_model() == config.SHERPA_20M_ID


def test_model_missing_when_nothing_installed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    assert overlay_state.model_missing(home) is True


def test_model_present_when_any_catalog_model_installed(
        tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".local" / "share" / "xx-captions" / "models"
    entry = config.MODEL_CATALOG[config.VOSK_ID]
    for name in entry["files"]:
        target = root / config.VOSK_ID / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    assert overlay_state.model_missing(home) is False


def test_download_label_shows_size_first() -> None:
    label = overlay_state.download_label()
    assert "42" in label
    assert "MB" in label


def test_progress_fraction_bounds() -> None:
    assert overlay_state.progress_fraction(5, 10) == 0.5
    assert overlay_state.progress_fraction(0, 10) == 0.0
    assert overlay_state.progress_fraction(10, 10) == 1.0
    assert overlay_state.progress_fraction(99, 10) == 1.0
    assert overlay_state.progress_fraction(5, None) == 0.0
    assert overlay_state.progress_fraction(5, 0) == 0.0
