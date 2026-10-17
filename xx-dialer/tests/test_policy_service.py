"""Policy1 over fakes plus a real private-bus CheckNumber round trip.

Fakes mirror the Gio traps (tuple replies, two-arg callbacks, seven-arg
signal handlers); the ``dbus-run-session`` test proves the real service
answers a real client with the engine's verdict.
"""
import os
import shutil
import subprocess
import sys
import textwrap

import pytest

from xx_dialer import config as app_config
from xx_dialer.service import (
    BUS_NAME,
    CONSULT_TIMEOUT_MS,
    INTERFACE,
    OBJECT_PATH,
    OWNER_PROBE_TIMEOUT_MS,
    PolicyService,
    bus_name_has_owner,
    consult_check_number,
)

HELPER = textwrap.dedent("""\
    import os
    import sys
    sys.path.insert(0, {src!r})
    os.environ["HOME"] = {home!r}
    from gi.repository import Gio, GLib
    from xx_dialer import config as app_config
    from xx_dialer.service import (
        PolicyService, BUS_NAME, OBJECT_PATH, INTERFACE)
    from xx_dialer.store import Store
    from pathlib import Path
    home = Path({home!r})
    settings = app_config.load(home)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, home)
    store = Store(home / ".local" / "share" / "xx-dialer" / "calls.db")
    store.add_blocked("+14155550200")
    store.close()
    service = PolicyService(home=home)
    loop = GLib.MainLoop()
    result = {{}}
    def on_bus_acquired(connection, _name):
        service.register_on(connection)
    def on_name_acquired(_connection, _name):
        def _call():
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call(
                BUS_NAME, OBJECT_PATH,
                INTERFACE, "CheckNumber",
                GLib.Variant("(s)", ("+14155550200",)),
                GLib.VariantType.new("(ss)"),
                Gio.DBusCallFlags.NONE, 5000, None, _on_reply)
            return False
        GLib.idle_add(_call)
    def _on_reply(conn, res):
        try:
            result["reply"] = conn.call_finish(res).unpack()
        except Exception as err:
            result["error"] = str(err)
        loop.quit()
    def on_name_lost(_connection, _name):
        result["error"] = "name lost"
        loop.quit()
    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
                     Gio.BusNameOwnerFlags.NONE,
                     on_bus_acquired, on_name_acquired, on_name_lost)
    GLib.timeout_add_seconds(15, loop.quit)
    loop.run()
    assert result.get("reply") == ("block", "blocked"), result
    print("VERDICT=%s REASON=%s" % result["reply"])
    """)


class FakeVariant:
    def __init__(self, fmt, value):
        self.fmt = fmt
        self.value = value

    def unpack(self):
        return self.value


class FakeGlib:
    Error = type("Error", (Exception,), {})

    def Variant(self, fmt, value):
        return FakeVariant(fmt, value)


class FakeGio:
    BusType = type("BusType", (), {"SESSION": "session"})
    DBusCallFlags = type("Flags", (), {"NONE": 0})
    DBusSignalFlags = type("Flags", (), {"NONE": 0})

    def bus_get_sync(self, *_args):
        raise AssertionError("tests inject the bus")


class FakeInvocation:
    def __init__(self):
        self.values = []
        self.errors = []

    def return_value(self, variant):
        self.values.append(variant.unpack())

    def return_dbus_error(self, name, message):
        self.errors.append((name, message))


class FakeConnection:
    def __init__(self):
        self.signals = []

    def emit_signal(self, bus_name, path, iface, signal, params):
        self.signals.append((bus_name, path, iface, signal,
                             params.unpack()))


