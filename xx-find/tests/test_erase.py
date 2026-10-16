"""Lane 5 erase: dry-run wipe planning, guards, double gate.

Nothing here deletes outside `tmp_path`: the plan is the only mode the
suite exercises for discovery, and the one real-wipe test runs inside
`tmp_path` with `dry_run=False` passed explicitly.
"""

from __future__ import annotations

from pathlib import Path

from xx_find import agent as agent_mod
from xx_find import config, crypto, erase, protocol

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000


def _paired():
    verify_hex, signing_hex = crypto.generate_owner_signing_keypair()
    cfg = config.AgentConfig(
        enabled=True, server_url="http://nas:8080",
        device_id="phone-1", owner_public_key_hex=verify_hex)
    return cfg, signing_hex


def test_confirmation_repeats_device_id_verbatim():
    assert erase.validate_erase_confirmation("phone-1", "phone-1") is True
    assert erase.validate_erase_confirmation("phone-1", "Phone-1") is False
    assert erase.validate_erase_confirmation("phone-1", "phone-1 ") is False
    assert erase.validate_erase_confirmation("phone-1", "") is False
    assert erase.validate_erase_confirmation("", "") is False
    assert erase.validate_erase_confirmation(None, "phone-1") is False


def test_plan_lists_xx_dirs_and_browser_profiles_scoped_to_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    targets = erase.plan_erase(home=home)
    dirs = [t["path"] for t in targets if t["kind"] == "dir"]
    assert str(home / ".config" / "xx-wm") in dirs
    assert str(home / ".config" / "xx-find") in dirs
    assert str(home / ".local" / "share" / "xx-wm") in dirs
    assert str(home / ".local" / "state" / "xx-find") in dirs
    assert str(home / ".cache" / "xx-wm") in dirs
    assert str(home / ".mozilla" / "firefox") in dirs
    assert str(home / ".config" / "chromium") in dirs
    for path in dirs:
        assert Path(path).is_absolute()
        assert str(home) in path
    assert not any(p == str(home) for p in dirs)
    assert not any(p in ("/", "/usr", "/etc", "/media", "/mnt",
                        "/run/media") for p in dirs)


def test_plan_reports_existence_without_deleting(tmp_path):
    home = tmp_path / "home"
    keep = home / ".config" / "xx-wm"
    keep.mkdir(parents=True)
    marker = keep / "config.json"
    marker.write_text("{}", encoding="utf-8")
    targets = erase.plan_erase(home=home)
    by_path = {t["path"]: t for t in targets if t["kind"] == "dir"}
    assert by_path[str(keep)]["exists"] is True
    assert by_path[str(home / ".config" / "xx-find")]["exists"] is False
    assert marker.exists()
    kinds = {t["kind"] for t in targets}
    assert {"dir", "keystore-account", "keyring-item"} <= kinds
    text = erase.format_plan(targets)
    assert "per-item Delete" in text
    assert "never rm" in text


