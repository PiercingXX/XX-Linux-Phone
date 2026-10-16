"""Client contract: exact D-Bus payloads the overlay and shell tile send.

A fake bus records every call/subscribe so the suite pins sender
filtering, VariantType reply types (never plain strings), two-arg
async callbacks, and tuple reply unpacking without a live bus.
"""
from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")

from gi.repository import GLib  # noqa: E402

from xx_captions import client  # noqa: E402
from xx_captions.dbus_api import (  # noqa: E402
    BUS_NAME,
    INTERFACE,
    OBJECT_PATH,
)


class FakeParams:
    def __init__(self, values: tuple) -> None:
        self._values = values

    def unpack(self):
        return self._values


class FakeCallResult:
    def __init__(self, reply=None, error: Exception | None = None) -> None:
        self._reply = reply
        self._error = error


class FakeBus:
    """Duck-typed Gio.DBusConnection with the REAL call/subscribe shape."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.subs: list[dict] = []
        self.unsubs: list[int] = []
        self._next_sub = 1
        self._handlers: dict[int, object] = {}
        self._pending: list[tuple] = []

    def call(self, bus_name, object_path, interface, method, params,
             reply_type, flags, timeout_msec, cancellable, callback,
             *user_data):
        assert not user_data, "no trailing user_data: callback takes 2 args"
        self.calls.append({
            "bus": bus_name, "path": object_path, "iface": interface,
            "method": method, "params": params, "reply_type": reply_type,
        })
        self._pending.append((callback, FakeCallResult(GLib.Variant(
            "()", ()))))
        return 1

    def run_pending(self) -> None:
        pending, self._pending = self._pending, []
        for callback, result in pending:
            callback(self, result)

    def call_finish(self, result):
        if result._error is not None:
            raise result._error
        return result._reply

    def signal_subscribe(self, sender, iface, signal, path, arg0, flags,
                         callback, *user_data):
        assert not user_data, "no trailing user_data: handler takes 6 args"
        sub = self._next_sub
        self._next_sub += 1
        self.subs.append({"sender": sender, "iface": iface,
                          "signal": signal, "path": path})
        self._handlers[sub] = callback
        return sub

    def signal_unsubscribe(self, sub: int) -> None:
        self.unsubs.append(sub)

    def emit(self, sub: int, params) -> None:
        self._handlers[sub](self, ":1.99", OBJECT_PATH, INTERFACE,
                            "Text", params)


def test_start_sends_validated_playback_source() -> None:
    bus = FakeBus()
    done: list = []
    cli = client.CaptionsClient(bus=bus)
    cli.start("playback", done=done.append)
    assert len(bus.calls) == 1
    call = bus.calls[0]
    assert (call["bus"], call["path"], call["iface"],
            call["method"]) == (BUS_NAME, OBJECT_PATH, INTERFACE, "Start")
    assert call["params"].unpack() == ("playback",)
    assert call["reply_type"] is None
    bus.run_pending()
    assert done == [None]


def test_start_rejects_bad_source_without_bus_traffic() -> None:
    bus = FakeBus()
    done: list = []
    cli = client.CaptionsClient(bus=bus)
    cli.start("bluetooth", done=done.append)
    assert bus.calls == []
    assert len(done) == 1 and done[0]


def test_stop_needs_no_reply_type() -> None:
    bus = FakeBus()
    done: list = []
    cli = client.CaptionsClient(bus=bus)
    cli.stop(done=done.append)
    assert len(bus.calls) == 1
    assert bus.calls[0]["method"] == "Stop"
    assert bus.calls[0]["reply_type"] is None
    bus.run_pending()
    assert done == [None]


def test_no_plain_string_reply_type_anywhere() -> None:
    bus = FakeBus()
    cli = client.CaptionsClient(bus=bus)
    cli.start("mic")
    cli.stop()
    cli.refresh_props()
    for call in bus.calls:
        reply_type = call["reply_type"]
        assert reply_type is None or isinstance(
            reply_type, GLib.VariantType), call


def test_text_subscribe_filters_on_service_sender() -> None:
    bus = FakeBus()
    seen: list = []
    cli = client.CaptionsClient(on_text=seen.append, bus=None)
    cli._bus = bus
    cli._subscribe()
    text_subs = [s for s in bus.subs if s["signal"] == "Text"]
    assert len(text_subs) == 1
    assert text_subs[0]["sender"] == BUS_NAME
    assert text_subs[0]["path"] == OBJECT_PATH


def test_text_signal_updates_in_order() -> None:
    bus = FakeBus()
    seen: list = []
    cli = client.CaptionsClient(on_text=lambda t, f: seen.append((t, f)),
                                bus=bus)
    cli._subscribe()
    sub = next(i for i, s in
               zip(bus._handlers, bus.subs) if s["signal"] == "Text")
    bus.emit(sub, FakeParams(("hello", False)))
    bus.emit(sub, FakeParams(("hello world", True)))
    assert seen == [("hello", False), ("hello world", True)]


def test_bad_text_payloads_are_dropped() -> None:
    bus = FakeBus()
    seen: list = []
    cli = client.CaptionsClient(on_text=lambda t, f: seen.append((t, f)),
                                bus=bus)
    cli._subscribe()
    sub = next(i for i, s in
               zip(bus._handlers, bus.subs) if s["signal"] == "Text")
    bus.emit(sub, FakeParams(("", True)))
    bus.emit(sub, GLib.Variant("(sb)", ("", True)))

    class _Exploding:
        def unpack(self):
            raise ValueError("nope")

    bus.emit(sub, _Exploding())
    assert seen == []


def test_parse_text_params_uses_real_variants() -> None:
    params = GLib.Variant("(sb)", ("hello", False))
    assert client.parse_text_params(params) == ("hello", False)


def test_parse_active_source_unpacks_first_out_arg() -> None:
    reply = GLib.Variant("(a{sv})", ({
        "Active": GLib.Variant("b", True),
        "Source": GLib.Variant("s", "playback"),
    },))
    assert client.parse_active_source(reply.unpack()) == (True, "playback")


def test_refresh_props_asks_getall_with_variant_reply_type() -> None:
    bus = FakeBus()
    cli = client.CaptionsClient(bus=bus)
    cli.refresh_props()
    assert len(bus.calls) == 1
    call = bus.calls[0]
    assert call["method"] == "GetAll"
    assert isinstance(call["reply_type"], GLib.VariantType)
    assert call["reply_type"].dup_string() == "(a{sv})"
    assert call["params"].unpack() == (INTERFACE,)


def test_app_id_differs_from_service_name() -> None:
    from xx_captions import overlay

    assert overlay.APPLICATION_ID == "io.piercingxx.Captions"
    assert overlay.APPLICATION_ID != BUS_NAME