class FakeBus:
    def __init__(self, verdict=("silence", "unknown caller"), owned=True):
        self.verdict = verdict
        self.owned = owned
        self.calls = []
        self.pending = []
        self._results = []
        self.owner_probes = []

    def call_sync(self, bus_name, path, iface, method, params=None,
                  reply_type=None, flags=None, timeout=-1,
                  cancellable=None):
        self.owner_probes.append({"bus": bus_name, "path": path,
                                  "iface": iface, "method": method,
                                  "timeout": timeout,
                                  "params": params.unpack()})
        if not self.owned:
            raise FakeGlib.Error("no owner")
        return FakeVariant("(s)", (":1.9",))

    def call(self, bus_name, path, iface, method, params=None,
             reply_type=None, flags=None, timeout=-1, cancellable=None,
             callback=None, *user_data):
        self.calls.append({"bus": bus_name, "path": path, "iface": iface,
                           "method": method, "timeout": timeout,
                           "reply_type": reply_type,
                           "user_data": user_data,
                           "params": params.unpack()})
        self.pending.append(callback)

    def call_finish(self, _result):
        return FakeVariant("(ss)", self.verdict)

    def drain(self):
        while self.pending:
            self.pending.pop(0)(self, object())


class Deferred:
    """spawn/idle seams that queue work until the test drains them."""

    def __init__(self):
        self.workers = []
        self.idles = []

    def spawn(self, fn):
        self.workers.append(fn)

    def idle(self, fn, *args):
        self.idles.append((fn, args))

    def drain(self):
        while self.workers or self.idles:
            while self.workers:
                self.workers.pop(0)()
            while self.idles:
                fn, args = self.idles.pop(0)
                fn(*args)


def _service(tmp_path, deferred=None):
    deferred = deferred or Deferred()
    settings = app_config.load(tmp_path)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, tmp_path)
    return PolicyService(
        db_file=tmp_path / "calls.db", home=tmp_path,
        spawn=deferred.spawn, idle=deferred.idle), deferred


def _check(service, deferred, number):
    from xx_dialer.store import Store

    with Store(service._db_file) as store:
        store.add_blocked("+14155550200")
    invocation = FakeInvocation()
    service.handle_method_call(FakeConnection(), ":1.1", OBJECT_PATH,
                               INTERFACE, "CheckNumber",
                               FakeVariant("(s)", (number,)), invocation)
    return invocation, deferred


def test_check_number_returns_engine_verdict(tmp_path):
    service, deferred = _service(tmp_path)
    invocation, deferred = _check(service, deferred, "+14155550200")
    assert invocation.values == [] and invocation.errors == []
    deferred.drain()
    assert invocation.values == [("block", "blocked")]
    from xx_dialer.store import Store

    with Store(service._db_file) as store:
        latest = store.latest_for("+14155550200")
        assert latest is not None and latest.verdict == "Block"


def test_check_number_handler_never_blocks_main_loop(tmp_path):
    service, deferred = _service(tmp_path)
    from xx_dialer.store import Store

    with Store(service._db_file) as store:
        store.add_blocked("+14155550200")
    invocation = FakeInvocation()
    service.handle_method_call(FakeConnection(), ":1.1", OBJECT_PATH,
                               INTERFACE, "CheckNumber",
                               FakeVariant("(s)", ("+14155550200",)),
                               invocation)
    assert deferred.workers and not deferred.idles
    assert invocation.values == []
    deferred.drain()
    assert invocation.values == [("block", "blocked")]


def test_check_number_emits_verdict_signal(tmp_path):
    service, deferred = _service(tmp_path)
    invocation, deferred = _check(service, deferred, "+14155550200")
    connection = FakeConnection()
    service.handle_method_call(connection, ":1.1", OBJECT_PATH,
                               INTERFACE, "CheckNumber",
                               FakeVariant("(s)", ("+14155550200",)),
                               invocation)
    deferred.drain()
    assert ("block", "blocked") in [
        (verdict, reason)
        for _bus, _path, _iface, signal, (_n, verdict, reason)
        in connection.signals if signal == "VerdictDecided"]


