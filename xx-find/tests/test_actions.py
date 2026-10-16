"""Lane 4: ring/message/lock drivers, modem restore, cadence + budget.

Fakes mirror real Gio semantics per XXWM_RULES traps: replies unpack to
a tuple of out-args, `reply_type` must be a GLib.VariantType, and no
test touches the real bus, network, GPS, keyring, audio or files outside
tmp_path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from gi.repository import GLib

from xx_find import actions as act
from xx_find import agent as agent_mod
from xx_find import config, crypto, protocol
from xx_find import providers as prov
from xx_find import schedule as sched

NOW = 1_800_000_000
MODEM = "/org/freedesktop/ModemManager1/Modem/0"


class FakeReply:
    def __init__(self, payload):
        self._payload = payload

    def unpack(self):
        return self._payload


class FakeBus:
    def __init__(self):
        self.calls = []
        self.handlers = {}

    def on(self, iface, method, handler):
        self.handlers[(iface, method)] = handler
        return self

    def call_sync(self, name, path, iface, method, params, reply_type,
                  flags, timeout, cancellable):
        if reply_type is not None and not isinstance(
                reply_type, GLib.VariantType):
            raise TypeError("reply_type must be GLib.VariantType")
        self.calls.append({"name": name, "path": path, "iface": iface,
                           "method": method,
                           "params": params.unpack() if params is not None
                           else None})
        handler = self.handlers.get((iface, method))
        if handler is None:
            raise GLib.Error("no such method %s" % method)
        return handler(params)


def _nmea_loc():
    return {prov.MM_SOURCE_3GPP: GLib.Variant("s", "214,03,21071,7033737"),
            prov.MM_SOURCE_GPS_NMEA: GLib.Variant(
                "s", "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,"
                     "545.4,M,46.9,M,,*47")}


def make_modem_bus_with_state(*, enabled=0, signals=False, loc=None,
                              fail_get=None):
    bus = FakeBus()
    setups = []

    def _objects(params):
        del params
        return FakeReply(({MODEM: {prov.MM_LOCATION_IFACE: {}}},))

    def _get(params):
        iface, prop = params.unpack()
        assert iface == prov.MM_LOCATION_IFACE
        if prop == "Enabled":
            return FakeReply((GLib.Variant("u", enabled),))
        if prop == "SignalsLocation":
            return FakeReply((GLib.Variant("b", signals),))
        raise GLib.Error("no such property")

    def _setup(params):
        setups.append(params.unpack())
        return None

    def _loc(params):
        del params
        if fail_get is not None:
            raise fail_get
        return FakeReply((loc if loc is not None else _nmea_loc(),))

    bus.on(prov.OBJMGR_IFACE, "GetManagedObjects", _objects)
    bus.on(prov.PROPS_IFACE, "Get", _get)
    bus.on(prov.MM_LOCATION_IFACE, "Setup", _setup)
    bus.on(prov.MM_LOCATION_IFACE, "GetLocation", _loc)
    bus.setups = setups
    return bus


def test_modem_restore_runs_on_success():
    bus = make_modem_bus_with_state(enabled=1, signals=True)
    fix = prov.modem_fix(bus, now=lambda: NOW)
    assert fix is not None and fix["source"] == "modem-gps"
    assert bus.setups[0] == (prov.MM_SETUP_SOURCES, False)
    assert bus.setups[-1] == (1, True)
    assert len(bus.setups) == 2


def test_modem_restore_runs_on_getlocation_error():
    bus = make_modem_bus_with_state(
        enabled=0, signals=False, fail_get=GLib.Error("injected error"))
    assert prov.modem_fix(bus, now=lambda: NOW) is None
    assert bus.setups[0] == (prov.MM_SETUP_SOURCES, False)
    assert bus.setups[-1] == (0, False)
    assert len(bus.setups) == 2


def test_modem_restore_runs_on_timeout():
    timeout = GLib.Error("Timeout was reached")
    bus = make_modem_bus_with_state(
        enabled=4, signals=False, fail_get=timeout)
    assert prov.modem_fix(bus, now=lambda: NOW) is None
    assert bus.setups[0] == (prov.MM_SETUP_SOURCES, False)
    assert bus.setups[-1] == (4, False)


def test_modem_setup_failure_leaves_no_restore():
    bus = make_modem_bus_with_state()
    orig = bus.handlers[(prov.MM_LOCATION_IFACE, "Setup")]

    def _boom(params):
        raise GLib.Error("Setup rejected")

    del orig
    bus.on(prov.MM_LOCATION_IFACE, "Setup", _boom)
    assert prov.modem_fix(bus, now=lambda: NOW) is None


def test_modem_restore_skipped_when_state_unreadable():
    bus = FakeBus()
    setups = []

    def _objects(params):
        del params
        return FakeReply(({MODEM: {prov.MM_LOCATION_IFACE: {}}},))

    def _setup(params):
        setups.append(params.unpack())
        return None

    def _loc(params):
        del params
        return FakeReply((_nmea_loc(),))

    bus.on(prov.OBJMGR_IFACE, "GetManagedObjects", _objects)
    bus.on(prov.MM_LOCATION_IFACE, "Setup", _setup)
    bus.on(prov.MM_LOCATION_IFACE, "GetLocation", _loc)
    fix = prov.modem_fix(bus, now=lambda: NOW)
    assert fix is not None
    assert setups == [(prov.MM_SETUP_SOURCES, False)]


def _feedback_bus(*, trigger_ok=True):
    bus = FakeBus()
    ended = []

    def _trigger(params):
        app_id, event, hints, timeout = params.unpack()
        assert event == act.RINGER_EVENT
        assert timeout == 0
        del app_id
        assert hints == {"profile": "full", "important": True}
        if not trigger_ok:
            raise GLib.Error("no feedbackd")
        return FakeReply((77,))

    def _end(params):
        (fid,) = params.unpack()
        ended.append(fid)
        return None

    bus.on(act.FEEDBACK_IFACE, "TriggerFeedback", _trigger)
    bus.on(act.FEEDBACK_IFACE, "EndFeedback", _end)
    bus.ended = ended
    return bus


def test_ring_hints_request_full_profile_with_important():
    params = act.ring_feedback_params(act.FIND_APP_ID, act.RINGER_EVENT)
    app_id, event, hints, timeout = params.unpack()
    assert app_id == act.FIND_APP_ID
    assert event == act.RINGER_EVENT
    assert timeout == 0
    assert hints == {"profile": "full", "important": True}


def test_ring_hints_built_in_one_variant_step():
    hints = act.ring_feedback_hints()
    assert hints["profile"].unpack() == "full"
    assert hints["important"].unpack() is True
    params = GLib.Variant(
        "(ssa{sv}i)",
        (act.FIND_APP_ID, act.RINGER_EVENT, hints, 0))
    assert params.unpack()[2] == {"profile": "full", "important": True}


def test_ring_drives_feedbackd_then_ends():
    bus = _feedback_bus()
    slept = []
    ring = act.build_ring_action(
        session_bus_factory=lambda: bus,
        sleep=lambda s: slept.append(s), duration_s=60.0)
    ring(full_volume=True)
    assert slept == [60.0]
    assert bus.ended == [77]
    methods = [c["method"] for c in bus.calls]
    assert methods == ["TriggerFeedback", "EndFeedback"]
    params = bus.calls[0]["params"]
    assert params[1] == act.RINGER_EVENT and params[3] == 0


def test_ring_falls_back_to_sound_loop_without_feedbackd():
    bus = _feedback_bus(trigger_ok=False)
    played, stopped, slept = [], [], []
    ring = act.build_ring_action(
        session_bus_factory=lambda: bus,
        play=lambda: played.append("play"),
        stop=lambda: stopped.append("stop"),
        sleep=lambda s: slept.append(s), duration_s=30.0)
    ring(full_volume=True)
    assert played == ["play"] and stopped == ["stop"]
    assert slept == [30.0]
    assert bus.ended == []


def test_ring_without_bus_uses_fallback():
    played, stopped = [], []
    ring = act.build_ring_action(
        session_bus_factory=lambda: (_ for _ in ()).throw(
            GLib.Error("no bus")),
        play=lambda: played.append(1), stop=lambda: stopped.append(1),
        sleep=lambda s: None, duration_s=5.0)
    ring(full_volume=True)
    assert played == [1] and stopped == [1]


def test_ring_end_runs_when_sleep_raises():
    bus = _feedback_bus()
    stopped = []
    ring = act.build_ring_action(
        session_bus_factory=lambda: bus,
        play=lambda: None, stop=lambda: stopped.append(1),
        sleep=lambda s: (_ for _ in ()).throw(RuntimeError("boom")),
        duration_s=5.0)
    with pytest.raises(RuntimeError):
        ring()
    assert bus.ended == [77]


def test_actions_write_no_mixer_volumes():
    src = Path(act.__file__).read_text(encoding="utf-8").lower()
    for banned in ("amixer", "alsamixer", "set-sink-volume",
                   "set-source-volume", "set-sink-mute",
                   "set-source-mute", "wpctl set-volume",
                   "pactl set-", "volume up", "volume+"):
        assert banned not in src, banned


def test_actions_import_touches_no_bus():
    src = Path(act.__file__).read_text(encoding="utf-8")
    top = [line for line in src.splitlines()
           if line.startswith("import ") or line.startswith("from ")]
    assert not any("secretstorage" in line for line in top)
    assert "bus_get_sync" not in "\n".join(top)


def test_message_format_clips_and_coerces():
    record = act.format_find_message("  hello   world  ", "+1 555 0100",
                                     NOW)
    assert record == {"v": 1, "text": "hello world",
                      "callback": "+1 555 0100", "epoch": NOW}
    long_text = "x" * 500
    record = act.format_find_message(long_text, "y" * 200, "bad")
    assert len(record["text"]) == act.MESSAGE_TEXT_MAX
    assert len(record["callback"]) == act.CALLBACK_MAX
    assert record["epoch"] == 0


def test_message_write_read_clear_round_trip(tmp_path):
    record = act.format_find_message("lost phone", "+15550100", NOW)
    target = act.write_find_message(tmp_path, record)
    assert target.stat().st_mode & 0o777 == 0o600
    assert act.read_find_message(tmp_path) == record
    act.clear_find_message(tmp_path)
    assert act.read_find_message(tmp_path) is None


def test_message_action_nudges_lock(tmp_path):
    nudges = []
    show = act.build_message_action(
        state_dir_factory=lambda: tmp_path,
        ipc_sender=lambda cmd: nudges.append(cmd),
        now=lambda: NOW)
    show("please call me", "+15550100")
    assert nudges == ["lock"]
    assert act.read_find_message(tmp_path)["text"] == "please call me"


def test_message_ipc_failure_still_persists(tmp_path):
    def _boom(cmd):
        del cmd
        raise OSError("no shell")

    show = act.build_message_action(
        state_dir_factory=lambda: tmp_path, ipc_sender=_boom,
        now=lambda: NOW)
    show("hi", "")
    assert act.read_find_message(tmp_path)["text"] == "hi"


def test_lock_prefers_shell_ipc():
    calls = []
    lock = act.build_lock_action(
        ipc_sender=lambda cmd: calls.append(cmd) or True,
        system_bus_factory=lambda: (_ for _ in ()).throw(
            AssertionError("bus must not be touched")))
    lock()
    assert calls == ["lock"]


def test_lock_falls_back_to_logind():
    bus = FakeBus()
    locked = []

    def _lock_sessions(params):
        assert params is None
        locked.append(True)
        return None

    bus.on(act.LOGIN1_MANAGER, "LockSessions", _lock_sessions)
    lock = act.build_lock_action(
        ipc_sender=lambda cmd: False,
        system_bus_factory=lambda: bus)
    lock()
    assert locked == [True]
    assert bus.calls[0]["method"] == "LockSessions"


def test_lock_raises_when_both_paths_fail():
    lock = act.build_lock_action(
        ipc_sender=lambda cmd: False,
        system_bus_factory=lambda: (_ for _ in ()).throw(
            GLib.Error("no bus")))
    with pytest.raises(RuntimeError):
        lock()


def test_lock_reply_type_is_vartype_or_none():
    bus = FakeBus()
    seen = []

    def _lock_sessions(params):
        del params
        return None

    orig_call = bus.call_sync

    def _checked(name, path, iface, method, params, reply_type,
                 flags, timeout, cancellable):
        seen.append(reply_type)
        return orig_call(name, path, iface, method, params, reply_type,
                         flags, timeout, cancellable)

    bus.on(act.LOGIN1_MANAGER, "LockSessions", _lock_sessions)
    bus.call_sync = _checked
    act.build_lock_action(
        ipc_sender=lambda cmd: False,
        system_bus_factory=lambda: bus)()
    assert seen == [None]


def _paired_cfg():
    verify_hex, signing_hex = crypto.generate_owner_signing_keypair()
    cfg = config.AgentConfig(
        enabled=True, server_url="http://nas:8080", device_id="phone-1",
        owner_public_key_hex=verify_hex)
    return cfg, signing_hex


def test_agent_dispatches_lane4_actions():
    cfg, signing_hex = _paired_cfg()
    rang, shown, locked = [], [], []
    deps = agent_mod.AgentDeps(
        now=lambda: NOW, ring=lambda full_volume: rang.append(full_volume),
        show_message=lambda t, cb: shown.append((t, cb)),
        lock_now=lambda: locked.append("lock"))
    handled = agent_mod.Agent(cfg, deps)
    for cmd_type, args in (("ring", {}),
                           ("message", {"text": "hi", "callback": "+1"}),
                           ("lock", {})):
        cmd = protocol.build_command("phone-1", 1 + len(rang) + len(shown)
                                     + len(locked), NOW + 120, cmd_type,
                                     args)
        out = handled.handle_command(protocol.sign_envelope(cmd,
                                                            signing_hex))
        assert out["ok"] is True, (cmd_type, out)
    assert rang == [True]
    assert shown == [("hi", "+1")]
    assert locked == ["lock"]


def test_jitter_bounds():
    assert sched.jittered(900.0, 0.0) == pytest.approx(675.0)
    assert sched.jittered(900.0, 0.5) == pytest.approx(900.0)
    assert sched.jittered(900.0, 0.9999) == pytest.approx(1125.0, rel=1e-3)
    with pytest.raises(ValueError):
        sched.jittered(900.0, 1.0)
    with pytest.raises(ValueError):
        sched.jittered(0, 0.5)


def test_backoff_doubles_then_caps():
    assert sched.poll_delay(active=False, failures=0,
                            rand=0.5) == pytest.approx(900.0)
    assert sched.poll_delay(active=False, failures=1,
                            rand=0.5) == pytest.approx(1800.0)
    assert sched.poll_delay(active=False, failures=9,
                            rand=0.5) == pytest.approx(3600.0)
    assert sched.poll_delay(active=True, failures=0,
                            rand=0.5) == pytest.approx(360.0)
    assert sched.poll_delay(active=True, failures=0,
                            rand=0.0) == pytest.approx(270.0)
    assert sched.poll_delay(active=True, failures=0,
                            rand=0.9999) == pytest.approx(450.0, rel=1e-3)


def test_heartbeat_and_low_battery_gates():
    assert sched.heartbeat_due(0, NOW) is True
    assert sched.heartbeat_due(NOW - 100, NOW) is False
    assert sched.heartbeat_due(NOW - int(sched.HEARTBEAT_S) - 1,
                               NOW) is True
    assert sched.is_low_battery(15.0, False) is True
    assert sched.is_low_battery(14.9, False) is True
    assert sched.is_low_battery(15.1, False) is False
    assert sched.is_low_battery(10.0, True) is False
    assert sched.is_low_battery(None, False) is False


def test_budget_report_math_and_logging(caplog):
    report = sched.budget_report()
    assert report["idle_polls_per_day"] == pytest.approx(96.0)
    assert report["radio_s_per_day"] == pytest.approx(960.0)
    assert report["idle_target_pct_per_day"] == 1.0
    assert report["active_target_pct_per_hour"] == 3.0
    with caplog.at_level("INFO", logger="xx-find.schedule"):
        logged = sched.log_budget()
    assert logged == report
    assert any("idle polls/day" in rec.message for rec in caplog.records)


def test_battery_probe_shapes():
    upower = FakeBus()

    def _get_all(params):
        del params
        return FakeReply(({"IsPresent": GLib.Variant("b", True),
                           "Percentage": GLib.Variant("d", 42.5),
                           "State": GLib.Variant("u", 1)},))

    upower.on(sched.PROPS_IFACE, "GetAll", _get_all)
    probe = sched.build_battery_probe(
        system_bus_factory=lambda: upower)
    assert probe() == (42.5, True)

    empty = FakeBus()
    empty.on(sched.PROPS_IFACE, "GetAll",
             lambda params: FakeReply(({},)))
    assert sched.build_battery_probe(
        system_bus_factory=lambda: empty)() == (None, False)
    assert sched.build_battery_probe(
        system_bus_factory=lambda: (_ for _ in ()).throw(
            GLib.Error("no bus")))() == (None, False)


def test_schedule_next_wake_is_pure():
    decision = sched.next_wake(active=True, failures=0, rand=0.5,
                               now=lambda: float(NOW))
    assert decision["delay_s"] == pytest.approx(360.0)
    assert decision["at"] == pytest.approx(float(NOW) + 360.0)


def test_schedule_import_touches_no_bus():
    src = Path(sched.__file__).read_text(encoding="utf-8")
    top = [line for line in src.splitlines()
           if line.startswith("import ") or line.startswith("from ")]
    assert "bus_get_sync" not in "\n".join(top)
