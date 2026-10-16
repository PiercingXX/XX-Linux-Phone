"""Headless smoke tests for config parsing (no display, no GI imports)."""

import json
import sys

from xx_email import config


def test_pure_module_imports_no_gi():
    assert "gi" not in sys.modules


def test_defaults_match_android_settings():
    cfg = config.AppConfig()
    assert cfg.undo_seconds == 10
    assert cfg.sync_minutes == 15
    assert (cfg.swipe_left, cfg.swipe_right) == ("ARCHIVE", "DELETE")
    assert cfg.notifications_enabled is True
    assert cfg.remote_images is False
    assert cfg.send_and_archive is False
    assert cfg.client_id == ""
    assert cfg.last_account == ""


def test_missing_file_loads_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "absent.json"))
    assert config.load() == config.AppConfig()


def test_invalid_file_loads_defaults(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{nope", encoding="utf-8")
    monkeypatch.setenv(config.CONFIG_ENV, str(path))
    assert config.load() == config.AppConfig()


def test_round_trip_preserves_values(tmp_path):
    path = tmp_path / "config.json"
    cfg = config.AppConfig(client_id="x.apps.googleusercontent.com",
                           undo_seconds=20, swipe_left="STAR",
                           swipe_right="SNOOZE", theme_mode="dark",
                           sync_minutes=30, remote_images=True,
                           send_and_archive=True,
                           last_account="me@example.com")
    config.save(cfg, path)
    assert config.load(path) == cfg


def test_unknown_keys_ignored_and_types_defaulted(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"future_key": 1, "undo_seconds": "long",
                                "swipe_left": "YEET", "theme_mode": "neon",
                                "notifications_enabled": "yes"}),
                    encoding="utf-8")
    cfg = config.load(path)
    assert cfg.undo_seconds == 10
    assert cfg.swipe_left == "ARCHIVE"
    assert cfg.theme_mode == "follow"
    assert cfg.notifications_enabled is True


def test_clamps_and_floors():
    assert config.from_dict({"undo_seconds": 3}).undo_seconds == 5
    assert config.from_dict({"undo_seconds": 99}).undo_seconds == 30
    assert config.from_dict({"sync_minutes": 5}).sync_minutes == 15
    assert config.from_dict({"sync_minutes": 60}).sync_minutes == 60
    assert config.from_dict([1, 2]) == config.AppConfig()


def test_save_is_atomic(tmp_path):
    path = tmp_path / "config.json"
    config.save(config.AppConfig(), path)
    leftovers = list(tmp_path.glob("config.json.*.tmp"))
    assert path.is_file() and not leftovers
    assert json.loads(path.read_text(encoding="utf-8"))["undo_seconds"] == 10


def test_xdg_dirs_honored(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv(config.CONFIG_ENV, raising=False)
    assert config.config_path() == tmp_path / "cfg" / "xx-email" / "config.json"
    assert config.data_dir() == tmp_path / "data" / "xx-email"
    assert config.cache_dir() == tmp_path / "cache" / "xx-email"
    assert config.state_dir() == tmp_path / "state" / "xx-email"
