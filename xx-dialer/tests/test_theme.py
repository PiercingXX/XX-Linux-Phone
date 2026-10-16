"""Theme tests: preset table, shell-config loading, CSS output."""
import json

from xx_dialer import theme as t


def test_eight_presets_match_shell_keys():
    assert set(t.THEME_PRESETS) == {
        "amoled", "graphite", "forest", "ocean",
        "paper", "mist", "aura", "burgundy",
    }


def test_preset_backgrounds_match_shell():
    assert t.THEME_PRESETS["amoled"]["background"] == "#000000"
    assert t.THEME_PRESETS["graphite"]["background"] == "#111827"
    assert t.THEME_PRESETS["burgundy"]["background"] == "#2A1018"


def test_font_families_match_shell():
    assert set(t.FONT_FAMILIES) == {
        "system-light", "space-mono", "jetbrains-mono",
        "jetbrains-mono-nerd",
    }


def _write_shell_config(tmp_path, payload):
    path = tmp_path / ".config" / "xx-wm"
    path.mkdir(parents=True)
    (path / "config.json").write_text(json.dumps(payload), encoding="utf-8")


def test_loads_shell_theme(tmp_path):
    _write_shell_config(tmp_path, {"theme": "forest",
                                   "font": "space-mono",
                                   "text_size_scale": 1.2})
    theme = t.load_shell_theme(home=tmp_path)
    assert theme.preset == "forest"
    assert theme.background == "#10261B"
    assert theme.font_family == "Space Mono, Monospace"
    assert theme.text_scale == 1.2
    assert theme.dark is True


def test_missing_shell_config_falls_back(tmp_path):
    theme = t.load_shell_theme(home=tmp_path)
    assert theme.preset == t.DEFAULT_PRESET
    assert theme.background == "#000000"


def test_unknown_theme_falls_back(tmp_path):
    _write_shell_config(tmp_path, {"theme": "neon"})
    assert t.load_shell_theme(home=tmp_path).preset == t.DEFAULT_PRESET


def test_corrupt_shell_config_falls_back(tmp_path):
    _write_shell_config(tmp_path, "{not json")
    assert t.load_shell_theme(home=tmp_path).preset == t.DEFAULT_PRESET


def test_custom_background_used(tmp_path):
    _write_shell_config(tmp_path, {"theme": "custom",
                                   "custom_background": "#123456"})
    assert t.load_shell_theme(home=tmp_path).background == "#123456"


def test_css_uses_preset_colors(tmp_path):
    _write_shell_config(tmp_path, {"theme": "paper"})
    css = t.theme_css(t.load_shell_theme(home=tmp_path))
    assert "#F3EEE2" in css
    assert "#151515" in css
    assert "000000" not in css.replace("#000000", "")
