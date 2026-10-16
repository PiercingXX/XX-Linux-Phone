"""Headless tests for settings parsing (no display, no GTK import)."""
import json

from xx_calendar import app_config
from xx_calendar.app_config import (
    DEFAULTS,
    dump_settings,
    load_settings,
    parse_settings,
    save_settings,
)


def test_defaults_are_quiet():
    settings = parse_settings({})
    assert settings["default_view"] == "SCHEDULE"
    assert settings["show_declined"] is False
    assert settings["heads_up"] is False
    assert settings["lock_screen_title"] is False
    assert settings["daily_agenda"] is False
    assert settings["hide_auto_added"] is True
    assert settings["dim_past"] is True
    assert settings["default_duration_min"] == 30
    assert settings["default_notification_min"] == 10
    assert settings["all_day_notify_hour"] == 18
    assert settings["all_day_notify_days_before"] == 1


def test_android_backup_keys_parse():
    snapshot = {
        "default_view": "MONTH",
        "start_day_of_week": "SUNDAY",
        "week_numbers": True,
        "density": "COMPACT",
        "text_size_scale": 1.5,
        "auto_added_filter_mode": "CALENDAR",
    }
    settings = parse_settings(snapshot)
    assert settings["default_view"] == "MONTH"
    assert settings["start_day_of_week"] == "SUNDAY"
    assert settings["week_numbers"] is True
    assert settings["density"] == "COMPACT"
    assert settings["auto_added_filter_mode"] == "CALENDAR"


def test_unknown_keys_ignored_bad_values_fall_back():
    settings = parse_settings({
        "default_view": "YEAR",
        "week_numbers": "yes",
        "default_duration_min": True,
        "text_size_scale": "big",
        "mystery_key": 1,
    })
    assert settings["default_view"] == DEFAULTS["default_view"]
    assert settings["week_numbers"] is False
    assert settings["default_duration_min"] == 30
    assert settings["text_size_scale"] == 1.0
    assert "mystery_key" not in settings


def test_non_dict_and_corrupt_inputs_yield_defaults(tmp_path):
    assert parse_settings(None) == DEFAULTS
    assert parse_settings([1, 2]) == DEFAULTS
    assert load_settings(tmp_path / "missing.json") == DEFAULTS
    corrupt = tmp_path / "settings.json"
    corrupt.write_text("{broken", encoding="utf-8")
    assert load_settings(corrupt) == DEFAULTS


def test_round_trip_through_disk(tmp_path):
    target = tmp_path / "sub" / "settings.json"
    saved = save_settings({"default_view": "WEEK", "bogus": 1}, target)
    assert saved == target
    loaded = load_settings(target)
    assert loaded["default_view"] == "WEEK"
    assert "bogus" not in loaded
    assert loaded["hide_auto_added"] is True


def test_dump_is_deterministic_json():
    first = dump_settings({"default_view": "DAY"})
    second = dump_settings({"default_view": "DAY", "extra": "ignored"})
    assert first == second
    assert json.loads(first)["default_view"] == "DAY"
    assert app_config.SETTINGS_FILE_NAME == "settings.json"
