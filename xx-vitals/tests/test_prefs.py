"""Headless smoke tests: app config parsing (no display needed)."""

import json

from xx_vitals import prefs


def test_fresh_install_defaults(tmp_path):
    cfg = prefs.load(tmp_path / "config.json")
    assert cfg.units == "imperial"
    assert (cfg.steps_goal, cfg.effort_goal, cfg.weekly_effort_goal) == (10000, 90, 150)
    assert cfg.show_journal_tab and cfg.show_browse_tab and cfg.show_profile_tab
    assert cfg.server_url == ""


def test_saved_metric_choice_honored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"units": "metric"}), encoding="utf-8")
    assert prefs.load(path).units == "metric"


def test_invalid_values_keep_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"units": "stones", "steps_goal": -5, "height_metres": "tall"}),
        encoding="utf-8",
    )
    cfg = prefs.load(path)
    assert cfg.units == "imperial"
    assert cfg.steps_goal == 10000
    assert cfg.height_metres == 0.0


def test_round_trip(tmp_path):
    path = tmp_path / "config.json"
    cfg = prefs.AppConfig(steps_goal=8000, server_url="https://nas:8443")
    prefs.save(cfg, path)
    reloaded = prefs.load(path)
    assert reloaded.steps_goal == 8000
    assert reloaded.server_url == "https://nas:8443"
