"""NetworkManager gating: pure classify + fake NM over D-Bus.

The pure matrix mirrors data/repo/NetworkMonitor.kt (VPN/Tailscale is
not metering by itself; captive-unvalidated without VPN is offline).
The fake-NM cases run tests/nm_driver.py under dbus-run-session and
prove probe_nm() reads State/Metered/Connectivity over D-Bus with a
real GLib.VariantType reply type (never a plain string).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from xx_audiobook import download as download_module

Facts = download_module.Facts
NetState = download_module.NetState

DRIVER = Path(__file__).parent / "nm_driver.py"
SRC_DIR = str(Path(__file__).parent.parent / "src")


def test_classify_offline_without_internet():
    assert download_module.classify(
        Facts(False, True, True)) == NetState.Offline


def test_classify_captive_unvalidated_is_offline_unless_vpn():
    assert download_module.classify(
        Facts(True, False, True)) == NetState.Offline
    assert download_module.classify(
        Facts(True, False, True, vpn=True)) == NetState.Online


def test_classify_metered_needs_unmetered_flag():
    assert download_module.classify(
        Facts(True, True, False)) == NetState.Metered
    assert download_module.classify(
        Facts(True, True, True)) == NetState.Online


def test_classify_nm_maps_metered_and_state():
    assert download_module.classify_nm(70, 2, 4) == NetState.Online
    assert download_module.classify_nm(70, 1, 4) == NetState.Metered
    assert download_module.classify_nm(20, 2, 4) == NetState.Offline
    assert download_module.classify_nm(70, 2, 1) == NetState.Offline
    assert download_module.classify_nm(70, 1, 0, vpn=True) == (
        NetState.Metered)


def test_can_download_respects_wifi_only():
    assert download_module.can_download(NetState.Online, True) is None
    assert download_module.can_download(
        NetState.Metered, True) == download_module.WAITING_FOR_WIFI
    assert download_module.can_download(
        NetState.Metered, False) is None
    assert download_module.can_download(
        NetState.Offline, False) == download_module.OFFLINE_MESSAGE


def _run_mode(mode: str) -> str:
    env = dict(os.environ)
    env["SRC_DIR"] = SRC_DIR
    env["MODE"] = mode
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(DRIVER)],
        capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode == 0, (
        f"driver failed ({mode}):\n{proc.stdout}\n{proc.stderr}")
    assert "PASS" in proc.stdout
    for line in proc.stdout.splitlines():
        if line.startswith("STATE:"):
            return line[len("STATE:"):]
    raise AssertionError(f"no STATE line ({mode}):\n{proc.stdout}")


@pytest.mark.skipif(shutil.which("dbus-run-session") is None,
                    reason="dbus-run-session is missing")
def test_fake_nm_metered_and_online_over_dbus():
    assert _run_mode("metered") == "Metered"
    assert _run_mode("online") == "Online"
    assert _run_mode("offline") == "Offline"
