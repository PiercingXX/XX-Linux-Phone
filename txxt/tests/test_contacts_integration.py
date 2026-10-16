"""Real-service contacts integration: backend vs xx-contacts.

Starts the real ``xx-contacts`` service on a private session bus
provided by ``dbus-run-session`` with an isolated ``HOME``, then drives
:class:`txxt.contacts.GioContactsBackend` through lookup, search,
uid-addressed tag writes, a starred toggle that must preserve the
``"business"`` tag, and the ``Changed(t, as)`` signal.

Skipped when ``dbus-run-session`` or the ``xx-contacts`` source tree is
missing.
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
TXXT_SRC = ROOT / "src"

_HELPER = r'''
import os
import sys
import time
import subprocess
from pathlib import Path

txxt_src, ref_src, db_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, txxt_src)

from gi.repository import GLib, Gio

from txxt.contacts import GioContactsBackend

BUS = "io.piercingxx.Contacts"
PATH = "/io/piercingxx/Contacts"
IFACE = "io.piercingxx.Contacts1"

env = {**os.environ, "PYTHONPATH": ref_src}
svc = subprocess.Popen([sys.executable, "-m", "xx_contacts",
                        "--service", "--db", db_path], env=env)
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

    ada_vcard = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada Lovelace\r\n"
                 "TEL:+15550100200\r\nEND:VCARD\r\n")
    (ada_uid,) = call("Create", (ada_vcard,), "(s)")
    assert ada_uid, "service created no uid"
    call("SetTags", (ada_uid, ["business"]), "(sas)")

    backend = GioContactsBackend(bus)

    hit = backend.lookup("+15550100200")
    assert hit is not None, "backend missed a known number"
    assert hit.uid == ada_uid, f"wrong uid: {hit.uid!r}"
    assert hit.name == "Ada Lovelace", f"wrong name: {hit.name!r}"
    assert "business" in hit.tags, f"tags lost: {hit.tags!r}"

    assert backend.lookup("+19999999999") is None, \
        "unknown number must read as a miss so EDS is still consulted"

    hits = backend.search("ada", limit=10)
    assert len(hits) == 1, f"search missed: {hits!r}"
    assert hits[0].uid == ada_uid, f"search row lost uid: {hits!r}"
    assert hits[0].name == "Ada Lovelace", f"search row lost name: {hits!r}"
    assert "15550100200" in hits[0].number, \
        f"search row lost primary number: {hits!r}"

    assert backend.set_starred("+15550100200", True) is True
    _uid, _name, tags = call("LookupNumber", ("+15550100200",), "(s)")
    assert sorted(tags) == ["business", "starred"], \
        f"starred toggle wiped business: {tags!r}"
    assert backend.set_starred("+15550100200", False) is True
    _uid, _name, tags = call("LookupNumber", ("+15550100200",), "(s)")
    assert sorted(tags) == ["business"], \
        f"unstar toggle kept starred: {tags!r}"

    seen = []

    def on_changed(conn, sender, path, iface, signal, params, user_data):
        seen.append(params.unpack())

    sub = backend.subscribe_changed(on_changed)
    assert sub, "Changed subscription failed"
    assert backend.set_tags(ada_uid, ["business", "blocked"]) is True

    loop = GLib.MainLoop()
    GLib.timeout_add(3000, loop.quit)
    loop.run()
    assert [uids for _rev, uids in seen if ada_uid in uids], \
        f"Changed(t, as) never arrived: {seen!r}"
    assert all(isinstance(rev, int) for rev, _uids in seen), \
        f"Changed revision is not t: {seen!r}"

    print("INTEGRATION-OK")
finally:
    try:
        svc.terminate()
    except Exception:
        pass
    try:
        svc.wait(timeout=10)
    except Exception:
        try:
            svc.kill()
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
           "PYTHONPATH": str(TXXT_SRC)}
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(helper),
         str(TXXT_SRC), str(REF_SRC), str(tmp_path / "contacts.db")],
        env=env, capture_output=True, text=True, timeout=120)
    assert "INTEGRATION-OK" in proc.stdout, (
        f"helper failed (rc={proc.returncode}):\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
    assert proc.returncode == 0
