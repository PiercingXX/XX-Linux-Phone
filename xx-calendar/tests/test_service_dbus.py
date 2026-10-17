"""D-Bus integration tests over private session buses (L2).

Every test spawns its own ``dbus-daemon`` plus service subprocess, so
nothing touches the real session bus and tests run headless. Client
replies are always tuples (``reply.unpack()[0]`` is the first out-arg)
and every ``call_sync`` passes a real ``GLib.VariantType`` reply type,
never a plain string.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from gi.repository import Gio, GLib

ROOT = Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")
COMMON = str(ROOT.parent / "common")

BUS_NAME = "io.piercingxx.Calendar"
OBJECT_PATH = "/io/piercingxx/Calendar"
INTERFACE = "io.piercingxx.Calendar1"
APP_ID = "io.piercingxx.XXCalendar"

WEEKLY_ICS = "\r\n".join([
    "BEGIN:VEVENT", "UID:weekly-1", "DTSTAMP:20260101T000000Z",
    "DTSTART:20260302T090000Z", "DTEND:20260302T100000Z",
    "RRULE:FREQ=WEEKLY;BYDAY=MO", "SUMMARY:Standup", "END:VEVENT"])

ONE_SHOT_ICS = "\r\n".join([
    "BEGIN:VEVENT", "UID:one-1", "DTSTAMP:20260101T000000Z",
    "DTSTART:20260302T090000Z", "DTEND:20260302T100000Z",
    "SUMMARY:Solo", "END:VEVENT"])

SUBSCRIBER_SCRIPT = """\
import sys
import gi
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib
address, sender, path, iface = sys.argv[1:5]
conn = Gio.DBusConnection.new_for_address_sync(
    address,
    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
def on_signal(_conn, sender_name, _path, _iface, _signal, params, *extra):
    print("SIGNAL sender=%s body=%r" % (sender_name, params.unpack()),
          flush=True)
conn.signal_subscribe(sender, iface, "Changed", path, None,
                      Gio.DBusSignalFlags.NONE, on_signal)
print("READY", flush=True)
GLib.MainLoop().run()
"""

HOLDER_SCRIPT = """\
import os
import sys
import time
os.environ["DBUS_SESSION_BUS_ADDRESS"] = sys.argv[1]
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio
app = Gio.Application(application_id=sys.argv[2],
                      flags=Gio.ApplicationFlags.FLAGS_NONE)
assert app.register(None)
print("READY", flush=True)
time.sleep(30)
"""


def _client_env(extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [SRC, COMMON] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH")
                          else []))
    if extra:
        env.update(extra)
    return env


def _start_run_session(extra_env=None, config_file=None):
    """Spawn ``dbus-run-session`` kept alive by a sleeper; return (proc)."""
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is not installed")
    cmd = ["dbus-run-session"]
    if config_file is not None:
        cmd.append("--config-file=" + str(config_file))
    cmd += ["--", "bash", "-c",
            "echo BUS=$DBUS_SESSION_BUS_ADDRESS; exec sleep 300"]
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env=_client_env(extra_env))


def _session_address(proc):
    line = proc.stdout.readline().strip()
    assert line.startswith("BUS=unix:"), \
        "no bus address published: %r" % (line,)
    return line[len("BUS="):]


def _stop_run_session(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def bus(tmp_path):
    """A private session bus via dbus-run-session; yields its address."""
    proc = _start_run_session({"XDG_RUNTIME_DIR": str(tmp_path)})
    try:
        yield _session_address(proc)
    finally:
        _stop_run_session(proc)


def _connect(address):
    return Gio.DBusConnection.new_for_address_sync(
        address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


def bus_call(address, method, in_sig=None, args=(), out_sig=None,
             timeout_secs=15):
    """One method call over an explicit connection (closed afterwards)."""
    conn = _connect(address)
    try:
        reply = conn.call_sync(
            BUS_NAME, OBJECT_PATH, INTERFACE, method,
            GLib.Variant(in_sig, args) if in_sig else None,
            GLib.VariantType.new(out_sig) if out_sig else None,
            Gio.DBusCallFlags.NONE, int(timeout_secs * 1000), None)
        return reply.unpack()
    finally:
        try:
            conn.close_sync(None)
        except Exception:
            pass


def _name_has_owner(address, name):
    conn = _connect(address)
    try:
        try:
            conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                           "org.freedesktop.DBus", "GetNameOwner",
                           GLib.Variant("(s)", (name,)),
                           GLib.VariantType.new("(s)"),
                           Gio.DBusCallFlags.NONE, 5000, None)
            return True
        except GLib.Error:
            return False
    finally:
        try:
            conn.close_sync(None)
        except Exception:
            pass


def _wait_for_service(address, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            bus_call(address, "ListCalendars", out_sig="(aa{sv})",
                     timeout_secs=5)
            return
        except GLib.Error:
            time.sleep(0.2)
    raise AssertionError("service did not appear on the bus")


def _wait_name_gone(address, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _name_has_owner(address, BUS_NAME):
            return
        time.sleep(0.2)
    raise AssertionError("service did not idle-exit")


@pytest.fixture
def service_proc(bus, tmp_path):
    """An explicitly started service on the private bus (idle 120 s)."""
    db_path = str(tmp_path / "calendar.db")
    proc = subprocess.Popen(
        [sys.executable, "-m", "xx_calendar", "--service",
         "--db", db_path, "--idle-timeout", "120"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=_client_env({"DBUS_SESSION_BUS_ADDRESS": bus}))
    try:
        _wait_for_service(bus)
        yield proc, db_path
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _window():
    from datetime import datetime

    def utc(iso):
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00"))
                   .timestamp() * 1000)

    return utc("2026-03-01T00:00:00Z"), utc("2026-05-01T00:00:00Z")


def test_every_method_over_the_bus(service_proc, bus):
    address = bus
    (calendars,) = bus_call(address, "ListCalendars", out_sig="(aa{sv})")
    assert any(c["id"] == "local" for c in calendars)
    (uid,) = bus_call(address, "Create", "(ss)",
                      ("local", ONE_SHOT_ICS), "(s)")
    assert uid == "one-1"
    (ics_text,) = bus_call(address, "Get", "(s)", (uid,), "(s)")
    assert "SUMMARY:Solo" in ics_text
    start, end = _window()
    (instances,) = bus_call(address, "ListInstances", "(xxas)",
                            (start, end, ["local"]), "(aa{sv})")
    assert [(i["uid"], i["start"]) for i in instances] == [
        ("one-1", start + 9 * 3600000 + 86400000)]
    bus_call(address, "Update", "(sssx)",
             (uid, ONE_SHOT_ICS.replace("Solo", "Solo 2"), "all", 0), "()")
    (ics_text,) = bus_call(address, "Get", "(s)", (uid,), "(s)")
    assert "Solo 2" in ics_text
    (revision, changed, deleted) = bus_call(address, "List", "(t)", (0,),
                                            "(tasas)")
    assert revision >= 2 and "one-1" in changed and deleted == []
    bus_call(address, "Delete", "(ssx)", (uid, "all", 0), "()")
    (instances,) = bus_call(address, "ListInstances", "(xxas)",
                            (start, end, ["local"]), "(aa{sv})")
    assert instances == []
    (_revision, _changed, deleted) = bus_call(address, "List", "(t)",
                                              (revision,), "(tasas)")
    assert "one-1" in deleted


def test_method_errors_over_the_bus(service_proc, bus):
    with pytest.raises(GLib.Error) as excinfo:
        bus_call(bus, "Get", "(s)", ("missing",), "(s)")
    assert "NotFound" in str(excinfo.value)
    with pytest.raises(GLib.Error) as excinfo:
        bus_call(bus, "Update", "(sssx)", ("missing", ONE_SHOT_ICS,
                                           "all", 0), "()")
    assert "NotFound" in str(excinfo.value)
    bus_call(bus, "Create", "(ss)", ("local", ONE_SHOT_ICS), "(s)")
    with pytest.raises(GLib.Error) as excinfo:
        bus_call(bus, "Update", "(sssx)", ("one-1", ONE_SHOT_ICS,
                                           "someday", 0), "()")
    assert "InvalidArgument" in str(excinfo.value)


def test_gui_app_id_coexists_with_service(service_proc, bus):
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER_SCRIPT, bus, APP_ID],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env=_client_env())
    try:
        assert holder.stdout.readline().strip() == "READY"
        assert _name_has_owner(bus, APP_ID)
        assert _name_has_owner(bus, BUS_NAME)
        (calendars,) = bus_call(bus, "ListCalendars", out_sig="(aa{sv})")
        assert calendars != []
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_changed_subscriber_filtered_on_sender(service_proc, bus, tmp_path):
    script = tmp_path / "subscriber.py"
    script.write_text(SUBSCRIBER_SCRIPT, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(script), bus, BUS_NAME, OBJECT_PATH, INTERFACE],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env=_client_env())
    try:
        assert proc.stdout.readline().strip() == "READY"
        bus_call(bus, "Create", "(ss)", ("local", ONE_SHOT_ICS), "(s)")
        bus_call(bus, "Update", "(sssx)",
                 ("one-1", ONE_SHOT_ICS, "all", 0), "()")
        bus_call(bus, "Delete", "(ssx)", ("one-1", "all", 0), "()")
        time.sleep(3)
        proc.terminate()
        out, _ = proc.communicate(timeout=10)
        signals = [line for line in out.splitlines()
                   if line.startswith("SIGNAL")]
        assert len(signals) == 3, signals
        revisions = []
        for line in signals:
            assert "sender=:1." in line
            assert "'one-1'" in line
            revisions.append(int(line.split("(")[1].split(",")[0]))
        assert revisions == sorted(revisions) and len(set(revisions)) == 3
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_scoped_edits_on_weekly_series(service_proc, bus):
    start, end = _window()
    bus_call(bus, "Create", "(ss)", ("local", WEEKLY_ICS), "(s)")

    def instances():
        (found,) = bus_call(bus, "ListInstances", "(xxas)",
                            (start, end, ["local"]), "(aa{sv})")
        return found

    before = instances()
    assert len(before) == 9
    second = before[1]["start"]
    moved = ONE_SHOT_ICS.replace("UID:one-1", "UID:weekly-1").replace(
        "DTSTART:20260302T090000Z", "DTSTART:20260309T110000Z").replace(
        "DTEND:20260302T100000Z", "DTEND:20260309T120000Z").replace(
        "SUMMARY:Solo", "SUMMARY:Moved")
    bus_call(bus, "Update", "(sssx)", ("weekly-1", moved, "this", second),
             "()")
    after_this = instances()
    assert len(after_this) == 9
    assert after_this[1]["start"] == second + 2 * 3600000
    assert after_this[1]["is_override"] is True
    assert after_this[1]["title"] == "Moved"

    split_at = before[2]["start"]
    tail = WEEKLY_ICS.replace("SUMMARY:Standup", "SUMMARY:Tail").replace(
        "DTSTART:20260302T090000Z", "DTSTART:20260316T090000Z").replace(
        "DTEND:20260302T100000Z", "DTEND:20260316T100000Z")
    before_rev = bus_call(bus, "List", "(t)", (0,), "(tasas)")[0]
    bus_call(bus, "Update", "(sssx)",
             ("weekly-1", tail, "this-and-following", split_at), "()")
    (parent_ics,) = bus_call(bus, "Get", "(s)", ("weekly-1",), "(s)")
    assert "UNTIL=20260316T085959Z" in parent_ics
    after_split = instances()
    assert [i["start"] for i in after_split[:4]] == [
        before[0]["start"], second + 2 * 3600000, split_at,
        before[3]["start"]]
    assert after_split[0]["title"] == "Standup"
    assert after_split[2]["title"] == "Tail"
    (_rev, changed, _deleted) = bus_call(bus, "List", "(t)",
                                         (before_rev,), "(tasas)")
    tail_uid = next(u for u in changed if u != "weekly-1")

    cut = after_split[0]["start"]
    bus_call(bus, "Delete", "(ssx)", ("weekly-1", "this", cut), "()")
    after_delete = instances()
    assert cut not in [i["start"] for i in after_delete
                       if i["uid"] == "weekly-1"]

    bus_call(bus, "Delete", "(ssx)", ("weekly-1", "all", 0), "()")
    remaining = instances()
    assert remaining != []
    assert {i["uid"] for i in remaining} == {tail_uid}
    bus_call(bus, "Delete", "(ssx)", (tail_uid, "all", 0), "()")
    assert instances() == []


@pytest.fixture(scope="module")
def built_prefix(tmp_path_factory):
    if shutil.which("meson") is None or shutil.which("ninja") is None:
        pytest.skip("meson/ninja are not installed")
    tmp = tmp_path_factory.mktemp("calendar_prefix")
    build = tmp / "build"
    prefix = tmp / "prefix"
    env = _client_env({"PATH": os.path.dirname(sys.executable) + ":" +
                       os.environ.get("PATH", "")})
    setup = subprocess.run(
        ["meson", "setup", str(build), "--prefix", str(prefix)],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=120)
    assert setup.returncode == 0, setup.stderr[-2000:]
    install = subprocess.run(
        ["meson", "install", "-C", str(build)],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=120)
    assert install.returncode == 0, install.stderr[-2000:]
    service_file = (prefix / "share" / "dbus-1" / "services" /
                    "io.piercingxx.Calendar.service")
    assert service_file.exists()
    return prefix


@pytest.fixture
def make_activated_bus(tmp_path_factory, built_prefix):
    """Spawn buses whose config servicedir holds the built .service file."""
    sessions = []

    def make(idle_timeout="60"):
        tmp_path = tmp_path_factory.mktemp("activated")
        home = tmp_path / "home"
        (home / ".local" / "share").mkdir(parents=True)
        config = tmp_path / "session.conf"
        config.write_text(
            "<!DOCTYPE busconfig PUBLIC \"-//freedesktop//DTD D-Bus Bus\"\n"
            " \"http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd\">\n"
            "<busconfig>\n"
            "  <type>session</type>\n"
            "  <keep_umask/>\n"
            "  <listen>unix:tmpdir=/tmp</listen>\n"
            "  <auth>EXTERNAL</auth>\n"
            "  <servicedir>" + str(built_prefix / "share" / "dbus-1"
                                   / "services") + "</servicedir>\n"
            "  <policy context=\"default\">\n"
            "    <allow send_destination=\"*\" eavesdrop=\"true\"/>\n"
            "    <allow eavesdrop=\"true\"/>\n"
            "    <allow own=\"*\"/>\n"
            "  </policy>\n"
            "</busconfig>\n",
            encoding="utf-8")
        extra = {"HOME": str(home),
                 "XDG_RUNTIME_DIR": str(tmp_path),
                 "XX_CALENDAR_IDLE_TIMEOUT": idle_timeout}
        proc = _start_run_session(extra, config_file=config)
        sessions.append(proc)
        return _session_address(proc), home

    yield make
    for proc in sessions:
        _stop_run_session(proc)


@pytest.fixture
def activated_bus(make_activated_bus):
    """Private bus whose servicedir holds the meson-built .service file."""
    yield make_activated_bus()


def test_activation_from_built_service_file(activated_bus, built_prefix):
    address, home = activated_bus
    assert not _name_has_owner(address, BUS_NAME)
    (calendars,) = bus_call(address, "ListCalendars", out_sig="(aa{sv})",
                            timeout_secs=30)
    assert any(c["id"] == "local" for c in calendars)
    assert _name_has_owner(address, BUS_NAME)
    service_file = (built_prefix / "share" / "dbus-1" / "services" /
                    "io.piercingxx.Calendar.service")
    assert service_file.read_text(
        encoding="utf-8").splitlines()[0] == "[D-BUS Service]"
    assert f"Name={BUS_NAME}" in service_file.read_text(encoding="utf-8")
    default_db = (home / ".local" / "share" / "xx-calendar" /
                  "calendar.db")
    assert default_db.exists()


def test_reactivation_after_idle_exit(make_activated_bus):
    address, _home = make_activated_bus(idle_timeout="2")
    (calendars,) = bus_call(address, "ListCalendars", out_sig="(aa{sv})",
                            timeout_secs=30)
    assert calendars != []
    _wait_name_gone(address, timeout=30)
    (calendars,) = bus_call(address, "ListCalendars", out_sig="(aa{sv})",
                            timeout_secs=30)
    assert any(c["id"] == "local" for c in calendars)
