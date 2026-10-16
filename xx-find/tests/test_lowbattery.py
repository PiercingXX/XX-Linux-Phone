"""Lane 5 low battery: WarningLevel-aware gate plus last-report hook.

Fakes mirror real Gio semantics: `GetAll` replies unpack to a tuple of
out-args and `reply_type` must be a `GLib.VariantType`. No test touches
the real bus, network, GPS, keyring or files outside `tmp_path`.
"""

from __future__ import annotations

from gi.repository import Gio, GLib

from xx_find import schedule as sched

NOW = 1_800_000_000


class FakeReply:
    def __init__(self, payload):
        self._payload = payload

    def unpack(self):
        return self._payload


class FakeBus:
    def __init__(self):
        self.calls = []
        self.handlers = {}
        self.subscriptions = []
        self._next_sub = 1

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

    def signal_subscribe(self, sender, iface, signal, path, arg0,
                         flags, callback, user_data):
        sub_id = self._next_sub
        self._next_sub += 1
        self.subscriptions.append({"sender": sender, "iface": iface,
                                   "signal": signal, "path": path,
                                   "callback": callback,
                                   "user_data": user_data})
        return sub_id

    def signal_unsubscribe(self, sub_id):
        self.subscriptions = [s for s in self.subscriptions
                              if s != sub_id]

    def emit(self, signal, params):
        for sub in self.subscriptions:
            if sub["signal"] == signal:
                sub["callback"](self, ":1.99", sched.LOGIN1_PATH,
                                sub["iface"], signal, params,
                                sub["user_data"])


def _upower_bus(*, pct=42.5, state=2, warning=1, present=True):
    bus = FakeBus()

    def _get_all(params):
        del params
        return FakeReply(({"IsPresent": GLib.Variant("b", present),
                           "Percentage": GLib.Variant("d", pct),
                           "State": GLib.Variant("u", state),
                           "WarningLevel": GLib.Variant("u", warning)},))

    bus.on(sched.PROPS_IFACE, "GetAll", _get_all)
    return bus


def test_warning_levels_gate_even_above_percentage():
    assert sched.is_low_battery(80.0, False, sched.UP_WARNING_LOW) is True
    assert sched.is_low_battery(80.0, False,
                                sched.UP_WARNING_CRITICAL) is True
    assert sched.is_low_battery(80.0, False,
                                sched.UP_WARNING_ACTION) is True
    assert sched.is_low_battery(None, False,
                                sched.UP_WARNING_CRITICAL) is True


def test_non_critical_levels_do_not_gate_alone():
    assert sched.is_low_battery(80.0, False,
                                sched.UP_WARNING_DISCHARGING) is False
    assert sched.is_low_battery(80.0, False,
                                sched.UP_WARNING_NONE) is False
    assert sched.is_low_battery(None, False,
                                sched.UP_WARNING_NONE) is False
    assert sched.is_low_battery(15.0, False,
                                sched.UP_WARNING_NONE) is True
    assert sched.is_low_battery(15.1, False,
                                sched.UP_WARNING_NONE) is False


def test_charging_wins_over_warning_level():
    assert sched.is_low_battery(5.0, True,
                                sched.UP_WARNING_CRITICAL) is False
    assert sched.is_low_battery(5.0, True) is False


def test_snapshot_reads_warning_level():
    snap = sched.battery_snapshot(
        _upower_bus(pct=12.0, state=2, warning=sched.UP_WARNING_LOW))
    assert snap == {"pct": 12.0, "charging": False,
                    "warning_level": sched.UP_WARNING_LOW, "present": True}
    snap = sched.battery_snapshot(_upower_bus(pct=90.0, state=1))
    assert snap["charging"] is True
    empty = FakeBus()
    empty.on(sched.PROPS_IFACE, "GetAll",
             lambda params: FakeReply(({},)))
    assert sched.battery_snapshot(empty)["present"] is False


def test_last_report_hook_sends_through_report_path():
    events, reports = [], []
    attempt = sched.build_last_report_hook(
        battery_probe=lambda: {"pct": 9.0, "charging": False,
                               "warning_level": sched.UP_WARNING_CRITICAL,
                               "present": True},
        read_fix=lambda: {"lat": 1.0, "lon": 2.0, "epoch": NOW},
        send_report=lambda cfg, fix: reports.append(fix),
        config=object(),
        on_privacy_event=lambda kind, app: events.append((kind, app)))
    assert attempt() == {"sent": True, "reason": "last report sent"}
    assert reports[0]["battery_pct"] == 9
    assert reports[0]["lat"] == 1.0
    assert events == [("location", "xx-find-agent")]


def test_last_report_hook_quiet_when_battery_ok():
    calls = []
    attempt = sched.build_last_report_hook(
        battery_probe=lambda: (80.0, False),
        read_fix=lambda: calls.append("fix") or {"lat": 1.0},
        send_report=lambda cfg, fix: calls.append("send"),
        config=object())
    assert attempt() == {"sent": False, "reason": "battery ok"}
    assert calls == []


def test_last_report_hook_never_raises():
    attempt = sched.build_last_report_hook(
        battery_probe=lambda: (5.0, False),
        read_fix=lambda: (_ for _ in ()).throw(RuntimeError("no gps")),
        send_report=lambda cfg, fix: None,
        config=object())
    assert attempt()["sent"] is False

    def _boom(cfg, fix):
        raise OSError("radio off")

    attempt = sched.build_last_report_hook(
        battery_probe=lambda: (5.0, False),
        read_fix=lambda: {"lat": 1.0},
        send_report=_boom, config=object())
    assert attempt()["sent"] is False

    attempt = sched.build_last_report_hook(
        battery_probe=lambda: (_ for _ in ()).throw(
            RuntimeError("no bus")),
        read_fix=lambda: {"lat": 1.0},
        send_report=lambda cfg, fix: None, config=object())
    assert attempt()["sent"] is False


def test_shutdown_hook_fires_only_on_starting():
    bus = FakeBus()
    calls = []
    sub_id = sched.subscribe_shutdown_hook(bus, lambda: calls.append(1))
    assert bus.subscriptions[0]["sender"] is None
    assert bus.subscriptions[0]["path"] == sched.LOGIN1_PATH
    assert bus.subscriptions[0]["iface"] == sched.LOGIN1_MANAGER
    bus.emit("PrepareForShutdown", GLib.Variant("(b)", (False,)))
    assert calls == []
    bus.emit("PrepareForShutdown", GLib.Variant("(b)", (True,)))
    assert calls == [1]
    assert sub_id is not None


def test_shutdown_hook_uses_vartype_reply_type():
    bus = _upower_bus()
    seen = []
    orig = bus.call_sync

    def _checked(name, path, iface, method, params, reply_type,
                 flags, timeout, cancellable):
        seen.append(reply_type)
        return orig(name, path, iface, method, params, reply_type,
                    flags, timeout, cancellable)

    bus.call_sync = _checked
    sched.battery_snapshot(bus)
    assert all(isinstance(item, GLib.VariantType) for item in seen)
    assert Gio is not None
