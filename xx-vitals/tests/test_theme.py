"""Headless smoke tests: XX-WM theme loading (no display needed)."""

import json

import pytest

from xx_vitals import theme


def test_all_preset_keys_resolve(tmp_path):
    cfg = tmp_path / "config.json"
    for key in theme.THEME_PRESETS:
        cfg.write_text(json.dumps({"theme": key}), encoding="utf-8")
        assert theme.resolve_preset(theme.load_xx_wm_config(cfg)).key == key


def test_unknown_theme_falls_back_to_amoled(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"theme": "nope"}), encoding="utf-8")
    assert theme.resolve_preset(theme.load_xx_wm_config(cfg)).key == "amoled"


def test_missing_and_corrupt_shell_config_fall_back():
    assert theme.load_xx_wm_config("/nonexistent/config.json") == {}
    assert theme.resolve_preset({}).key == "amoled"
    assert theme.resolve_font_family({}) == theme.FONT_FAMILIES["jetbrains-mono-nerd"]


def test_corrupt_json_is_tolerated(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{not json", encoding="utf-8")
    assert theme.load_xx_wm_config(cfg) == {}


def test_font_keys_match_shell(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"font": "space-mono"}), encoding="utf-8")
    data = theme.load_xx_wm_config(cfg)
    assert theme.resolve_font_family(data) == theme.FONT_FAMILIES["space-mono"]
    assert set(theme.FONT_FAMILIES) == {
        "system-light", "space-mono", "jetbrains-mono", "jetbrains-mono-nerd",
    }


def test_custom_font_family_honored(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps({"font": "custom", "custom_font_family": "My Font"}),
        encoding="utf-8",
    )
    assert theme.resolve_font_family(theme.load_xx_wm_config(cfg)) == "My Font"


def test_preset_css_uses_preset_colors():
    css = theme.preset_css(theme.THEME_PRESETS["paper"])
    assert "#F3EEE2" in css and "#151515" in css


def test_file_monitor_fires_on_shell_config_change(tmp_path):
    gi = pytest.importorskip("gi")
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    from xx_vitals.ui import shell

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"theme": "amoled"}), encoding="utf-8")
    fired: list[str] = []
    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(10, loop.quit)
    monitor = shell.watch_xx_wm_config(
        lambda: (fired.append("changed"), loop.quit()), path=cfg)
    assert monitor is not None
    assert isinstance(monitor, Gio.FileMonitor)
    GLib.timeout_add(100, lambda: cfg.write_text(
        json.dumps({"theme": "paper"}), encoding="utf-8") or False)
    loop.run()
    monitor.cancel()
    assert len(fired) >= 1 and all(item == "changed" for item in fired)


def test_directory_monitor_fires_on_atomic_replace(tmp_path):
    """Editors save atomically (temp + rename); the watch must survive that."""
    import os

    gi = pytest.importorskip("gi")
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    from xx_vitals.ui import shell

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"theme": "amoled"}), encoding="utf-8")
    fired: list[str] = []
    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(10, loop.quit)
    monitor = shell.watch_xx_wm_config(
        lambda: (fired.append("changed"), loop.quit()), path=cfg)
    assert monitor is not None
    assert isinstance(monitor, Gio.FileMonitor)

    def atomic_replace() -> bool:
        tmp = tmp_path / "config.json.tmp"
        tmp.write_text(json.dumps({"theme": "paper"}), encoding="utf-8")
        os.replace(tmp, cfg)
        return False

    GLib.timeout_add(100, atomic_replace)
    loop.run()
    monitor.cancel()
    assert len(fired) >= 1
    assert theme.resolve_preset(theme.load_xx_wm_config(cfg)).key == "paper"


def test_directory_monitor_ignores_other_files(tmp_path):
    gi = pytest.importorskip("gi")
    gi.require_version("Gio", "2.0")
    from gi.repository import GLib

    from xx_vitals.ui import shell

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"theme": "amoled"}), encoding="utf-8")
    fired: list[str] = []
    monitor = shell.watch_xx_wm_config(lambda: fired.append("changed"), path=cfg)
    assert monitor is not None
    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(3, loop.quit)
    GLib.timeout_add(100, lambda: (tmp_path / "other.json").write_text(
        "{}", encoding="utf-8") or False)
    loop.run()
    monitor.cancel()
    assert fired == []


def test_preset_switch_resolves_without_restart(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"theme": "amoled"}), encoding="utf-8")
    assert theme.resolve_preset(theme.load_xx_wm_config(cfg)).key == "amoled"
    cfg.write_text(json.dumps({"theme": "ocean"}), encoding="utf-8")
    assert theme.resolve_preset(theme.load_xx_wm_config(cfg)).key == "ocean"


def test_watch_without_callback_returns_monitor(tmp_path):
    gi = pytest.importorskip("gi")
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    from xx_vitals.ui import shell

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"theme": "ocean"}), encoding="utf-8")
    monitor = shell.watch_xx_wm_config(path=cfg)
    assert isinstance(monitor, Gio.FileMonitor)
    monitor.cancel()
