"""Lane-2 round trip: owner + phone + server with fakes only.

Phone seals a location fix to the owner key, the server stores the
ciphertext it cannot read, and the owner unseals it. Owner-signed
commands travel the queue to the agent; replays and duplicates die.
"""

from __future__ import annotations

import json

import pytest

from xx_find import agent as agent_mod
from xx_find import config, crypto, keystore, pairing, protocol, server

NOW = 1_800_000_000


def _paired_phone(tmp_path):
    box_pub, box_secret = crypto.generate_owner_box_keypair()
    verify_hex, signing_hex = crypto.generate_owner_signing_keypair()
    path = tmp_path / "agent.conf"
    cfg = config.AgentConfig(
        enabled=True, server_url="http://100.64.0.1:8080",
        device_id="phone-1", owner_public_key_hex=verify_hex)
    config.save(cfg, path)
    cfg = config.load(path)
    saver = lambda c: config.save(c, path)  # noqa: E731
    return cfg, saver, signing_hex, box_pub, box_secret


def test_report_round_trip_ciphertext_only(tmp_path):
    cfg, _, _, box_pub, box_secret = _paired_phone(tmp_path)
    del cfg
    store = server.Store(tmp_path / "store.json")
    token = store.pair_device("phone-1")
    raw = protocol.encode_location_plaintext(47.37, 8.54, 12.5, NOW, 42,
                                             "geoclue")
    sealed = crypto.seal_report(raw, box_pub)
    envelope = protocol.build_report_envelope("phone-1", 1, NOW, sealed)
    assert store.submit_report("phone-1", envelope, NOW) == {"ok": True}
    dump = (tmp_path / "store.json").read_text(encoding="utf-8")
    assert "box" in dump
    assert '"lat"' not in dump and "47.37" not in dump
    assert token not in dump
    stored_box = protocol._b64decode(
        store.devices["phone-1"]["last_report"]["box"])
    assert protocol.decode_location_plaintext(
        crypto.unseal_report(stored_box, box_secret))["battery_pct"] == 42


def test_command_round_trip_with_fakes(tmp_path):
    cfg, saver, signing_hex, _, _ = _paired_phone(tmp_path)
    store = server.Store()
    store.pair_device("phone-1")
    cmd = protocol.build_command("phone-1", 1, NOW + 120, "lock")
    envelope = protocol.sign_envelope(cmd, signing_hex)
    assert store.queue_envelope("phone-1", envelope)["queued"] == 1
    with pytest.raises(server.ServerError):
        store.queue_envelope("phone-1", envelope)
    locks = []
    deps = agent_mod.AgentDeps(
        now=lambda: NOW,
        fetch_commands=lambda c: store.pending(c.device_id,
                                              c.last_counter),
        lock_now=lambda: locks.append("lock"), save_config=saver)
    handled = agent_mod.Agent(cfg, deps)
    summary = handled.poll_once()
    assert summary["status"] == "ok"
    assert summary["acted"] == [{"ok": True, "action": "lock"}]
    assert locks == ["lock"]
    assert handled.poll_once()["acted"] == []
    replay = handled.handle_command(envelope)
    assert replay["ok"] is False
    assert locks == ["lock"]


def test_pairing_binding_end_to_end():
    store = server.Store()
    token = store.pair_device("phone-1")
    verify_hex = crypto.generate_owner_signing_keypair()[0]
    text = pairing.build_pairing_payload("phone-1",
                                        "http://100.64.0.1:8080",
                                        verify_hex, token)
    bundle = pairing.parse_pairing_payload(text)
    assert store.check_device_token(bundle["device_id"],
                                    bundle["token"]) is True
    vault = keystore.MemoryKeystore()
    vault.put(keystore.ACCOUNT_DEVICE_TOKEN,
              bundle["token"].encode("utf-8"))
    vault.put(keystore.ACCOUNT_OWNER_VERIFY_KEY,
              bundle["verify_key"].encode("utf-8"))
    assert json.loads(text)["device_id"] == "phone-1"
    assert pairing.verify_short_code(
        bundle["token"], pairing.short_code(bundle["token"])) is True
