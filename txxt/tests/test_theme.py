"""Theme behavior through the shared design system.

TxxT owns no theme reader, CSS, or appearance setting of its own: the
window installs ``xx_ui`` once at startup and the shell theme
(``~/.config/xx-wm/config.json``) is the single theme source. These
tests pin that contract: no manual theme mapping survives, the retired
appearance keys are gone from fresh configs, and existing configs
carrying them still load.
"""

import json
from pathlib import Path

import xx_ui
from txxt import main as main_mod
from txxt.settings import Settings, load


def test_all_eight_shell_presets_known():
    assert set(xx_ui.theme.preset_keys()) == {
        'amoled', 'graphite', 'forest', 'ocean',
        'paper', 'mist', 'aura', 'burgundy',
    }


def test_no_manual_theme_mapping_survives():
    assert not hasattr(main_mod, "manual_theme")
    assert not hasattr(main_mod, "MANUAL_FONTS")


def test_settings_carry_no_appearance_keys():
    fields = set(Settings.__dataclass_fields__)
    assert "auto_sync_theme" not in fields
    assert "theme_preset" not in fields
    assert "font_mode" not in fields
    assert Settings.defaults().to_dict() == Settings().to_dict()


def test_old_appearance_keys_still_load(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "lock_screen_privacy": "content",
        "auto_sync_theme": False,
        "theme_preset": "forest",
        "font_mode": "jetbrains-mono",
    }), encoding="utf-8")
    loaded = load(path)
    assert loaded.lock_screen_privacy == "content"
    assert not hasattr(loaded, "auto_sync_theme")
    assert not hasattr(loaded, "theme_preset")
    assert not hasattr(loaded, "font_mode")


def test_settings_page_has_no_appearance_section():
    import txxt.settings_page as page_mod

    assert not hasattr(page_mod, "FONT_MODE_OPTIONS")
    source = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "Follow the shell theme" not in source
    assert "auto_sync_theme" not in source
    assert "theme_preset" not in source
    assert "font_mode" not in source
