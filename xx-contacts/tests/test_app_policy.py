from xx_contacts.app_config import AppConfig, default_config_path
from xx_contacts.names import compose, split
from xx_contacts.groups import (
    chooser_list,
    is_creatable,
    reserved_kind,
    stored_name,
)
from xx_contacts.visibility import row_actions, show_on_list


def test_config_defaults_and_round_trip(tmp_path):
    path = str(tmp_path / "config.json")
    assert AppConfig.load(path) == AppConfig()
    cfg = AppConfig(has_phone_only=True, show_photos=True)
    cfg.save(path)
    assert AppConfig.load(path) == cfg


def test_config_ignores_bad_payload(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2", encoding="utf-8")
    assert AppConfig.load(str(path)) == AppConfig()
    assert default_config_path("/home/u").endswith("xx-contacts/config.json")


def test_name_split_and_compose():
    assert split("Ada Lovelace").given == "Ada"
    assert split("Ada Lovelace").family == "Lovelace"
    assert split("Ada").family is None
    assert split("  ") == split(None)
    assert compose("Ada", "Lovelace") == "Ada Lovelace"
    assert compose(None, "Lovelace") == "Lovelace"
    assert compose(None, None) == ""


def test_group_policy():
    assert reserved_kind("STAR") == "star"
    assert reserved_kind("blocked") == "blocked"
    assert reserved_kind("Friends") is None
    assert is_creatable("Friends")
    assert not is_creatable("Star")
    assert stored_name("block") == "Blocked"
    assert stored_name("Star") is None
    kinds = [c.kind for c in chooser_list(["Friends"])]
    assert kinds == ["star", "business", "blocked", "custom", "create"]


def test_visibility_and_row_actions():
    assert show_on_list(False, None)
    assert not show_on_list(True, None)
    assert show_on_list(True, "ada")
    assert "call" in row_actions(True)
    assert "call" not in row_actions(False)
