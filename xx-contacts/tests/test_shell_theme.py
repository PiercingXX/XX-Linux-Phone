from xx_contacts import shell_theme


def test_resolve_preset_from_shell_config(shell_config_file):
    data = shell_theme.load_shell_config(shell_config_file)
    assert shell_theme.resolve_preset(data).key == "forest"


def test_missing_shell_config_falls_back_to_amoled(tmp_path):
    data = shell_theme.load_shell_config(str(tmp_path / "absent.json"))
    assert data == {}
    assert shell_theme.resolve_preset(data).key == "amoled"


def test_unknown_preset_falls_back(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"theme": "neon"}', encoding="utf-8")
    data = shell_theme.load_shell_config(path)
    assert shell_theme.resolve_preset(data).key == "amoled"


def test_font_resolution_and_custom(tmp_path):
    data = shell_theme.load_shell_config("/nonexistent/config.json")
    assert shell_theme.resolve_font_family(data) == (
        shell_theme.FONT_FAMILIES["jetbrains-mono-nerd"])
    custom = {"font": "custom", "custom_font_family": "My Font"}
    assert shell_theme.resolve_font_family(custom) == "My Font"


def test_text_scale_clamped():
    assert shell_theme.resolve_text_scale({"text_size_scale": 9.0}) == 2.0
    assert shell_theme.resolve_text_scale({"text_size_scale": 0.1}) == 0.5


def test_css_mentions_preset_colors(shell_config_file):
    data = shell_theme.load_shell_config(shell_config_file)
    css = shell_theme.to_css(shell_theme.resolve_preset(data),
                             shell_theme.resolve_font_family(data))
    assert "#10261B" in css
    assert "Space Mono" in css
