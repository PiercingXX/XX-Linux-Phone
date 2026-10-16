"""Headless tests for theme loading (no display, no GTK import)."""
import json

from xx_calendar import theme
from xx_calendar.theme import (
    FONT_FAMILIES,
    THEME_PRESETS,
    clamp_text_scale,
    css_for_theme,
    load_xxwm_config,
    resolve_font_css,
    resolve_preset_key,
    theme_from_xxwm_config,
)


def test_eight_presets_match_shell_keys():
    assert set(THEME_PRESETS) == {
        "amoled", "graphite", "forest", "ocean",
        "paper", "mist", "aura", "burgundy",
    }
    assert THEME_PRESETS["amoled"].background == "#000000"
    assert THEME_PRESETS["paper"].foreground == "#151515"


def test_four_font_families():
    assert set(FONT_FAMILIES) == {
        "system-light", "space-mono", "jetbrains-mono",
        "jetbrains-mono-nerd",
    }


def test_unknown_preset_falls_back_to_amoled():
    assert resolve_preset_key("nope") == "amoled"
    assert resolve_preset_key(None) == "amoled"
    assert resolve_preset_key("paper") == "paper"


def test_font_resolution_with_custom():
    assert "JetBrains" in resolve_font_css("jetbrains-mono")
    assert resolve_font_css("custom", "My Font") == "My Font"
    assert resolve_font_css("custom", "") == FONT_FAMILIES["jetbrains-mono-nerd"]
    assert resolve_font_css("bogus") == FONT_FAMILIES["jetbrains-mono-nerd"]


def test_text_scale_clamped_like_shell():
    assert clamp_text_scale(1.5) == 1.5
    assert clamp_text_scale(99.0) == 2.0
    assert clamp_text_scale(0.0) == 0.5
    assert clamp_text_scale("huge") == 1.0
    assert clamp_text_scale(None) == 1.0


def test_missing_shell_config_is_none(tmp_path):
    assert load_xxwm_config(tmp_path / "config.json") is None


def test_corrupt_shell_config_is_none(tmp_path):
    bad = tmp_path / "config.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_xxwm_config(bad) is None


def test_shell_config_selects_preset_and_font(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "theme": "paper",
        "font": "space-mono",
        "text_size_scale": 1.25,
        "prefer_dark": False,
    }), encoding="utf-8")
    resolved = theme_from_xxwm_config(load_xxwm_config(cfg))
    assert resolved.preset.key == "paper"
    assert "Space Mono" in resolved.font_css
    assert resolved.text_scale == 1.25
    assert resolved.prefer_dark is False


def test_absent_shell_config_falls_back_to_defaults():
    resolved = theme_from_xxwm_config(load_xxwm_config())
    if resolved.preset.key != "amoled":
        assert resolved.preset.key in THEME_PRESETS
    assert theme_from_xxwm_config(None).preset.key == "amoled"
    assert theme_from_xxwm_config(None).prefer_dark is True


def test_css_mentions_theme_colors_and_font():
    css = css_for_theme(theme_from_xxwm_config({"theme": "forest"}))
    assert "#10261B" in css
    assert "JetBrains" in css
    assert theme.APP_ID == "io.piercingxx.XXCalendar"