def test_guards_refuse_os_media_and_symlink_escape(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    for bad in ("/", "/usr", "/etc/passwd", "/boot", "/media/sd",
                "/mnt/nas", "/run/media/user/sd", str(home.parent),
                "/home/other-user/.config"):
        assert erase.is_safe_target(bad, home) is False, bad
    assert erase.is_safe_target(str(home), home) is False
    assert erase.is_safe_target(str(home / ".config" / "xx-wm"),
                                home) is True
    link = home / "evil"
    try:
        link.symlink_to("/etc")
    except OSError:
        return
    assert erase.is_safe_target(link, home) is False
    assert link.exists() or link.is_symlink()


def test_dry_run_action_deletes_nothing(tmp_path):
    home = tmp_path / "home"
    keep = home / ".local" / "share" / "xx-wm"
    keep.mkdir(parents=True)
    (keep / "contacts.json").write_text("{}", encoding="utf-8")
    run = erase.build_erase_action(home_factory=lambda: home)
    targets = run()
    assert (keep / "contacts.json").exists()
    assert any(t["kind"] == "dir" and t["exists"] for t in targets)


def test_real_wipe_stays_inside_tmp_home(tmp_path):
    home = tmp_path / "home"
    victim = home / ".cache" / "xx-wm"
    victim.mkdir(parents=True)
    (victim / "weather.json").write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    deleted = []

    class _Store:
        def delete(self, account):
            deleted.append(account)

    run = erase.build_erase_action(home_factory=lambda: home,
                                   keystore=_Store(), dry_run=False)
    run()
    assert not victim.exists()
    assert outside.read_text(encoding="utf-8") == "keep"
    assert "xx-find/device-token" in deleted


def test_real_wipe_purges_collection_per_item(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    removed = []

    class _Item:
        def delete(self):
            removed.append(True)

    class _Collection:
        def search_items(self, attrs):
            assert attrs == {}
            return [_Item(), _Item()]

    run = erase.build_erase_action(
        home_factory=lambda: home, collection_factory=_Collection,
        dry_run=False)
    run()
    assert removed == [True, True]


def test_plan_lists_every_suite_app_dir(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    targets = erase.plan_erase(home=home)
    dirs = [t["path"] for t in targets if t["kind"] == "dir"]
    for rel in (".config/txxt", ".local/share/txxt", ".cache/txxt",
                ".config/xx-email", ".local/share/xx-email",
                ".local/state/xx-email", ".cache/xx-email",
                ".config/xx-vitals", ".local/share/xx-vitals",
                ".local/state/xx-vitals", ".cache/xx-vitals"):
        assert str(home / rel) in dirs, rel
    for app in ("xx-contacts", "xx-dialer", "xx-calendar", "xx-share",
                "xx-captions"):
        for root in (".config", ".local/share", ".local/state",
                     ".cache"):
            assert str(home / root / app) in dirs, (app, root)
    for path in dirs:
        assert erase.is_safe_target(path, home) is True, path
    details = " ".join(t.get("detail", "") for t in targets
                       if t["kind"] == "dir")
    for note in ("MMS", "drafts", "outbox", "photos", "transcripts"):
        assert note in details, note


def test_plan_keyring_items_use_app_schemas(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    targets = erase.plan_erase(home=home)
    items = {t["account"]: t["detail"] for t in targets
             if t["kind"] == "keyring-item"}
    assert erase.EMAIL_KEYRING_LABEL in items
    assert erase.VITALS_KEYRING_LABEL in items
    assert "io.piercingxx.XXEmail" in items[erase.EMAIL_KEYRING_LABEL]
    assert "account" in items[erase.EMAIL_KEYRING_LABEL]
    assert "io.piercingxx.XXVitals" in items[erase.VITALS_KEYRING_LABEL]
    assert "host" in items[erase.VITALS_KEYRING_LABEL]
    assert erase.EMAIL_KEYRING_SCHEMA == {
        "application": "io.piercingxx.XXEmail", "account": "<email>"}
    assert erase.VITALS_KEYRING_SCHEMA == {
        "application": "io.piercingxx.XXVitals", "host": "<nas-host>"}


def test_plan_todo_lines_are_runtime_output_not_comments(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    text = erase.format_plan(erase.plan_erase(home=home))
    todos = [line for line in text.splitlines()
             if line.startswith("TODO owner-review:")]
    assert len(todos) == len(erase.UNVERIFIED_SUITE_APPS) == 5
    for app in erase.UNVERIFIED_SUITE_APPS:
        assert any(app in line for line in todos), app
    for line in (ROOT / "src" / "xx_find" / "erase.py").read_text(
            encoding="utf-8").splitlines():
        stripped = line.split("#", 1)
        assert not ("TODO" in stripped[1] if len(stripped) > 1 else False), \
            line


def test_real_wipe_removes_suite_app_data(tmp_path):
    home = tmp_path / "home"
    victim = home / ".local" / "share" / "txxt"
    victim.mkdir(parents=True)
    (victim / "txxt.db").write_text("{}", encoding="utf-8")
    keep = home / ".local" / "share" / "xx-email"
    keep.mkdir(parents=True)
    run = erase.build_erase_action(home_factory=lambda: home,
                                   dry_run=False)
    run()
    assert not victim.exists()
    assert not keep.exists()


def test_erase_command_still_needs_double_gate():
    cfg, signing_hex = _paired()
    calls = []
    deps = agent_mod.AgentDeps(
        now=lambda: NOW,
        erase_user_data=erase.build_erase_action(
            home_factory=lambda: Path("/nonexistent-xx-find-test-home")))
    handled = agent_mod.Agent(cfg, deps)
    cmd = protocol.build_command("phone-1", 1, NOW + 120, "erase",
                                 {"confirmation": "phone-1"})
    assert handled.handle_command(
        protocol.sign_envelope(cmd, signing_hex))["ok"] is False
    assert calls == []
    cfg.erase_enabled = True
    cmd = protocol.build_command("phone-1", 2, NOW + 120, "erase",
                                 {"confirmation": "phone-1"})
    out = handled.handle_command(protocol.sign_envelope(cmd, signing_hex))
    assert out == {"ok": True, "action": "erase"}
