"""Config tests: defaults, round-trip, tolerant parsing."""
import json

from xx_dialer import config as c


def test_defaults_match_android_keys(tmp_path):
    data = c.load(home=tmp_path)
    assert data["enforcement_mode"] == "observing"
    assert data["unknown_window"] == {"start": 540, "end": 1020, "days": 127}
    assert data["business_window"] == {"start": 540, "end": 1140, "days": 127}
    assert data["repeat_caller_enabled"] is True


def test_round_trip(tmp_path):
    data = c.load(home=tmp_path)
    data["enforcement_mode"] = "enforcing"
    data["unknown_window"] = {"start": 480, "end": 1080, "days": 31}
    path = c.save(data, home=tmp_path)
    assert path == c.config_path(home=tmp_path)
    again = c.load(home=tmp_path)
    assert again["enforcement_mode"] == "enforcing"
    assert again["unknown_window"] == {"start": 480, "end": 1080, "days": 31}


def test_missing_file_gives_defaults(tmp_path):
    assert c.load(home=tmp_path)["stir_action"] == "block"


def test_corrupt_file_gives_defaults(tmp_path):
    path = c.config_path(home=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{nope", encoding="utf-8")
    assert c.load(home=tmp_path)["stir_action"] == "block"


def test_unknown_keys_ignored_and_invalid_enums_kept_default(tmp_path):
    path = c.config_path(home=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"nope_key": 1,
                                "enforcement_mode": "turbo",
                                "unknown_window": {"start": 9999}}),
                    encoding="utf-8")
    data = c.load(home=tmp_path)
    assert "nope_key" not in data
    assert data["enforcement_mode"] == "observing"
    assert data["unknown_window"]["start"] == 540


def test_save_writes_known_keys_only(tmp_path):
    path = c.save({"enforcement_mode": "enforcing", "extra": 1},
                  home=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["enforcement_mode"] == "enforcing"
    assert "extra" not in payload
    assert set(payload) == set(c.DEFAULTS)