def test_note_call_ingests_shell_event(tmp_path):
    service, deferred = _service(tmp_path)
    invocation = FakeInvocation()
    service.handle_method_call(FakeConnection(), ":1.1", OBJECT_PATH,
                               INTERFACE, "NoteCall",
                               FakeVariant("(ssxxs)",
                                           ("+14155550100", "incoming",
                                            1_790_000_000_000, 30_000,
                                            "answered")),
                               invocation)
    deferred.drain()
    assert invocation.values == [()]
    from xx_dialer.store import Store

    with Store(service._db_file) as store:
        assert store.call_count() == 1
        assert store.calls_for("+14155550100")[0].duration == 30


def test_note_call_bad_direction_errors(tmp_path):
    service, deferred = _service(tmp_path)
    invocation = FakeInvocation()
    service.handle_method_call(FakeConnection(), ":1.1", OBJECT_PATH,
                               INTERFACE, "NoteCall",
                               FakeVariant("(ssxxs)",
                                           ("+14155550100", "martian", 0,
                                            0, "missed")),
                               invocation)
    deferred.drain()
    assert invocation.values == []
    assert invocation.errors and "InvalidArgument" in invocation.errors[0][0]


def test_unknown_method_errors(tmp_path):
    service, deferred = _service(tmp_path)
    invocation = FakeInvocation()
    service.handle_method_call(FakeConnection(), ":1.1", OBJECT_PATH,
                               INTERFACE, "LookupNumber",
                               FakeVariant("(s)", ("+14155550100",)),
                               invocation)
    deferred.drain()
    assert invocation.values == []
    assert invocation.errors and "unknown method" in invocation.errors[0][1]


def test_owner_probe_helper_shape():
    bus = FakeBus()
    gio, glib = FakeGio(), FakeGlib()
    assert bus_name_has_owner(BUS_NAME, OWNER_PROBE_TIMEOUT_MS, bus,
                              gio, glib) is True
    assert bus.owner_probes[0]["timeout"] == OWNER_PROBE_TIMEOUT_MS
    assert bus.owner_probes[0]["method"] == "GetNameOwner"
    assert bus.owner_probes[0]["params"] == (BUS_NAME,)


def test_service_bus_name_is_not_the_app_id():
    """The service must never own the GTK application id.

    ``Gtk.Application`` registers its app id as a bus name; a service
    on the same name turns the GUI into a secondary instance that
    exits with "Failed to register: No such interface
    org.gtk.Actions".
    """
    from pathlib import Path

    from xx_dialer.main import APP_ID

    assert BUS_NAME == "io.piercingxx.XXDialer.Policy"
    assert OBJECT_PATH == "/io/piercingxx/XXDialer/Policy"
    assert INTERFACE == "io.piercingxx.XXDialer.Policy1"
    assert APP_ID == "io.piercingxx.XXDialer"
    assert BUS_NAME != APP_ID
    service_file = (
        Path(__file__).parent.parent / "data"
        / "io.piercingxx.XXDialer.Policy.service.in").read_text(
            encoding="utf-8")
    assert "Name=%s" % BUS_NAME in service_file


def test_consult_shape_matches_shell_conventions():
    bus = FakeBus()
    gio, glib = FakeGio(), FakeGlib()
    verdicts = []
    consult_check_number("+14155559999",
                         lambda v, r: verdicts.append((v, r)),
                         bus=bus, gio=gio, glib=glib)
    assert verdicts == []
    assert bus.owner_probes == []
    call = bus.calls[0]
    assert (call["bus"], call["path"], call["iface"],
            call["method"]) == (BUS_NAME, OBJECT_PATH, INTERFACE,
                                "CheckNumber")
    assert call["timeout"] == CONSULT_TIMEOUT_MS
    assert call["reply_type"] is None
    assert call["user_data"] == ()
    bus.drain()
    assert verdicts == [("silence", "unknown caller")]


def test_consult_calls_through_without_owner():
    bus = FakeBus(owned=False)
    verdicts = []
    consult_check_number("+14155559999",
                         lambda v, r: verdicts.append((v, r)),
                         bus=bus, gio=FakeGio(), glib=FakeGlib())
    assert verdicts == []
    assert bus.owner_probes == []
    assert len(bus.calls) == 1
    bus.drain()
    assert verdicts == [("silence", "unknown caller")]


