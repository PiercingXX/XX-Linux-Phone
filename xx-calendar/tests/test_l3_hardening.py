"""L3 hardening: atomic config writes + theme follow (custom, watch, fallback)."""
import json
import os
import stat

from xx_calendar import app_config, theme
from xx_calendar.app_config import load_settings, save_settings
from xx_calendar.theme import (
    THEME_PRESETS,
    ThemeWatcher,
    css_for_theme,
    derive_custom_preset,
    fallback_theme,
    resolve_preset,
    theme_from_xxwm_config,
)


def _mode(path):
    return stat.S_IMODE(os.stat(str(path)).st_mode)


def test_save_is_atomic_0600_in_0700_dir(tmp_path):
    target = tmp_path / "sub" / "settings.json"
    os.makedirs(str(target.parent), mode=0o755)
    save_settings({"default_view": "WEEK"}, target)
    assert _mode(target) == 0o600
    assert _mode(target.parent) == 0o700
    assert load_settings(target)["default_view"] == "WEEK"
    assert list(tmp_path.rglob("*.tmp")) == []


def test_save_repairs_loose_modes(tmp_path):
    target = tmp_path / "settings.json"
    save_settings({}, target)
    os.chmod(str(target), 0o644)
    os.chmod(str(tmp_path), 0o755)
    save_settings({"default_view": "DAY"}, target)
    assert _mode(target) == 0o600
    assert _mode(tmp_path) == 0o700


def test_custom_derivation_uses_shell_shade_rule():
    derived = derive_custom_preset("#10261B")
    assert derived is not None
    assert derived.key == "custom"
    assert derived.background == "#10261B"
    assert derived.surface != derived.background
    assert derive_custom_preset("junk") is None
    assert derive_custom_preset("#12345") is None
    resolved = resolve_preset({"theme": "custom",
                               "custom_background": "#10261B"})
    assert resolved.key == "custom"
    assert resolve_preset({"theme": "custom",
                           "custom_background": "junk"}).key == "amoled"
    assert theme.theme_from_xxwm_config(
        {"theme": "custom", "custom_background": "#10261B"}).preset.key == "custom"


def test_custom_light_background_takes_paper_ink():
    derived = derive_custom_preset("#F3EEE2")
    assert derived is not None
    assert derived.foreground == THEME_PRESETS["paper"].foreground


def test_directory_watch_fires_on_atomic_replace(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme": "paper"}), encoding="utf-8")
    seen = []
    watcher = ThemeWatcher(path, seen.append)
    path.write_text(json.dumps({"theme": "forest"}), encoding="utf-8")
    assert watcher.poll() is True
    assert seen and seen[-1]["theme"] == "forest"
    assert watcher.poll() is False
    watcher.stop()


def test_directory_watch_ignores_other_files(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme": "paper"}), encoding="utf-8")
    watcher = ThemeWatcher(path, lambda _data: None)
    assert watcher._wanted(str(tmp_path / "other.json")) is False
    assert watcher._wanted(str(path)) is True
    watcher.stop()


def test_absent_shell_config_uses_libadwaita_fallback():
    resolved = theme_from_xxwm_config(None)
    assert resolved == fallback_theme()
    assert resolved.preset.key == "amoled"
    assert resolved.prefer_dark is True
    assert theme.adw_color_scheme_name(resolved) == "dark"
    light = theme.ResolvedTheme(preset=resolved.preset,
                                font_css=resolved.font_css,
                                text_scale=1.0, prefer_dark=False)
    assert theme.adw_color_scheme_name(light) == "light"


def test_css_paints_lists_entries_and_buttons():
    css = css_for_theme(theme_from_xxwm_config({"theme": "forest"}))
    assert "#10261B" in css
    for selector in ("listview", "listview row", "row:selected",
                     "entry", "button"):
        assert selector in css
    assert app_config.SETTINGS_FILE_NAME == "settings.json"
