"""Static proof that nothing here can be enabled by installation.

These tests read the shipped files (unit, meson.build, config) and fail
if any of them gains an enable/start/autostart path. Also covers
`python -m xx_find --version`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Scaffold-owned files only: ref/ holds read-only reference trees and .git
# holds history, neither of which this gate may judge.
SCAFFOLD = [ROOT / "meson.build", ROOT / "server" / "Dockerfile",
            *(ROOT / "src").rglob("*"), *(ROOT / "data").rglob("*"),
            *(ROOT / "tests").rglob("*")]


def _code_text(path: Path) -> str:
    return "\n".join(
        line.split("#", 1)[0] for line in
        path.read_text(encoding="utf-8").splitlines())


UNIT = _code_text(ROOT / "data" / "systemd" / "xx-find-agent.service")
MESON = _code_text(ROOT / "meson.build").lower()


def test_unit_has_no_install_section():
    assert "[Install]" not in UNIT
    assert "WantedBy" not in UNIT
    assert "Alias=" not in UNIT


def test_meson_never_enables_or_starts():
    for banned in ("systemctl", "enable", "start", "preset", "symlink",
                   "autostart", "/etc/xdg", "dbus-1/services"):
        assert banned not in MESON, banned


def test_no_autostart_entries_shipped():
    hits = [p for p in SCAFFOLD if p.suffix == ".desktop"]
    assert hits == []
    units = [p for p in SCAFFOLD if p.suffix == ".service"]
    assert [p.name for p in units] == ["xx-find-agent.service"]


def test_no_nas_deployment_scripts():
    scripts = [ROOT / "meson.build", ROOT / "server" / "Dockerfile",
               *(p for p in SCAFFOLD if p.suffix in (".sh", ""))]
    for banned in ("scp ", "rsync", "tailscale up", "docker push",
                   "ssh "):
        for path in scripts:
            if path.is_file():
                text = path.read_text(encoding="utf-8",
                                      errors="replace").lower()
                assert banned not in text, (banned, path)


def test_version_flag(capsys):
    from xx_find import __main__
    assert __main__.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("xx-find ")


def test_version_subprocess():
    proc = subprocess.run(
        [sys.executable, "-m", "xx_find", "--version"],
        capture_output=True, text=True, cwd=ROOT, env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(ROOT / "src")})
    assert proc.returncode == 0
    assert proc.stdout.startswith("xx-find ")


def test_meson_installs_lane2_modules_without_enabling():
    text = (ROOT / "meson.build").read_text(encoding="utf-8")
    for module in ("src/xx_find/keystore.py", "src/xx_find/pairing.py"):
        assert module in text
    units = [p for p in SCAFFOLD if p.suffix == ".service"]
    assert [p.name for p in units] == ["xx-find-agent.service"]


def test_lane2_modules_have_no_enable_or_bus_paths():
    for name in ("agent.py", "config.py", "crypto.py", "keystore.py",
                 "pairing.py", "protocol.py", "server.py"):
        code = _code_text(ROOT / "src" / "xx_find" / name).lower()
        for banned in ("systemctl", "wantedby", "alias=", "autostart",
                       "preset", "symlink"):
            assert banned not in code, (banned, name)
    for name in ("keystore.py", "pairing.py"):
        code = _code_text(ROOT / "src" / "xx_find" / name).lower()
        for banned in ("serve_forever", "httpserver", "socket"):
            assert banned not in code, (banned, name)


def test_keystore_touches_no_bus_or_keyring_on_import():
    src = (ROOT / "src" / "xx_find" / "keystore.py").read_text(
        encoding="utf-8")
    top = [line for line in src.splitlines()
           if line.startswith("import ") or line.startswith("from ")]
    assert not any("secretstorage" in line or line.startswith("import gi")
                   or "dbus" in line for line in top)
    from xx_find import keystore
    assert keystore.MemoryKeystore().get("xx-find/device-token") is None


def test_meson_installs_lane3_providers_without_enabling():
    text = (ROOT / "meson.build").read_text(encoding="utf-8")
    assert "src/xx_find/providers.py" in text
    units = [p for p in SCAFFOLD if p.suffix == ".service"]
    assert [p.name for p in units] == ["xx-find-agent.service"]


def test_lane3_providers_never_enable_start_or_configure():
    code = _code_text(ROOT / "src" / "xx_find" / "providers.py").lower()
    for banned in ("systemctl", "wantedby", "alias=", "autostart",
                   "preset", "symlink", "/etc/xdg", "dbus-1/services",
                   "serve_forever", "httpserver"):
        assert banned not in code, banned
    src = (ROOT / "src" / "xx_find" / "providers.py").read_text(
        encoding="utf-8")
    for banned_call in ("'RegisterAgent'", '"RegisterAgent"',
                        "'UnregisterAgent'", '"UnregisterAgent"'):
        assert banned_call not in src, banned_call
    assert "open(" not in src
    assert "/etc/" not in src


def test_providers_import_touches_no_bus_or_keyring():
    src = (ROOT / "src" / "xx_find" / "providers.py").read_text(
        encoding="utf-8")
    top = [line for line in src.splitlines()
           if line.startswith("import ") or line.startswith("from ")]
    assert not any("secretstorage" in line for line in top)
    from xx_find import providers
    assert providers.GEOCLUE_DESKTOP_ID == "io.piercingxx.Find"
    candidates, cell = providers.parse_mmcli_location_get("", 0)
    assert (candidates, cell) == ([], None)


def test_agent_persists_counter_before_dispatch():
    src = (ROOT / "src" / "xx_find" / "agent.py").read_text(
        encoding="utf-8")
    assert "save_config" in src
    assert src.index("last_counter") < src.index("handler(cmd)")


def test_meson_installs_lane4_modules_without_enabling():
    text = (ROOT / "meson.build").read_text(encoding="utf-8")
    for module in ("src/xx_find/actions.py", "src/xx_find/schedule.py"):
        assert module in text
    units = [p for p in SCAFFOLD if p.suffix == ".service"]
    assert [p.name for p in units] == ["xx-find-agent.service"]


def test_lane4_modules_never_enable_start_or_configure():
    for name in ("actions.py", "schedule.py"):
        code = _code_text(ROOT / "src" / "xx_find" / name).lower()
        for banned in ("systemctl", "wantedby", "alias=", "autostart",
                       "preset", "symlink", "/etc/xdg", "dbus-1/services",
                       "serve_forever", "httpserver"):
            assert banned not in code, (banned, name)
    for name in ("actions.py", "schedule.py"):
        src = (ROOT / "src" / "xx_find" / name).read_text(encoding="utf-8")
        for banned_call in ("'RegisterAgent'", '"RegisterAgent"',
                            "'UnregisterAgent'", '"UnregisterAgent"'):
            assert banned_call not in src, (banned_call, name)
    for name in ("actions.py", "schedule.py"):
        code = _code_text(ROOT / "src" / "xx_find" / name).lower()
        assert "/etc/" not in code, name


def test_lane4_never_writes_mixer_volumes():
    for name in ("actions.py", "schedule.py"):
        code = (ROOT / "src" / "xx_find" / name).read_text(
            encoding="utf-8").lower()
        for banned in ("amixer", "alsamixer", "set-sink-volume",
                       "set-source-volume", "set-sink-mute",
                       "set-source-mute", "wpctl set-volume"):
            assert banned not in code, (banned, name)


def test_lane4_import_touches_no_bus_or_keyring():
    for name in ("actions.py", "schedule.py"):
        src = (ROOT / "src" / "xx_find" / name).read_text(encoding="utf-8")
        top = [line for line in src.splitlines()
               if line.startswith("import ") or line.startswith("from ")]
        assert not any("secretstorage" in line for line in top), name
        assert "bus_get_sync" not in "\n".join(top), name
    from xx_find import actions, schedule
    assert actions.FIND_APP_ID == "io.piercingxx.Find"
    assert actions.RINGER_EVENT == "phone-incoming-call"
    assert schedule.IDLE_POLL_S == 900.0
    assert schedule.ACTIVE_POLL_S == 360.0


def test_meson_installs_lane5_modules_without_enabling():
    text = (ROOT / "meson.build").read_text(encoding="utf-8")
    assert "src/xx_find/erase.py" in text
    assert "src/xx_find/schedule.py" in text
    units = [p for p in SCAFFOLD if p.suffix == ".service"]
    assert [p.name for p in units] == ["xx-find-agent.service"]


def test_lane5_modules_never_enable_start_or_configure():
    for name in ("erase.py", "schedule.py"):
        code = _code_text(ROOT / "src" / "xx_find" / name).lower()
        for banned in ("systemctl", "wantedby", "alias=", "autostart",
                       "preset", "/etc/xdg", "dbus-1/services",
                       "serve_forever", "httpserver"):
            assert banned not in code, (banned, name)
    for name in ("erase.py", "schedule.py"):
        src = (ROOT / "src" / "xx_find" / name).read_text(encoding="utf-8")
        for banned_call in ("'RegisterAgent'", '"RegisterAgent"',
                            "'UnregisterAgent'", '"UnregisterAgent"'):
            assert banned_call not in src, (banned_call, name)
    for name in ("erase.py", "schedule.py"):
        code = _code_text(ROOT / "src" / "xx_find" / name).lower()
        assert "/etc/" not in code, name


def test_lane5_import_touches_no_bus_or_keyring():
    src = (ROOT / "src" / "xx_find" / "erase.py").read_text(
        encoding="utf-8")
    top = [line for line in src.splitlines()
           if line.startswith("import ") or line.startswith("from ")]
    assert not any("secretstorage" in line or "dbus" in line
                   or line.startswith("import gi") for line in top)
    assert "bus_get_sync" not in "\n".join(top)
    assert "socket" not in "\n".join(top).lower()
    from xx_find import erase
    assert erase.validate_erase_confirmation("phone-1", "phone-1") is True
    assert erase.validate_erase_confirmation("phone-1", "other") is False


def test_erase_is_dry_run_by_default_with_guards():
    src = (ROOT / "src" / "xx_find" / "erase.py").read_text(
        encoding="utf-8")
    assert "dry_run" in src
    assert "is_safe_target" in src
    assert "FORBIDDEN_PREFIXES" in src
    code = _code_text(ROOT / "src" / "xx_find" / "erase.py")
    assert 'rmtree("/")' not in code
    assert 'rmtree("/usr")' not in code


def test_lane5_ring_hints_do_not_change_mixer_or_enable_paths():
    code = (ROOT / "src" / "xx_find" / "actions.py").read_text(
        encoding="utf-8").lower()
    for banned in ("amixer", "alsamixer", "set-sink-volume",
                   "set-source-volume", "wpctl set-volume"):
        assert banned not in code, banned
    assert '"full"' in (ROOT / "src" / "xx_find" / "actions.py").read_text(
        encoding="utf-8")


def test_meson_installs_lane6_modules_without_enabling():
    text = (ROOT / "meson.build").read_text(encoding="utf-8")
    for module in ("src/xx_find/decrypt.py", "src/xx_find/owner.js",
                   "src/xx-find-decrypt"):
        assert module in text, module
    units = [p for p in SCAFFOLD if p.suffix == ".service"]
    assert [p.name for p in units] == ["xx-find-agent.service"]
    assert (ROOT / "src" / "xx_find" / "owner.js").exists()


def test_lane6_modules_never_enable_start_or_configure():
    code = _code_text(ROOT / "src" / "xx_find" / "decrypt.py").lower()
    for banned in ("systemctl", "wantedby", "alias=", "autostart",
                   "preset", "/etc/xdg", "dbus-1/services",
                   "serve_forever", "httpserver"):
        assert banned not in code, banned
    src = (ROOT / "src" / "xx_find" / "decrypt.py").read_text(
        encoding="utf-8")
    for banned_call in ("'RegisterAgent'", '"RegisterAgent"',
                        "'UnregisterAgent'", '"UnregisterAgent"'):
        assert banned_call not in src, banned_call
    assert "/etc/" not in _code_text(
        ROOT / "src" / "xx_find" / "decrypt.py").lower()
    launcher = (ROOT / "src" / "xx-find-decrypt").read_text(
        encoding="utf-8").lower()
    for banned in ("systemctl", "wantedby", "autostart", "preset"):
        assert banned not in launcher, banned


def test_lane6_import_touches_no_bus_keyring_or_network():
    src = (ROOT / "src" / "xx_find" / "decrypt.py").read_text(
        encoding="utf-8")
    top = [line for line in src.splitlines()
           if line.startswith("import ") or line.startswith("from ")]
    assert not any("secretstorage" in line or "dbus" in line
                   or line.startswith("import gi") for line in top)
    assert "bus_get_sync" not in "\n".join(top)
    assert "socket" not in "\n".join(top).lower()
    from xx_find.decrypt import main
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("--help should exit 0")


def test_lane6_ui_has_no_trackers_or_plaintext_paths():
    code = (ROOT / "src" / "xx_find" / "server.py").read_text(
        encoding="utf-8").lower()
    for banned in ("googleapis", "gstatic", "maps.google",
                   "google.com", "fonts.google"):
        assert banned not in code, banned
    owner = (ROOT / "src" / "xx_find" / "owner.js").read_text(
        encoding="utf-8")
    assert "google" not in owner.lower()
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket",
                   "EventSource"):
        assert banned not in owner, banned
    from xx_find import server as server_mod
    assert "boxSealOpen" in server_mod._owner_js()
    assert "tile.openstreetmap.org" in server_mod._app_shell()