class FakeContactsClient:
    def __init__(self, revision=0):
        self._revision = revision

    @property
    def revision(self):
        return self._revision


def _enforcing_service(tmp_path, **kwargs):
    settings = app_config.load(tmp_path)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, tmp_path)
    return PolicyService(db_file=tmp_path / "calls.db", home=tmp_path,
                         **kwargs)


def test_cold_lookup_seeds_star_on_first_check(tmp_path):
    calls = []
    service = _enforcing_service(
        tmp_path,
        cold_lookup=lambda number: calls.append(number) or (
            "uid-grace", "Grace Hopper", ["starred"]))
    service.attach_contacts_client(FakeContactsClient(revision=0))
    assert service.check_number("+15550100200") == ("ring", "starred")
    assert service.check_number("+15550100200") == ("ring", "starred")
    assert calls == ["+15550100200"]


def test_cold_lookup_blocked_number_blocks(tmp_path):
    service = _enforcing_service(
        tmp_path,
        cold_lookup=lambda number: ("uid-blocked", "Spammer",
                                    ["blocked"]))
    service.attach_contacts_client(FakeContactsClient(revision=0))
    assert service.check_number("+15550100299") == ("block", "blocked")


def test_cold_lookup_skipped_once_cache_synced(tmp_path):
    calls = []
    service = _enforcing_service(
        tmp_path,
        cold_lookup=lambda number: calls.append(number))
    service.attach_contacts_client(FakeContactsClient(revision=7))
    service.check_number("+15550100200")
    assert calls == []


def test_cold_lookup_failure_fails_open(tmp_path):
    def _boom(number):
        raise OSError("contacts unreachable")

    service = _enforcing_service(tmp_path, cold_lookup=_boom)
    service.attach_contacts_client(FakeContactsClient(revision=0))
    verdict, _reason = service.check_number("+15550100200")
    assert verdict in ("ring", "silence", "block")


def test_no_attached_client_never_cold_looks_up(tmp_path):
    calls = []
    service = _enforcing_service(
        tmp_path,
        cold_lookup=lambda number: calls.append(number))
    service.check_number("+15550100200")
    assert calls == []


def test_consult_fails_open_on_bad_verdict():
    bus = FakeBus(verdict=("ring!", "x"))
    verdicts = []
    consult_check_number("+14155559999",
                         lambda v, r: verdicts.append((v, r)),
                         bus=bus, gio=FakeGio(), glib=FakeGlib())
    bus.drain()
    assert verdicts == [("ring", "fail-open")]


def test_introspection_xml_registers():
    Gio = pytest.importorskip("gi.repository.Gio")
    node = Gio.DBusNodeInfo.new_for_xml(
        __import__("xx_dialer.service", fromlist=["INTROSPECTION_XML"])
        .INTROSPECTION_XML)
    methods = {m.name: ([a.signature for a in m.in_args],
                        [a.signature for a in m.out_args])
               for m in node.interfaces[0].methods}
    assert methods["CheckNumber"] == (["s"], ["s", "s"])
    assert methods["NoteCall"] == (["s", "s", "x", "x", "s"], [])


def test_real_private_bus_check_number(tmp_path):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session not installed")
    pytest.importorskip("gi.repository.Gio")
    home = tmp_path / "home"
    (home / ".local" / "share" / "xx-dialer").mkdir(parents=True)
    (home / ".config" / "xx-dialer").mkdir(parents=True)
    import xx_dialer

    src = str(__import__("pathlib").Path(
        xx_dialer.__file__).parent.parent)
    script = HELPER.format(src=src, home=str(home))
    env = dict(os.environ)
    env["HOME"] = str(home)
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, "-c", script],
        capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "VERDICT=block REASON=blocked" in proc.stdout


