"""Config defaults: disabled until the owner opts in."""

from __future__ import annotations

from xx_find import config


def test_defaults_disabled():
    cfg = config.AgentConfig()
    assert cfg.enabled is False
    assert cfg.erase_enabled is False
    assert cfg.is_paired() is False
    assert cfg.is_operational() is False


def test_missing_file_yields_disabled(tmp_home):
    cfg = config.load(tmp_home / "nope" / "agent.conf")
    assert cfg.is_operational() is False


def test_corrupt_file_yields_disabled(tmp_home):
    target = tmp_home / "agent.conf"
    target.write_text("not [valid ini \x00\n", encoding="utf-8")
    assert config.load(target).is_operational() is False


def test_explicit_enable_does_not_imply_pairing():
    cfg = config.from_dict({"agent": {"enabled": True}})
    assert cfg.enabled is True
    assert cfg.is_operational() is False


def test_operational_only_when_enabled_and_paired():
    cfg = config.from_dict({"agent": {
        "enabled": True, "server_url": "http://100.x:8080",
        "device_id": "phone-1", "owner_public_key_hex": "ab" * 32}})
    assert cfg.is_operational() is True


def test_round_trip_preserves_disabled(tmp_home):
    target = tmp_home / "agent.conf"
    config.save(config.AgentConfig(), target)
    assert config.load(target).enabled is False
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_save_is_atomic_durable_and_private(tmp_home):
    target = tmp_home / "sub" / "agent.conf"
    cfg = config.AgentConfig(
        enabled=True, server_url="http://100.64.0.1:8080",
        device_id="phone-1", owner_public_key_hex="ab" * 32,
        last_counter=7)
    config.save(cfg, target)
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert not (target.parent / (target.name + ".tmp")).exists()
    assert config.load(target).last_counter == 7
    cfg.last_counter = 8
    config.save(cfg, target)
    assert config.load(target).last_counter == 8
    assert not (target.parent / (target.name + ".tmp")).exists()
