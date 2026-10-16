"""Agent gate: no I/O until enabled AND paired; erase needs its own key."""

from __future__ import annotations

import pytest

from xx_find import agent as agent_mod
from xx_find import config, crypto, protocol

NOW = 1_800_000_000


def _paired(enabled=True):
    verify_hex, signing_hex = crypto.generate_owner_signing_keypair()
    cfg = config.AgentConfig(
        enabled=enabled, server_url="http://nas:8080",
        device_id="phone-1", owner_public_key_hex=verify_hex)
    return cfg, signing_hex


def _envelope(signing_hex, counter=1, cmd_type="locate", args=None,
              exp=NOW + 120):
    cmd = protocol.build_command("phone-1", counter, exp, cmd_type, args)
    return protocol.sign_envelope(cmd, signing_hex)


def test_disabled_agent_performs_no_io():
    cfg = config.AgentConfig()
    calls = []

    def _boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("must not be called")

    deps = agent_mod.AgentDeps(now=lambda: NOW, fetch_commands=_boom,
                              send_report=_boom, read_fix=_boom)
    summary = agent_mod.Agent(cfg, deps).poll_once()
    assert summary == {"status": "disabled", "acted": []}
    assert calls == []


def test_enabled_but_unpaired_performs_no_io():
    cfg = config.AgentConfig(enabled=True)
    seen = []
    deps = agent_mod.AgentDeps(
        now=lambda: NOW,
        fetch_commands=lambda *a: seen.append(a) or [])
    assert agent_mod.Agent(cfg, deps).poll_once()["status"] == "unpaired"
    assert seen == []


def test_bad_signature_commands_never_advance_counter():
    cfg, signing_hex = _paired()
    handled = agent_mod.Agent(cfg, agent_mod.AgentDeps(now=lambda: NOW))
    bad = _envelope(signing_hex)
    bad["sig"] = "AA" + bad["sig"][2:]
    assert handled.handle_command(bad)["ok"] is False
    assert cfg.last_counter == 0


def test_replay_does_not_reactuate():
    cfg, signing_hex = _paired()
    calls = []
    deps = agent_mod.AgentDeps(now=lambda: NOW,
                              lock_now=lambda: calls.append("lock"))
    handled = agent_mod.Agent(cfg, deps)
    envelope = _envelope(signing_hex, counter=1, cmd_type="lock")
    assert handled.handle_command(envelope)["ok"] is True
    assert handled.handle_command(envelope)["ok"] is False
    assert calls == ["lock"]


def test_erase_refused_without_separate_enable():
    cfg, signing_hex = _paired()
    erased = []
    deps = agent_mod.AgentDeps(
        now=lambda: NOW,
        erase_user_data=lambda: erased.append(True))
    handled = agent_mod.Agent(cfg, deps)
    first = _envelope(signing_hex, counter=1, cmd_type="erase",
                      args={"confirmation": "phone-1"})
    assert handled.handle_command(first)["ok"] is False
    assert erased == []
    cfg.erase_enabled = True
    second = _envelope(signing_hex, counter=2, cmd_type="erase",
                       args={"confirmation": "phone-1"})
    assert handled.handle_command(second)["ok"] is True
    assert erased == [True]


def test_erase_refused_without_typed_confirmation():
    cfg, signing_hex = _paired()
    cfg.erase_enabled = True
    deps = agent_mod.AgentDeps(now=lambda: NOW,
                              erase_user_data=lambda: None)
    handled = agent_mod.Agent(cfg, deps)
    envelope = _envelope(signing_hex, cmd_type="erase",
                         args={"confirmation": "yes definitely"})
    out = handled.handle_command(envelope)
    assert out["ok"] is False
    assert cfg.last_counter == 1
    assert handled.handle_command(envelope)["ok"] is False


def test_locate_reports_privacy_event_and_fix():
    cfg, signing_hex = _paired()
    events, reports = [], []
    deps = agent_mod.AgentDeps(
        now=lambda: NOW,
        read_fix=lambda: {"lat": 1.0, "lon": 2.0},
        send_report=lambda c, fix: reports.append((c.device_id, fix)),
        on_privacy_event=lambda kind, app: events.append((kind, app)))
    handled = agent_mod.Agent(cfg, deps)
    out = handled.handle_command(_envelope(signing_hex, cmd_type="locate"))
    assert out == {"ok": True, "action": "locate"}
    assert events == [("location", "xx-find-agent")]
    assert reports[0][0] == "phone-1"


