from __future__ import annotations

import json
from pathlib import Path

from xx_captions import theme


def _write_shell_config(home: Path, payload: object) -> None:
    conf = home / ".config" / "xx-wm"
    conf.mkdir(parents=True, exist_ok=True)
    (conf / "config.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_shell_config_falls_back(tmp_path: Path) -> None:
    assert theme.read_xx_wm_theme_key(tmp_path) == theme.FALLBACK_THEME
    assert theme.resolve_palette(tmp_path).key == theme.FALLBACK_THEME


def test_known_key_resolves(tmp_path: Path) -> None:
    _write_shell_config(tmp_path, {"theme": "ocean"})
    palette = theme.resolve_palette(tmp_path)
    assert palette.key == "ocean"
    assert palette.foreground == "#edf4f7"


def test_unknown_key_falls_back(tmp_path: Path) -> None:
    _write_shell_config(tmp_path, {"theme": "neon-grass"})
    assert theme.resolve_palette(tmp_path).key == theme.FALLBACK_THEME


def test_corrupt_shell_config_falls_back(tmp_path: Path) -> None:
    shell = tmp_path / ".config" / "xx-wm"
    shell.mkdir(parents=True)
    (shell / "config.json").write_text("oops", encoding="utf-8")
    assert theme.resolve_palette(tmp_path).key == theme.FALLBACK_THEME


def test_css_uses_palette_not_hardcoded_colors() -> None:
    palette = theme.preset_for_key("paper")
    css = theme.caption_css(palette)
    assert palette.foreground in css
    assert palette.surface in css
    assert palette.muted in css
    for other in ("graphite", "ocean", "aura"):
        assert theme.preset_for_key(other).background not in css


def test_all_shell_keys_have_palettes() -> None:
    for key in ("amoled", "graphite", "forest", "ocean",
                "paper", "mist", "aura", "burgundy"):
        assert theme.preset_for_key(key).key == key
