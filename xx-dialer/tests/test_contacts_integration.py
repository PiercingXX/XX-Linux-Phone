"""Real-service contacts integration: dialer client vs xx-contacts.

Starts the real ``xx-contacts`` service (read-only tree at
``ref/xx-contacts``) on a private session bus provided by
``dbus-run-session`` with an isolated ``HOME``, then drives the real
``ContactsClient`` through warm-up, an upstream retag (star removed),
an exact dialer write, and an absent -> present reconcile that pushes
only pending edits.

Skipped when ``dbus-run-session`` or ``ref/xx-contacts`` is missing.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Lane copies carry it under ref/; the real checkout has it as a sibling repo.
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

dialer_src, ref_src, db_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, dialer_src)

from gi.repository import GLib, Gio

from xx_dialer.store import Store
from xx_dialer import contacts_client as cc

BUS = "io.piercingxx.Contacts"
PATH = "/io/piercingxx/Contacts"
IFACE = "io.piercingxx.Contacts1"

env = {**os.environ, "PYTHONPATH": ref_src}
svc = subprocess.Popen([sys.executable, "-m", "xx_contacts", "--service"],
                       env=env)
try:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(method, args, sig):
        return bus.call_sync(
            BUS, PATH, IFACE, method,
            GLib.Variant(sig, args) if sig else None,
            None, 0, 5000, None).unpack()

    deadline = time.monotonic() + 15
    while True:
        try:
            call("List", (0,), "(t)")
            break
        except Exception:
            if time.monotonic() > deadline:
                raise AssertionError("service did not appear on the bus")
            time.sleep(0.2)

    grace_vcard = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Grace Hopper\r\n"
                   "TEL:+15550100200\r\nEND:VCARD\r\n")
    bob_vcard = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Bob Builder\r\n"
                 "TEL:+15550100201\r\nEND:VCARD\r\n")
    (grace_uid,) = call("Create", (grace_vcard,), "(s)")
    (bob_uid,) = call("Create", (bob_vcard,), "(s)")
    call("SetTags", (grace_uid, ["starred"]), "(sas)")
    call("SetTags", (bob_uid, ["business"]), "(sas)")

    store = Store(Path(db_path))
    try:
        syncs = []
        client = cc.ContactsClient(store, on_sync=syncs.append)
        assert client.start(), "client failed to start"
        loop = GLib.MainLoop()

        def run(seconds):
            GLib.timeout_add(int(seconds * 1000), loop.quit)
            loop.run()

        run(3.0)
        assert client.is_present, "client never saw the service"
        name, tier = cc.lookup_number(store, "+15550100200")
        assert (name, tier) == ("Grace Hopper", "starred"), \
            f"warm-up failed: {(name, tier)}"

        call("SetTags", (grace_uid, ["business"]), "(sas)")
        run(2.0)
        name, tier = cc.lookup_number(store, "+15550100200")
        assert (name, tier) == ("Grace Hopper", "business"), \
            f"upstream retag did not propagate: {(name, tier)}"
        assert not store.is_starred(grace_uid), "star overlay survived retag"

        done = []
        client.set_tags_async(grace_uid, ["starred", "blocked"],
                              on_done=lambda: done.append(True))
        run(2.0)
        assert done, "dialer write never completed"
        _uid, _name, tags = call("LookupNumber", ("+15550100200",), "(s)")
        assert sorted(tags) == ["blocked", "starred"], \
            f"dialer write merged instead of replacing: {tags}"

        svc.terminate()
        svc.wait(timeout=10)
        run(1.5)
        assert not client.is_present, "client still present after stop"
        client.set_business(grace_uid, True)
        pending = dict((uid, tags)
                       for uid, tags, _ts in store.pending_edits())
        assert sorted(pending.get(grace_uid, [])) == \
            ["blocked", "business", "starred"], \
            f"offline edit not recorded: {pending}"
        assert bob_uid not in pending, \
            f"unedited contact gained a pending edit: {pending}"

        svc2 = subprocess.Popen(
            [sys.executable, "-m", "xx_contacts", "--service"], env=env)
        try:
            run(5.0)
            assert client.is_present, "client never reappeared"
            _uid, _name, tags = call("LookupNumber",
                                    ("+15550100200",), "(s)")
            assert sorted(tags) == ["blocked", "business", "starred"], \
                f"reconcile did not push pending edit: {tags}"
            _uid, _name, bob_tags = call("LookupNumber",
                                        ("+15550100201",), "(s)")
            assert sorted(bob_tags) == ["business"], \
                f"unedited contact was pushed: {bob_tags}"
            assert store.pending_edits() == [], \
                "pending edits were not cleared after push"
            run(2.0)
            name, tier = cc.lookup_number(store, "+15550100200")
            assert tier == "starred", \
                f"reconciled cache lost star: {(name, tier)}"
        finally:
            svc2.terminate()
            try:
                svc2.wait(timeout=10)
            except Exception:
                svc2.kill()
        client.stop()
    finally:
        store.close()
    print("INTEGRATION-OK")
finally:
    try:
        svc.terminate()
    except Exception:
        pass
'''


def test_contacts_against_real_service(tmp_path):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is not installed")
    if not (REF_SRC / "xx_contacts" / "__main__.py").exists():
        pytest.skip("ref/xx-contacts is missing")
    helper = tmp_path / "contacts_integration_helper.py"
    helper.write_text(_HELPER)
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home),
           "PYTHONPATH": str(REF_SRC) + os.pathsep + str(DIALER_SRC)}
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(helper),
         str(DIALER_SRC), str(REF_SRC), str(tmp_path / "dialer.db")],
        env=env, capture_output=True, text=True, timeout=120)
    assert "INTEGRATION-OK" in proc.stdout, (
        f"helper failed (rc={proc.returncode}):\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
    assert proc.returncode == 0