def test_service_entry_imports_no_gtk(tmp_path):
    import pathlib

    import xx_dialer

    src = str(pathlib.Path(xx_dialer.__file__).parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", "-m", "xx_dialer",
         "--service", "--check-imports"],
        capture_output=True, text=True, timeout=120, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "no Gtk/Adw" in proc.stdout
    assert "gi.repository.Gtk" not in proc.stderr
    assert "gi.repository.Adw" not in proc.stderr


_ACTIVATION_DRIVER = r'''
import sys
import time
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
names = bus.call_sync(
    "org.freedesktop.DBus", "/org/freedesktop/DBus",
    "org.freedesktop.DBus", "ListActivatableNames",
    None, None, 0, 5000, None).unpack()[0]
assert "io.piercingxx.XXDialer.Policy" in list(names), "not activatable"
started = time.monotonic()
reply = bus.call_sync(
    "io.piercingxx.XXDialer.Policy", "/io/piercingxx/XXDialer/Policy",
    "io.piercingxx.XXDialer.Policy1", "CheckNumber",
    GLib.Variant("(s)", ("+14155550200",)),
    GLib.VariantType.new("(ss)"),
    Gio.DBusCallFlags.NONE, 30000, None).unpack()
elapsed_ms = int((time.monotonic() - started) * 1000)
assert tuple(reply) == ("block", "blocked"), reply
print("ACTIVATION-OK elapsed_ms=%d" % elapsed_ms)
'''

_ACTIVATION_WRAPPER = """\
#!/bin/sh
export HOME={home!r}
export PYTHONPATH={src!r}
exec {exe!r} -m xx_dialer --service
"""


def test_first_check_number_activates_service(tmp_path):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session not installed")
    pytest.importorskip("gi.repository.Gio")
    import pathlib

    import xx_dialer

    from xx_dialer.store import Store

    src = str(pathlib.Path(xx_dialer.__file__).parent.parent)
    home = tmp_path / "home"
    (home / ".local" / "share" / "xx-dialer").mkdir(parents=True)
    (home / ".config" / "xx-dialer").mkdir(parents=True)
    settings = app_config.load(home)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, home)
    store = Store(home / ".local" / "share" / "xx-dialer" / "calls.db")
    store.add_blocked("+14155550200")
    store.close()
    data_dir = tmp_path / "data"
    services_dir = data_dir / "dbus-1" / "services"
    services_dir.mkdir(parents=True)
    (services_dir / "io.piercingxx.XXDialer.Policy.service").write_text(
        "[D-BUS Service]\nName=io.piercingxx.XXDialer.Policy\n"
        "Exec=%s\n" % (tmp_path / "run-service.sh",))
    (tmp_path / "run-service.sh").write_text(
        _ACTIVATION_WRAPPER.format(home=str(home), src=src,
                                   exe=sys.executable))
    (tmp_path / "run-service.sh").chmod(0o755)
    driver = tmp_path / "activation_driver.py"
    driver.write_text(_ACTIVATION_DRIVER)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_DATA_DIRS"] = str(data_dir) + os.pathsep + env.get(
        "XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(driver)],
        capture_output=True, text=True, timeout=90, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "ACTIVATION-OK" in proc.stdout


def test_default_call_placer_starts_voice_client(monkeypatch):
    from xx_dialer import service as service_module

    calls = {}

    class _Voice:
        def start(self):
            calls["started"] = True
            return True

        def place(self, number, on_done):
            calls["placed"] = number
            on_done(True, "ok")

    monkeypatch.setattr("xx_dialer.modem.VoiceClient", lambda: _Voice())
    service_module._default_call_placer("+15551234567")
    assert calls == {"started": True, "placed": "+15551234567"}


def test_default_call_placer_gives_up_when_start_fails(monkeypatch):
    from xx_dialer import service as service_module

    class _FailingVoice:
        def start(self):
            return False

        def place(self, number, on_done):
            raise AssertionError("place must not run without start")

    monkeypatch.setattr("xx_dialer.modem.VoiceClient",
                        lambda: _FailingVoice())
    service_module._default_call_placer("+15551234567")
