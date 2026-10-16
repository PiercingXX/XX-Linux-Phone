"""Async client for io.piercingxx.Captions1 (overlay and shell tile).

Every call follows the shell-trap rules: reply unpacks to a TUPLE of
out-args (first element via dbus_api.unpack_single), reply_type is a
GLib.VariantType or None (never a plain string), signal handlers take
(conn, sender, path, iface, signal, params), and Text is subscribed
with sender=BUS_NAME so only the service's own signals arrive.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xx_captions.dbus_api import (
    BUS_NAME,
    INTERFACE,
    OBJECT_PATH,
    unpack_single,
    validate_source,
    validate_text,
)

PROPS_IFACE = "org.freedesktop.DBus.Properties"

TextFn = Callable[[str, bool], None]
PropsFn = Callable[[bool, str], None]
DoneFn = Callable[[str | None], None]


def start_params(source: str):
    """Variant for Start(source); validates before touching D-Bus."""
    from gi.repository import GLib

    return GLib.Variant("(s)", (validate_source(source),))


def parse_text_params(params) -> tuple[str, bool]:
    """(text, final) from a Text signal's parameters."""
    text, final = params.unpack()
    return validate_text(str(text), bool(final))


def parse_active_source(get_all_unpacked: tuple) -> tuple[bool, str]:
    """(active, source) from a GetAll reply's unpacked tuple."""
    props = unpack_single(get_all_unpacked)
    get = props.get if hasattr(props, "get") else (lambda k, d=None: d)
    active = get("Active", False)
    source = get("Source", "")
    if hasattr(active, "unpack"):
        active = active.unpack()
    if hasattr(source, "unpack"):
        source = source.unpack()
    return bool(active), str(source)


class CaptionsClient:
    """Overlay-side connection: Start/Stop plus Text and property watch.

    Pass a fake bus in tests; otherwise connect() opens the real
    session bus. All callbacks run on the GLib main loop thread.
    """

    def __init__(self, on_text: TextFn | None = None,
                 on_props: PropsFn | None = None,
                 bus: Any = None) -> None:
        self._on_text = on_text
        self._on_props = on_props
        self._bus = bus
        self._subs: list[int] = []
        self.active = False
        self.source = ""

    @property
    def connected(self) -> bool:
        return self._bus is not None

    def connect(self) -> bool:
        """Open the session bus and subscribe; False when unavailable."""
        if self._bus is not None:
            return True
        try:
            from gi.repository import Gio
        except ImportError:
            return False
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            self._bus = None
            return False
        self._subscribe()
        self.refresh_props()
        return True

    def _subscribe(self) -> None:
        from gi.repository import Gio

        bus = self._bus
        self._subs.append(bus.signal_subscribe(
            BUS_NAME, INTERFACE, "Text", OBJECT_PATH,
            None, Gio.DBusSignalFlags.NONE, self._on_text_signal))
        self._subs.append(bus.signal_subscribe(
            BUS_NAME, PROPS_IFACE, "PropertiesChanged", OBJECT_PATH,
            None, Gio.DBusSignalFlags.NONE, self._on_props_signal))

    def _on_text_signal(self, _conn, _sender: str, _path: str,
                        _iface: str, _signal: str, params) -> None:
        try:
            text, final = parse_text_params(params)
        except Exception:
            return
        if self._on_text is not None:
            try:
                self._on_text(text, final)
            except Exception:
                pass

    def _on_props_signal(self, _conn, _sender: str, _path: str,
                         _iface: str, _signal: str, params) -> None:
        try:
            _iface_name, changed, _invalidated = params.unpack()
        except Exception:
            return
        get = changed.get if hasattr(changed, "get") else (lambda k,
                                                           d=None: d)
        active = get("Active", None)
        source = get("Source", None)
        if hasattr(active, "unpack"):
            active = active.unpack()
        if hasattr(source, "unpack"):
            source = source.unpack()
        if active is None and source is None:
            return
        if active is not None:
            self.active = bool(active)
        if source is not None:
            self.source = str(source)
        if self._on_props is not None:
            try:
                self._on_props(self.active, self.source)
            except Exception:
                pass

    def start(self, source: str = "playback",
              done: DoneFn | None = None) -> None:
        """Async Start(source); done(error-text or None) on completion."""
        bus = self._bus
        if bus is None:
            if done is not None:
                done("not connected")
            return
        try:
            params = start_params(source)
        except Exception as exc:
            if done is not None:
                done(str(exc))
            return
        from gi.repository import Gio

        bus.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, "Start", params, None,
            Gio.DBusCallFlags.NONE, 5000, None,
            lambda conn, result: self._finish_call(conn, result, done))

    def stop(self, done: DoneFn | None = None) -> None:
        bus = self._bus
        if bus is None:
            if done is not None:
                done("not connected")
            return
        from gi.repository import Gio

        bus.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, "Stop", None, None,
            Gio.DBusCallFlags.NONE, 5000, None,
            lambda conn, result: self._finish_call(conn, result, done))

    def _finish_call(self, conn, result, done: DoneFn | None) -> None:
        error: str | None = None
        try:
            conn.call_finish(result)
        except Exception as exc:
            error = str(exc)
        if done is not None:
            try:
                done(error)
            except Exception:
                pass

    def refresh_props(self, done: DoneFn | None = None) -> None:
        """Async GetAll for Active/Source; done(error-text or None)."""
        bus = self._bus
        if bus is None:
            if done is not None:
                done("not connected")
            return
        from gi.repository import Gio, GLib

        bus.call(
            BUS_NAME, OBJECT_PATH, PROPS_IFACE, "GetAll",
            GLib.Variant("(s)", (INTERFACE,)),
            GLib.VariantType.new("(a{sv})"),
            Gio.DBusCallFlags.NONE, 5000, None,
            lambda conn, result: self._finish_props(conn, result, done))

    def _finish_props(self, conn, result, done: DoneFn | None) -> None:
        error: str | None = None
        try:
            reply = conn.call_finish(result)
            self.active, self.source = parse_active_source(reply.unpack())
            if self._on_props is not None:
                self._on_props(self.active, self.source)
        except Exception as exc:
            error = str(exc)
        if done is not None:
            try:
                done(error)
            except Exception:
                pass

    def close(self) -> None:
        bus = self._bus
        for sub in self._subs:
            try:
                bus.signal_unsubscribe(sub)
            except Exception:
                pass
        self._subs = []
        self._bus = None


__all__ = [
    "PROPS_IFACE",
    "CaptionsClient",
    "parse_active_source",
    "parse_text_params",
    "start_params",
]
