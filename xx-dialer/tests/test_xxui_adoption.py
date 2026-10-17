"""xx_ui adoption: the app holds no theme reader and no CSS of its own.

Styling comes from ``common/xx_ui`` (``xx_ui.apply`` once at startup,
``xx_ui.theme``/``xx_ui.css`` for reads). The old per-app theme module
and HeaderBar/ViewStack chrome are gone; these tests pin that so a
local copy never creeps back. Widget-free and headless-safe.
"""
import json
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "xx_dialer"


def _sources() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8")
            for path in SRC.glob("*.py")}


def test_no_app_theme_module():
    assert not (SRC / "theme.py").exists()


def test_no_local_theme_css():
    sources = _sources()
    for name, text in sources.items():
        assert "theme_css" not in text, name
        assert "apply_shell_theme" not in text, name
        assert "load_shell_theme" not in text, name


def test_no_headerbar_chrome():
    sources = _sources()
    for name, text in sources.items():
        assert "HeaderBar" not in text, name
        assert "ViewStack" not in text, name
        assert "ViewSwitcher" not in text, name
        assert "ToolbarView" not in text, name


def test_main_applies_shared_design_system():
    text = (SRC / "main.py").read_text(encoding="utf-8")
    assert "xx_ui.apply" in text
    assert "set_decorated(False)" in text


def test_shared_theme_reads_shell_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "xx-wm").mkdir(parents=True)
    (tmp_path / "xx-wm" / "config.json").write_text(
        json.dumps({"theme": "forest"}), encoding="utf-8")
    import xx_ui

    assert xx_ui.theme.load().key == "forest"
    assert "#10261B" in xx_ui.css.render(xx_ui.theme.load())
