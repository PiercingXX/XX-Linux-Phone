"""L3 hardening: atomic config writes + shared xx_ui theme follow."""
import json
import os
import stat

from xx_calendar import app_config
from xx_calendar.app_config import load_settings, save_settings


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


def test_shared_theme_covers_custom_and_fallback(tmp_path):
    from xx_ui import theme as xx_theme

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"theme": "custom",
                               "custom_background": "#10261B"}),
                   encoding="utf-8")
    resolved = xx_theme.load(cfg)
    assert resolved.background == "#10261B"
    assert resolved.key == "custom"
    cfg.write_text(json.dumps({"theme": "custom",
                               "custom_background": "junk"}),
                   encoding="utf-8")
    assert xx_theme.load(cfg).key == "amoled"
    assert xx_theme.load(tmp_path / "missing.json").key == "amoled"


def test_shared_css_paints_lists_entries_and_buttons():
    from xx_ui import css as xx_css
    from xx_ui import theme as xx_theme

    rendered = xx_css.render(xx_theme.load())
    assert "#000000" in rendered
    for selector in ("listview", "listview row", "row:selected",
                     "entry", "button"):
        assert selector in rendered
    assert app_config.SETTINGS_FILE_NAME == "settings.json"
