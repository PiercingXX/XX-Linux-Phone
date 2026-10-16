"""Headless L5 shell tests: launch parsing, models, and static D-Bus guards."""

import datetime as dt
import re
import subprocess
from pathlib import Path

import pytest
from gi.repository import GLib, GObject

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

BUS_NAME = "io.piercingxx.Calendar"
APP_ID = "io.piercingxx.XXCalendar"


def test_parse_launch_args():
    from xx_calendar.main import parse_launch_args

    opts = parse_launch_args(["--date=2026-10-16"])
    assert opts["date"] == dt.date(2026, 10, 16)
    assert opts["rest"] == []
    opts = parse_launch_args(["--date", "2026-10-16"])
    assert opts["date"] == dt.date(2026, 10, 16)
    opts = parse_launch_args(["--date=not-a-date"])
    assert opts["date"] is None
    opts = parse_launch_args(["--today"])
    assert opts["today"] is True
    opts = parse_launch_args(["--new-event"])
    assert opts["new_event"] is True
    opts = parse_launch_args(
        ["io.piercingxx.XXCalendar://date/2026-10-16"])
    assert opts["date"] == dt.date(2026, 10, 16)
    assert opts["rest"] == []
    opts = parse_launch_args(["--date=2026-10-16", "--today"])
    assert opts["date"] == dt.date(2026, 10, 16)
    assert opts["today"] is True


def test_parse_sheet_datetime():
    from xx_calendar.main import parse_sheet_datetime

    zone = dt.timezone.utc
    assert parse_sheet_datetime("2026-03-02", True, zone) == int(
        dt.datetime(2026, 3, 2, tzinfo=zone).timestamp() * 1000)
    assert parse_sheet_datetime("2026-03-02 09:00", False, zone) == int(
        dt.datetime(2026, 3, 2, 9, 0, tzinfo=zone).timestamp() * 1000)
    assert parse_sheet_datetime("nope", False, zone) is None
    assert parse_sheet_datetime("2026-03-02", False, zone) is None
    assert parse_sheet_datetime("", True, zone) is None


def test_agenda_list_model_headers_and_int64():
    from xx_calendar import views
    from xx_calendar.main import DayHeaderItem, EventItem, agenda_list_model

    big = 1772532000000
    assert big > 2 ** 31
    days = [views.AgendaDay(
        date=dt.date(2026, 3, 2),
        all_day=[views.AgendaRow(uid="a", calendar_id="local",
                                 title="Holiday", start_ms=big,
                                 end_ms=big + 86400000, all_day=True,
                                 sigil_tier=1)],
        timed=[views.AgendaRow(uid="b", calendar_id="local", title="Call",
                               start_ms=big, end_ms=big + 3600000,
                               sigil_tier=0)])]
    model = agenda_list_model(days, True, big + 7200000)
    assert model.get_n_items() == 3
    assert isinstance(model.get_item(0), DayHeaderItem)
    first = model.get_item(1)
    assert isinstance(first, EventItem)
    assert first.props.start == big
    assert first.props.sigil == "▏"
    assert first.props.dimmed is False
    past_model = agenda_list_model(days, True, big + 3 * 86400000)
    assert past_model.get_item(2).props.dimmed is True
    nodim = agenda_list_model(days, False, big + 3 * 86400000)
    assert nodim.get_item(2).props.dimmed is False


def test_event_item_int64_property_type():
    from xx_calendar.main import EventItem

    prop = EventItem.find_property("start")
    assert prop.value_type == GObject.TYPE_INT64
    item = EventItem()
    item.props.start = 1772532000000
    item.props.recurrence_id = 1772532000000
    assert item.props.start == 1772532000000


def test_create_call_uses_varianttype_reply():
    """Real PyGObject rejects a plain-string reply_type: pin the real type."""
    source = (ROOT / "src" / "xx_calendar" / "main.py").read_text()
    assert 'GLib.VariantType.new("(s)")' in source
    assert 'reply_type="' not in source
    variant = GLib.Variant("(ss)", ("local", "BEGIN:VEVENT"))
    assert variant.unpack() == ("local", "BEGIN:VEVENT")


def test_bus_name_is_not_the_gui_app_id():
    from xx_calendar import service
    from xx_calendar import theme

    assert service.BUS_NAME == BUS_NAME
    assert theme.APP_ID == APP_ID
    assert service.BUS_NAME != theme.APP_ID


