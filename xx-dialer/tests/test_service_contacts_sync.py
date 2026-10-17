"""Service-process contacts sync against the real xx-contacts service.

Starts the real ``xx-contacts`` service and the real dialer
``--service`` (the only dialer process running — no UI), retags a
contact with ``SetTags`` while just the service is up, and asserts the
next ``CheckNumber`` verdicts reflect the new tier within 2 s. This
proves the service process keeps its own cache warm instead of
relying on the GUI's ``ContactsClient``.

Skipped when ``dbus-run-session`` or ``ref/xx-contacts`` is missing.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REF_SRC = next(
    (p for p in (Path(os.environ.get("XX_CONTACTS_SRC", "/nonexistent")),
                 ROOT / "ref" / "xx-contacts" / "src",
                 ROOT.parent / "xx-contacts" / "src")
     if (p / "xx_contacts" / "__main__.py").exists()),
    ROOT / "ref" / "xx-contacts" / "src")
DIALER_SRC = ROOT / "src"

_HELPER = r'''
import os
import sys
import time
import subprocess
from pathlib import Path

dialer_src, ref_src, home_dir = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, dialer_src)
sys.path.insert(0, os.path.join(
    os.path.dirname(dialer_src), "..", "common"))
home = Path(home_dir)

from gi.repository import GLib, Gio

from xx_dialer import config as app_config

CONTACTS_BUS = "io.piercingxx.Contacts"
CONTACTS_PATH = "/io/piercingxx/Contacts"
CONTACTS_IFACE = "io.piercingxx.Contacts1"
DIALER_BUS = "io.piercingxx.XXDialer.Policy"
DIALER_PATH = "/io/piercingxx/XXDialer/Policy"
DIALER_IFACE = "io.piercingxx.XXDialer.Policy1"
NUMBER = "+15550100200"

settings = app_config.load(home)
settings["enforcement_mode"] = "enforcing"
app_config.save(settings, home)

env = dict(os.environ)
env["HOME"] = str(home)

contacts_env = dict(env, PYTHONPATH=ref_src)
contacts = subprocess.Popen(
    [sys.executable, "-m", "xx_contacts", "--service"], env=contacts_env)
dialer_env = dict(
    env,
    PYTHONPATH=os.pathsep.join(
        (str(dialer_src),
         os.path.join(os.path.dirname(str(dialer_src)),
                      "..", "common"))))
dialer = subprocess.Popen(
    [sys.executable, "-m", "xx_dialer", "--service"], env=dialer_env)
try:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def contacts_call(method, args, sig):
        return bus.call_sync(
            CONTACTS_BUS, CONTACTS_PATH, CONTACTS_IFACE, method,
            GLib.Variant(sig, args) if sig else None,
            None, 0, 5000, None).unpack()

    def check_number():
        return tuple(bus.call_sync(
            DIALER_BUS, DIALER_PATH, DIALER_IFACE, "CheckNumber",
            GLib.Variant("(s)", (NUMBER,)),
            GLib.VariantType.new("(ss)"),
            Gio.DBusCallFlags.NONE, 5000, None).unpack())

    deadline = time.monotonic() + 15
    while True:
        try:
            contacts_call("List", (0,), "(t)")
            break
        except Exception:
            if time.monotonic() > deadline:
                raise AssertionError("contacts service never appeared")
            time.sleep(0.2)

    vcard = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Grace Hopper\r\n"
             "TEL:+15550100200\r\nEND:VCARD\r\n")
    (grace_uid,) = contacts_call("Create", (vcard,), "(s)")

    deadline = time.monotonic() + 20
    while True:
        try:
            check_number()
            break
        except Exception:
            if time.monotonic() > deadline:
                raise AssertionError("dialer service never appeared")
            time.sleep(0.2)

    contacts_call("SetTags", (grace_uid, ["starred"]), "(sas)")
    changed_at = time.monotonic()
    verdict = None
    while time.monotonic() - changed_at < 2.0:
        verdict = check_number()
        if verdict == ("ring", "starred"):
            break
        time.sleep(0.1)
    elapsed_ms = int((time.monotonic() - changed_at) * 1000)
    assert verdict == ("ring", "starred"), \
        "service cache went stale: %r after %d ms" % (verdict, elapsed_ms)
    print("SERVICE-SYNC-OK elapsed_ms=%d" % elapsed_ms)
finally:
    for proc in (dialer, contacts):
        try:
            proc.terminate()
        except Exception:
            pass
    for proc in (dialer, contacts):
        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
'''


def test_service_reflects_upstream_retag_without_gui(tmp_path):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is not installed")
    if not (REF_SRC / "xx_contacts" / "__main__.py").exists():
        pytest.skip("ref/xx-contacts is missing")
    helper = tmp_path / "service_sync_helper.py"
    helper.write_text(_HELPER)
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home),
           "PYTHONPATH": str(REF_SRC) + os.pathsep + str(DIALER_SRC)}
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(helper),
         str(DIALER_SRC), str(REF_SRC), str(home)],
        env=env, capture_output=True, text=True, timeout=120)
    assert "SERVICE-SYNC-OK" in proc.stdout, (
        f"helper failed (rc={proc.returncode}):\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
    assert proc.returncode == 0
