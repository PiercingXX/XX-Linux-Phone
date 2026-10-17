"""Prefs defaults mirror Android AppPrefs; speed/rewind clamp."""

from xx_audiobook import prefs as prefs_module
from xx_audiobook.prefs import Prefs


def test_defaults_match_android():
    prefs = Prefs()
    assert prefs.skip_seconds == 30
    assert prefs.skip_back_seconds == 30
    assert prefs.default_speed == 1.0
    assert prefs.auto_rewind_seconds == 2
    assert prefs.download_wifi_only is True
    assert prefs.last_item_id is None


def test_speed_and_rewind_clamp():
    prefs = prefs_module.clamp_prefs(Prefs(default_speed=9.0,
                                           auto_rewind_seconds=-4))
    assert prefs.default_speed == 3.0
    assert prefs.auto_rewind_seconds == 0
    prefs = prefs_module.clamp_prefs(Prefs(default_speed=0.1,
                                           auto_rewind_seconds=99))
    assert prefs.default_speed == 0.5
    assert prefs.auto_rewind_seconds == 30


def test_roundtrip_and_missing_file():
    loaded = prefs_module.load()
    assert loaded == Prefs()
    prefs_module.save(Prefs(skip_seconds=15, default_speed=1.5,
                            owner_user_id="u1"))
    loaded = prefs_module.load()
    assert loaded.skip_seconds == 15
    assert loaded.default_speed == 1.5
    assert loaded.owner_user_id == "u1"


def test_unknown_keys_ignored():
    path = prefs_module.paths_module.config_path()
    prefs_module.paths_module.ensure_parent_for(path)
    path.write_text('{"skip_seconds": 10, "nope": 1}', encoding="utf-8")
    assert prefs_module.load().skip_seconds == 10


def test_debug_override_is_env_only(monkeypatch):
    monkeypatch.delenv("XX_AUDIOBOOK_API_BASE", raising=False)
    assert prefs_module.debug_api_base() is None
    monkeypatch.setenv("XX_AUDIOBOOK_API_BASE", "http://127.0.0.1:8750/")
    assert prefs_module.debug_api_base() == "http://127.0.0.1:8750/"
