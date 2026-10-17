"""Theme contract: the app owns no theme reader or CSS of its own.

All theming comes from the shared xx_ui design system: one apply() at
startup, colors only through xx_ui.theme tokens, live switches through
xx_ui.on_change with no restart. (Replaces the old local theme.py, deleted
in the xx_ui restyle.)
"""

import json
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "xx_vitals"


def test_no_local_theme_reader_or_css():
    assert not (SRC / "theme.py").exists()
    shell_src = (SRC / "ui" / "shell.py").read_text(encoding="utf-8")
    assert "xx_ui.apply" in shell_src
    assert "preset_css" not in shell_src
    assert "apply_xx_wm_theme" not in shell_src
    assert "watch_xx_wm_config" not in shell_src
    for name in ("home.py", "journal.py", "browse.py", "profile.py", "entry.py"):
        src = (SRC / "ui" / name).read_text(encoding="utf-8")
        assert "preset_css" not in src, name
        assert "THEME_PRESETS" not in src, name


def test_all_preset_keys_resolve(tmp_path, monkeypatch):
    from xx_ui import theme

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for key in theme.THEME_PRESETS:
        (tmp_path / "xx-wm").mkdir(exist_ok=True)
        (tmp_path / "xx-wm" / "config.json").write_text(
            json.dumps({"theme": key}), encoding="utf-8")
        assert theme.load().key == key


def test_unknown_missing_and_corrupt_config_fall_back(tmp_path, monkeypatch):
    from xx_ui import theme

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "xx-wm").mkdir(exist_ok=True)
    cfg = tmp_path / "xx-wm" / "config.json"
    cfg.write_text(json.dumps({"theme": "nope"}), encoding="utf-8")
    assert theme.load().key == "amoled"
    cfg.write_text("{not json", encoding="utf-8")
    assert theme.load().key == "amoled"
    cfg.unlink()
    assert theme.load().key == "amoled"


def test_render_paints_lists_from_preset(tmp_path, monkeypatch):
    from xx_ui import css, theme

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    rendered = css.render(theme.load())
    assert "listview" in rendered
    assert theme.THEME_PRESETS["amoled"].background in rendered


def test_live_switch_restyles_without_restart(tmp_path, monkeypatch):
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from xx_ui import css

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "xx-wm").mkdir(exist_ok=True)
    cfg = tmp_path / "xx-wm" / "config.json"
    cfg.write_text(json.dumps({"theme": "amoled"}), encoding="utf-8")
    seen: list = []
    unsubscribe = css.on_change(seen.append)
    try:
        assert css.reload().key == "amoled"
        cfg.write_text(json.dumps({"theme": "paper"}), encoding="utf-8")
        assert css.reload().key == "paper"
    finally:
        unsubscribe()
    assert [t.key for t in seen] == ["amoled", "paper"]


def test_cairo_widgets_redraw_on_theme_change():
    for name in ("home.py", "journal.py", "browse.py"):
        src = (SRC / "ui" / name).read_text(encoding="utf-8")
        assert "xx_ui.theme.load()" in src, name
        assert "xx_ui.on_change" in src, name
