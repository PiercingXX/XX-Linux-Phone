"""Headless smoke tests for theme loading (no display, no GI imports)."""

import json
import re
import sys

from xx_email import theme


def test_pure_module_imports_no_gi():
    assert "gi" not in sys.modules


def test_preset_table_matches_shell():
    assert set(theme.THEME_PRESETS) == {
        "amoled", "graphite", "forest", "ocean",
        "paper", "mist", "aura", "burgundy",
    }
    amoled = theme.THEME_PRESETS["amoled"]
    assert amoled.background == "#000000"
    assert theme.THEME_PRESETS["burgundy"].background == "#2A1018"
    for preset in theme.THEME_PRESETS.values():
        for field in ("background", "surface", "surface_alt", "border",
                      "foreground", "muted", "accent"):
            assert re.match(r"#[0-9a-fA-F]{6}\Z", getattr(preset, field)), field


def test_font_families_match_shell():
    assert set(theme.FONT_FAMILIES) == {
        "system-light", "space-mono", "jetbrains-mono", "jetbrains-mono-nerd",
    }


def test_missing_shell_config_resolves_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv(theme.SHELL_CONFIG_ENV, str(tmp_path / "absent.json"))
    assert theme.load_shell_config() == {}
    assert theme.resolve_theme({}).key == theme.DEFAULT_THEME
    assert theme.resolve_font({}) == theme.FONT_FAMILIES[theme.DEFAULT_FONT]
    assert theme.text_scale({}) == 1.0
    assert theme.prefer_dark({}) is True


def test_resolve_named_theme_and_font(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme": "forest", "font": "space-mono",
                                "text_size_scale": 1.5, "prefer_dark": False}),
                    encoding="utf-8")
    monkeypatch.setenv(theme.SHELL_CONFIG_ENV, str(path))
    assert theme.shell_config_path() == path
    shell = theme.load_shell_config()
    assert theme.resolve_theme(shell).key == "forest"
    assert theme.resolve_font(shell) == "Space Mono, Monospace"
    assert theme.text_scale(shell) == 1.5
    assert theme.prefer_dark(shell) is False


def test_unknown_theme_and_font_fall_back():
    assert theme.resolve_theme({"theme": "neon"}).key == theme.DEFAULT_THEME
    assert theme.resolve_font({"font": "comic-sans"}) == theme.FONT_FAMILIES[theme.DEFAULT_FONT]


def test_custom_theme_needs_valid_background():
    good = theme.resolve_theme({"theme": "custom", "custom_background": "#123ABC"})
    assert good.key == "custom"
    assert good.background == "#123ABC"
    for bad in ("#123ABZ", "red", "#1234", "", None, 123):
        assert theme.resolve_theme({"theme": "custom",
                                    "custom_background": bad}).key == theme.DEFAULT_THEME


def test_custom_font_needs_family():
    assert theme.resolve_font({"font": "custom",
                               "custom_font_family": "My Font"}) == "My Font"
    assert theme.resolve_font({"font": "custom"}) == theme.FONT_FAMILIES[theme.DEFAULT_FONT]


def test_scale_clamped_and_defensive():
    assert theme.text_scale({"text_size_scale": 9.0}) == 2.0
    assert theme.text_scale({"text_size_scale": 0.1}) == 0.5
    assert theme.text_scale({"text_size_scale": "huge"}) == 1.0


def test_invalid_shell_config_file_ignored(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv(theme.SHELL_CONFIG_ENV, str(path))
    assert theme.load_shell_config() == {}
    path.write_text("[1, 2]", encoding="utf-8")
    assert theme.load_shell_config() == {}


def test_android_preset_map_covers_android_set():
    assert set(theme.ANDROID_PRESET_MAP) == {
        "AMOLED Night", "Graphite", "Forest Night", "Ocean Drift",
        "Burgundy", "Paper", "Mist", "Custom",
    }
    for key in theme.ANDROID_PRESET_MAP.values():
        assert key == "custom" or key in theme.THEME_PRESETS


def test_css_uses_only_preset_colors():
    preset = theme.THEME_PRESETS["paper"]
    css = theme.theme_css(preset, theme.FONT_FAMILIES["space-mono"], 1.0)
    found = set(re.findall(r"#[0-9a-fA-F]{6}", css))
    allowed = {preset.background, preset.surface, preset.surface_alt,
               preset.border, preset.foreground, preset.muted, preset.accent}
    assert found and found <= allowed
    assert "Space Mono" in css
    big = theme.theme_css(preset, "Sans", 2.0)
    assert "34.0pt" in big
