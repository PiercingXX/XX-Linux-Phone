"""L5 SendMms over a real bus against a fake mmsd-tng.

Runs under ``dbus-run-session``: a fake mmsd-tng owns ``org.ofono.mms``
and exports the documented service shapes (``org.ofono.mms.Service`` at
``/org/ofono/mms/modemmanager``: ``GetMessages`` -> ``a(oa{sv})``,
``SendMessage(as recipients, v smil, a(sss) attachments)`` -> ``o``;
message objects with ``org.ofono.mms.Message`` + ``Properties.GetAll``
-> ``a{sv}`` and documented ``(id, type, file, offset, length)``
attachment 5-tuples), while the real :class:`Daemon` serves
``io.piercingxx.TxxT1`` and the real :class:`TxxtClient` drives
``SendMms``/``FetchMms``. Skipped without ``dbus-run-session``.
"""

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_HELPER = r'''
import struct
import sys

from gi.repository import GLib, Gio

from txxt import daemon as daemon_mod
from txxt.daemon import TXXT_IFACE, TXXT_OBJECT_PATH, Daemon
from txxt.mm_client import MMS_BUS_NAME, MMS_SERVICE_PATH
from txxt.store import Store
from txxt.txxt_client import TxxtClient

txxt_src, db_path, photo_path, carrier_path = (
    sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])

SERVICE_XML = """\
<node>
  <interface name="org.ofono.mms.Service">
    <method name="GetMessages">
      <arg direction="out" type="ao"/>
    </method>
    <method name="SendMessage">
      <arg direction="in" type="as" name="recipients"/>
      <arg direction="in" type="v" name="smil"/>
      <arg direction="in" type="a(sss)" name="attachments"/>
      <arg direction="out" type="o" name="path"/>
    </method>
    <signal name="MessageAdded">
      <arg type="o" name="path"/>
      <arg type="a{sv}" name="properties"/>
    </signal>
  </interface>
</node>
"""

MESSAGE_XML = """\
<node>
  <interface name="org.ofono.mms.Message">
    <method name="Delete"/>
    <method name="MarkRead"/>
  </interface>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="GetAll">
      <arg direction="in" type="s" name="interface"/>
      <arg direction="out" type="a{sv}" name="props"/>
    </method>
  </interface>
</node>
"""

state = {}
loop = GLib.MainLoop()
GLib.timeout_add(30000, loop.quit)

store = Store(db_path)
mmsd = {"messages": {}, "counter": 100, "sends": []}


def _variant_props(props):
    packed = {}
    for key, value in props.items():
        if isinstance(value, list) and value and all(
                isinstance(entry, (list, tuple)) and len(entry) == 5
                for entry in value):
            packed[key] = GLib.Variant("a(ssstt)", [
                (str(a), str(b), str(c), int(d), int(e))
                for a, b, c, d, e in value])
        elif isinstance(value, list) and value and all(
                isinstance(entry, (list, tuple)) and len(entry) == 3
                for entry in value):
            packed[key] = GLib.Variant("a(sss)", [
                (str(a), str(b), str(c)) for a, b, c in value])
        elif isinstance(value, list):
            packed[key] = GLib.Variant("as", [str(v) for v in value])
        else:
            packed[key] = GLib.Variant("s", str(value))
    return GLib.Variant("(a{sv})", (packed,))


def _service_handler(connection, sender, obj_path, iface, method, params, invocation):
    try:
        if method == "GetMessages":
            invocation.return_value(
                GLib.Variant("(ao)", (list(mmsd["messages"]),)))
        elif method == "SendMessage":
            recipients, smil, attachments = params.unpack()
            assert list(recipients) == ["+15550100200"], recipients
            assert "image_0.jpg" in smil and "text_0.txt" in smil, smil
            kinds = {kind for _, kind, _ in attachments}
            assert kinds == {"text/plain", "image/jpeg"}, kinds
            for _name, kind, staged in attachments:
                if kind == "image/jpeg":
                    with open(staged, "rb") as handle:
                        staged_bytes = handle.read()
                    assert b"GPS" not in staged_bytes, "unscrubbed bytes reached mmsd"
            mmsd["counter"] += 1
            path = f"/org/ofono/mms/message/{mmsd['counter']}"
            mmsd["messages"][path] = {
                "Recipients": list(recipients),
                "ContentLocation": "http://mms/sent",
            }
            mmsd["sends"].append((list(recipients), list(attachments)))
            node = Gio.DBusNodeInfo.new_for_xml(MESSAGE_XML)
            for message_iface in node.interfaces:
                connection.register_object(path, message_iface,
                                           _message_handler)
            invocation.return_value(GLib.Variant("(o)", (path,)))
        else:
            invocation.return_dbus_error(
                "org.ofono.mms.Error.Invalid", f"unknown: {method}")
    except Exception as exc:
        state["error"] = f"mmsd service: {exc}"
        loop.quit()


def _message_handler(connection, sender, obj_path, iface, method, params, invocation):
    try:
        if iface == "org.freedesktop.DBus.Properties" and method == "GetAll":
            (wanted,) = params.unpack()
            props = dict(mmsd["messages"].get(obj_path, {}))
            if wanted == "org.ofono.mms.Message":
                invocation.return_value(_variant_props(props))
            else:
                invocation.return_dbus_error(
                    "org.ofono.mms.Error.Invalid", f"unknown iface: {wanted}")
        elif method in ("Delete", "MarkRead"):
            mmsd["messages"].pop(obj_path, None)
            invocation.return_value(GLib.Variant("()", ()))
        else:
            invocation.return_dbus_error(
                "org.ofono.mms.Error.Invalid", f"unknown: {method}")
    except Exception as exc:
        state["error"] = f"mmsd message: {exc}"
        loop.quit()


def fail(message):
    state["error"] = message
    loop.quit()


conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
service_node = Gio.DBusNodeInfo.new_for_xml(SERVICE_XML)
conn.register_object(MMS_SERVICE_PATH, service_node.interfaces[0],
                     _service_handler)


def _on_mms_acquired(connection, name):
    state["mms_owned"] = True


Gio.bus_own_name(
    Gio.BusType.SESSION, MMS_BUS_NAME, Gio.BusNameOwnerFlags.NONE,
    None, _on_mms_acquired, None)

inbound_path = "/org/ofono/mms/message/7"
mmsd["messages"][inbound_path] = {
    "Recipients": ["+15550100000"],
    "Sender": "+15550100999",
    "Status": "received",
    "ContentLocation": "http://mms/inbound-7",
    "Attachments": [("image_0.jpg", "image/jpeg", carrier_path, 0, 26)],
}
message_node = Gio.DBusNodeInfo.new_for_xml(MESSAGE_XML)
for message_iface in message_node.interfaces:
    conn.register_object(inbound_path, message_iface, _message_handler)

d = daemon_mod.Daemon(
    store,
    system_bus_fn=lambda: None,
    session_bus_fn=lambda: Gio.bus_get_sync(Gio.BusType.SESSION, None),
)


def on_bus_acquired(connection, name):
    d.register_on(connection)


daemon_mod.own_bus_name(on_bus_acquired)
client = TxxtClient(conn)
inbound_id = store.persist_inbound_mms_metadata(
    "+15550100999", 1717243200000, content_location="http://mms/inbound-7")


def on_fetched_inbound(path, err):
    if err is not None or not path:
        fail(f"FetchMms inbound failed: path={path!r} err={err!r}")
        return
    try:
        stored = store.get_message(inbound_id)
    except Exception as exc:
        fail(f"store read failed: {exc}")
        return
    if stored is None or stored.media_path != path:
        fail("FetchMms did not record the copied photo")
        return
    loop.quit()


def on_fetched_sent(path, err):
    if err is not None or not path:
        fail(f"FetchMms sent failed: path={path!r} err={err!r}")
        return
    client.fetch_mms(inbound_id, on_fetched_inbound)


def on_sent_bad(mid, err):
    if err is None:
        fail("SendMms with an unscrubbable photo must fail")
        return
    client.fetch_mms(state["sent_id"], on_fetched_sent)


def on_sent(mid, err):
    if err is not None or mid is None:
        fail(f"SendMms failed: mid={mid!r} err={err!r}")
        return
    state["sent_id"] = int(mid)
    row = store.get_message(int(mid))
    if row is None or row.transport != "MMS" or row.body != "look":
        fail("SendMms did not persist the outgoing MMS row")
        return
    if row.sent is not True:
        fail("SendMms did not mark the row sent")
        return
    if not mmsd["sends"]:
        fail("mmsd-tng never saw SendMessage")
        return
    client.send_mms(["+1555"], "", [sys.argv[5]], on_sent_bad)


def start():
    if not state.get("mms_owned"):
        return True
    client.send_mms(["+15550100200"], "look", [photo_path], on_sent)
    return False


GLib.timeout_add(100, start)
loop.run()

assert "error" not in state, state["error"]
assert mmsd["sends"], "no SendMessage reached the fake mmsd-tng"
print("SENDMMS-BUS-OK")
'''


