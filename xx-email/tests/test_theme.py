"""xx_ui adoption contract for xx-email (headless, no display).

The app owns no theme reader and no theme CSS: one xx_ui.apply() at
startup paints every preset, live shell-config switches restyle the
open window, and the only app stylesheet (email.css) uses theme
tokens — never hardcoded colors.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "xx_email"
COMMON = Path(__file__).resolve().parent.parent.parent / "common"


def _xx_ui_mods():
    """Load xx_ui.theme/css without the Gtk-dependent package init.

    The suite stays gi-free in-process (other tests assert that), so
    bypass xx_ui/__init__ (which pulls Gtk widgets) with a stub
    package pointing at the real files. Drivers import the full
    package in subprocesses with a display.
    """
    import sys
    import types

    before = set(sys.modules)
    if "xx_ui" not in sys.modules:
        pkg = types.ModuleType("xx_ui")
        pkg.__path__ = [str(COMMON / "xx_ui")]
        sys.modules["xx_ui"] = pkg
    try:
        from xx_ui import css as css_mod
        from xx_ui import theme as theme_mod
        return (theme_mod, css_mod)
    finally:
        for name in [mod for mod in sys.modules
                     if mod not in before and mod != "xx_ui"]:
            del sys.modules[name]


def test_no_local_theme_reader_or_css():
    assert not (SRC / "theme.py").exists(), \
        "app theme reader must stay deleted (use xx_ui.theme)"
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from xx_email import theme" not in text, path
        assert "theme_mod." not in text, path


def test_startup_applies_xx_ui_once():
    text = (SRC / "main.py").read_text(encoding="utf-8")
    assert text.count("xx_ui.apply(") == 1
    assert "_apply_theme" not in text
    assert "_watch_shell_config" not in text
    assert "set_decorated(False)" in text


def test_app_css_uses_tokens_only():
    css = (SRC / "email.css").read_text(encoding="utf-8")
    assert css.strip(), "email.css must exist and not be empty"
    found = set(re.findall(r"#[0-9a-fA-F]{3,8}", css))
    assert not found, f"hardcoded colors in email.css: {found}"
    assert "email-unread-title" in css
    assert "email-dot" in css


def test_design_system_covers_every_required_theme():
    theme_mod, _css_mod = _xx_ui_mods()

    for key in ("amoled", "paper", "forest", "aura"):
        assert key in theme_mod.preset_keys(), key
    active = theme_mod.load()
    assert active.key == "amoled"


def test_design_css_paints_lists_without_white():
    theme_mod, css_mod = _xx_ui_mods()

    active = theme_mod.load()
    rendered = css_mod.render(active)
    assert "listview" in rendered
    assert "xx-row" in rendered
    found = set(re.findall(r"#[0-9a-fA-F]{6}", rendered))
    allowed = {active.background, active.surface, active.surface_alt,
               active.border, active.foreground, active.muted,
               active.accent, theme_mod.DANGER_RED,
               theme_mod.ON_DANGER_FG}
    assert found and found <= allowed, found - allowed