def test_main_refuses_when_disabled(tmp_home, capsys):
    assert agent_mod.main([]) == 0
    assert "not running" in capsys.readouterr().err


def test_wrong_device_ignored():
    cfg, signing_hex = _paired()
    deps = agent_mod.AgentDeps(now=lambda: NOW)
    handled = agent_mod.Agent(cfg, deps)
    cmd = protocol.build_command("other-phone", 1, NOW + 60, "lock")
    out = handled.handle_command(protocol.sign_envelope(cmd, signing_hex))
    assert out["ok"] is False
    assert cfg.last_counter == 0


def _file_backed(tmp_path):
    from pathlib import Path
    path = Path(tmp_path) / "agent.conf"
    cfg, signing_hex = _paired()
    config.save(cfg, path)
    loaded = config.load(path)
    assert loaded.last_counter == 0
    saver = lambda c: config.save(c, path)  # noqa: E731
    return path, loaded, signing_hex, saver


def test_failed_command_consumes_counter_durably(tmp_path):
    path, cfg, signing_hex, saver = _file_backed(tmp_path)
    calls = []

    def _boom():
        calls.append("fix")
        raise RuntimeError("no fix")

    deps = agent_mod.AgentDeps(now=lambda: NOW, read_fix=_boom,
                              send_report=lambda c, f: None,
                              save_config=saver)
    handled = agent_mod.Agent(cfg, deps)
    envelope = _envelope(signing_hex, counter=1, cmd_type="locate")
    assert handled.handle_command(envelope)["ok"] is False
    assert calls == ["fix"]
    assert config.load(path).last_counter == 1
    assert handled.handle_command(envelope)["ok"] is False
    assert calls == ["fix"]


def test_crash_after_persist_does_not_rerun_on_restart(tmp_path):
    path, cfg, signing_hex, saver = _file_backed(tmp_path)
    order, locks = [], []

    def _recording_saver(current):
        order.append("persist")
        saver(current)

    def _crashing_lock():
        order.append("act")
        locks.append("lock")
        raise RuntimeError("injected crash after persist")

    deps = agent_mod.AgentDeps(now=lambda: NOW, lock_now=_crashing_lock,
                              save_config=_recording_saver)
    handled = agent_mod.Agent(cfg, deps)
    envelope = _envelope(signing_hex, counter=1, cmd_type="lock")
    assert handled.handle_command(envelope)["ok"] is False
    assert order == ["persist", "act"]
    assert config.load(path).last_counter == 1
    restarted = agent_mod.Agent(
        config.load(path),
        agent_mod.AgentDeps(now=lambda: NOW,
                            lock_now=lambda: locks.append("lock"),
                            save_config=lambda c: config.save(c, path)))
    assert restarted.handle_command(envelope)["ok"] is False
    assert locks == ["lock"]


def test_persist_failure_refuses_to_act():
    cfg, signing_hex = _paired()
    calls = []

    def _failing_saver(current):
        del current
        raise OSError("disk full")

    deps = agent_mod.AgentDeps(now=lambda: NOW,
                              lock_now=lambda: calls.append("lock"),
                              save_config=_failing_saver)
    handled = agent_mod.Agent(cfg, deps)
    out = handled.handle_command(
        _envelope(signing_hex, counter=1, cmd_type="lock"))
    assert out["ok"] is False and "persist" in out["error"]
    assert calls == []
    assert cfg.last_counter == 0


@pytest.mark.parametrize("cmd_type", sorted(protocol.COMMAND_TYPES))
def test_every_command_type_dispatches(cmd_type):
    cfg, signing_hex = _paired()
    if cmd_type == "erase":
        cfg.erase_enabled = True
        args = {"confirmation": "phone-1"}
    elif cmd_type == "message":
        args = {"text": "hi", "callback": ""}
    else:
        args = {}
    deps = agent_mod.AgentDeps(
        now=lambda: NOW, read_fix=lambda: {"lat": 1.0},
        send_report=lambda c, f: None, ring=lambda full_volume: None,
        show_message=lambda t, cb: None, lock_now=lambda: None,
        erase_user_data=lambda: None)
    out = agent_mod.Agent(cfg, deps).handle_command(
        _envelope(signing_hex, cmd_type=cmd_type, args=args))
    assert out == {"ok": True, "action": cmd_type}