def test_changed_subscription_targets_service_sender():
    source = (ROOT / "src" / "xx_calendar" / "main.py").read_text()
    assert "BUS_NAME, INTERFACE, \"Changed\", OBJECT_PATH" in source


def test_desktop_actions_and_activation_file():
    desktop = (ROOT / "data" / "io.piercingxx.XXCalendar.desktop.in"
               ).read_text()
    assert "DBusActivatable=true" in desktop
    assert "MimeType=text/calendar;" in desktop
    assert "--new-event" in desktop
    assert "--today" in desktop
    activation = (ROOT / "data" / "io.piercingxx.Calendar.service.in"
                  ).read_text()
    assert "Name=io.piercingxx.Calendar" in activation


def test_no_socket_imports_in_sources():
    hits = []
    for path in sorted((ROOT / "src" / "xx_calendar").glob("*.py")):
        text = path.read_text()
        for name in ("import socket", "import urllib", "import http",
                     "from socket", "from urllib", "from http"):
            if re.search(r"(?m)^\s*(import|from)\s+" + name.split()[-1],
                         text):
                hits.append(f"{path.name}: {name}")
    assert hits == []


def _run_session_bus(tmp_path):
    import os
    import shutil

    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is not installed")
    cmd = ["dbus-run-session", "--", "bash", "-c",
           "echo BUS=$DBUS_SESSION_BUS_ADDRESS; exec sleep 60"]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + (":" + env["PYTHONPATH"] if env.get(
        "PYTHONPATH") else "")
    env["XDG_RUNTIME_DIR"] = str(tmp_path)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env)
    line = proc.stdout.readline().strip()
    assert line.startswith("BUS=unix:")
    return proc, line[len("BUS="):], env


def test_gui_app_registers_while_service_runs(tmp_path):
    """The service must never own the GUI app id (else the GUI exits)."""
    import time

    proc, address, env = _run_session_bus(tmp_path)
    service_proc = None
    try:
        db_path = str(tmp_path / "calendar.db")
        service_env = dict(env)
        service_env["DBUS_SESSION_BUS_ADDRESS"] = address
        service_proc = subprocess.Popen(
            ["python3", "-m", "xx_calendar", "--service", "--db", db_path,
             "--idle-timeout", "60"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=service_env, cwd=str(ROOT))
        deadline = time.time() + 15
        owned = False
        while time.time() < deadline:
            probe = subprocess.run(
                ["python3", "-c",
                 "import os,sys; os.environ['DBUS_SESSION_BUS_ADDRESS']="
                 "sys.argv[1]; import gi; "
                 "from gi.repository import Gio; "
                 "bus = Gio.bus_get_sync(Gio.BusType.SESSION, None); "
                 "print(bus.call_sync('org.freedesktop.DBus', "
                 "'/org/freedesktop/DBus', 'org.freedesktop.DBus', "
                 "'NameHasOwner', __import__('gi.repository', fromlist="
                 "['GLib']).GLib.Variant('(s)', ('io.piercingxx.Calendar',)), "
                 "__import__('gi.repository', fromlist=['GLib'])."
                 "GLib.VariantType.new('(b)'), "
                 "Gio.DBusCallFlags.NONE, -1, None).unpack()[0])",
                 address],
                capture_output=True, text=True, env=service_env, timeout=15)
            if "True" in probe.stdout:
                owned = True
                break
            time.sleep(0.2)
        assert owned, "service did not own io.piercingxx.Calendar: %r %r" % (
            probe.stdout, probe.stderr)
        client_env = dict(service_env)
        check = subprocess.run(
            ["python3", "-c",
             "import os,sys; os.environ['DBUS_SESSION_BUS_ADDRESS']="
             "sys.argv[1]; import gi; gi.require_version('Gio', '2.0'); "
             "from gi.repository import Gio; "
             "app = Gio.Application(application_id=sys.argv[2], "
             "flags=Gio.ApplicationFlags.FLAGS_NONE); "
             "assert app.register(None); print('REGISTERED')",
             address, APP_ID],
            capture_output=True, text=True, env=client_env, timeout=30)
        assert "REGISTERED" in check.stdout, check.stderr
    finally:
        if service_proc is not None:
            service_proc.terminate()
            try:
                service_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                service_proc.kill()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