def _jpeg_with_gps() -> bytes:
    def _seg(marker: int, payload: bytes) -> bytes:
        return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload

    return (
        b"\xff\xd8"
        + _seg(0xE0, b"JFIF\x00" + b"\x00" * 9)
        + _seg(0xE1, b"Exif\x00\x00GPS-payload")
        + b"\xff\xda\x11\x22\xff\x00\x33\xff\xd9"
    )


def test_sendmms_against_fake_mmsd_service(tmp_path):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is not installed")
    photo = tmp_path / "camera.jpg"
    photo.write_bytes(_jpeg_with_gps())
    carrier = tmp_path / "carrier.jpg"
    carrier.write_bytes(
        b"\xff\xd8" + b"\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 9
        + b"\xff\xda\x11\x22\xff\xd9")
    refused = tmp_path / "evil.heic"
    refused.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)
    helper = tmp_path / "sendmms_bus_helper.py"
    helper.write_text(_HELPER)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            (str(ROOT / "src"), str(ROOT.parent / "common"))),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(helper),
         str(ROOT / "src"), str(tmp_path / "txxt.db"),
         str(photo), str(carrier), str(refused)],
        env=env, capture_output=True, text=True, timeout=120)
    assert "SENDMMS-BUS-OK" in proc.stdout, (
        f"helper failed (rc={proc.returncode}):\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
    assert proc.returncode == 0
