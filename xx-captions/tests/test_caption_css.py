"""Overlay chrome follows the xx_ui theme: tokens only, live reload.

Renders src/xx_captions/captions.css against every xx_ui preset and
pins the overlay contract from the brief: surface at 92% opacity,
radius 16, no hardcoded colors besides the danger red.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")

from xx_captions.overlay import load_ui_theme, render_app_css  # noqa: E402
from xx_ui import theme as ui_theme  # noqa: E402

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


def _write_shell_config(home: Path, payload: object) -> None:
    conf = home / ".config" / "xx-wm"
    conf.mkdir(parents=True, exist_ok=True)
    (conf / "config.json").write_text(json.dumps(payload), encoding="utf-8")


def test_css_uses_tokens_not_hardcoded_colors() -> None:
    for key, preset in ui_theme.PRESETS.items():
        loaded = ui_theme.Theme(
            key=preset.key, background=preset.background,
            surface=preset.surface, surface_alt=preset.surface_alt,
            border=preset.border, foreground=preset.foreground,
            muted=preset.muted, accent=preset.accent,
            font_family="x", scale=1.0, dark=True)
        css = render_app_css(loaded)
        assert preset.surface in css, key
        assert preset.border in css, key
        assert preset.muted in css, key
        assert "#ff6b6b" in css, key
        for other in ui_theme.PRESETS.values():
            if other.key != key:
                assert other.surface not in css, (key, other.key)


def test_css_pins_overlay_contract() -> None:
    preset = ui_theme.PRESETS["amoled"]
    loaded = ui_theme.Theme(
        key=preset.key, background=preset.background,
        surface=preset.surface, surface_alt=preset.surface_alt,
        border=preset.border, foreground=preset.foreground,
        muted=preset.muted, accent=preset.accent,
        font_family="x", scale=1.0, dark=True)
    css = render_app_css(loaded)
    assert "alpha(#111111, 0.92)" in css
    assert "border-radius: 16px" in css


def test_load_ui_theme_follows_shell_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert load_ui_theme(home).key == "amoled"
    _write_shell_config(home, {"theme": "ocean"})
    assert load_ui_theme(home).key == "ocean"
    assert load_ui_theme(home).foreground == "#edf4f7"
    _write_shell_config(home, {"theme": "neon-grass"})
    assert load_ui_theme(home).key == "amoled"
    shell = home / ".config" / "xx-wm" / "config.json"
    shell.write_text("oops", encoding="utf-8")
    assert load_ui_theme(home).key == "amoled"
