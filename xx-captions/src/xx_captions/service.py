"""io.piercingxx.Captions1 session service (lane 2: capture + store).

SessionController owns the session state with no D-Bus and no GTK:
one worker pump thread pulls raw chunks from the capture reader(s),
converts them to 16 kHz mono float, and feeds the engine. Engine
segments come back on that same worker thread and are marshalled to
the main loop through an injected post() callable (GLib.idle_add in
production, direct in headless tests).

CaptionsService is the thin Gio wrapper around one controller: it
registers the introspection XML from dbus_api, owns the bus name,
emits Text signals, and answers Properties. Error mapping:
bad source -> InvalidArgs, call without opt-in -> NotPermitted,
unknown transcript -> NotFound, missing audio nodes -> Failed.
"""
from __future__ import annotations

import os
import queue
import threading
from collections.abc import Callable
from pathlib import Path

from xx_captions import audio
from xx_captions import capture as capture_mod
from xx_captions import config
from xx_captions import resample
from xx_captions import store as store_mod
from xx_captions.dbus_api import (
    BUS_NAME,
    ERR_NOT_FOUND,
    ERR_NOT_PERMITTED,
    INTERFACE,
    INTROSPECTION_XML,
    OBJECT_PATH,
    ContractError,
    validate_source,
    validate_text,
)
from xx_captions.engine import EngineError, Segment

ROUTE_NOTE = "audio route changed"
ROUTE_POLL_S = 2.0

_STOP = object()


class NotPermittedError(PermissionError):
    pass


class NoAudioError(RuntimeError):
    pass


def _post_main(callback: Callable[[], None]) -> None:
    """Run callback on the main loop; direct when GTK is unavailable."""
    try:
        from gi.repository import GLib

        GLib.idle_add(lambda: (callback(), False)[1])
    except ImportError:
        callback()


class EventSink:
    """Main-loop side of a session: Text signals plus Active tracking."""

    def on_text(self, text: str, final: bool) -> None:
        raise NotImplementedError

    def on_active(self, active: bool, source: str) -> None:
        raise NotImplementedError


class SessionController:
    """One captions session: capture readers -> one pump -> engine.

    resolve_plan maps a source to audio.CaptureTarget values and is
    re-run on every route poll, so mid-call sink changes restart
    capture instead of misattributing voices. capture_factory builds
    one capture-like (start/stop/running, on Chunk callbacks) per
    session; engine_factory builds an object with start(source,
    on_text)/stop() plus optional feed(samples)/flush().
    """

    def __init__(self, engine_factory: Callable,
                 resolve_plan: Callable[[str], dict] | None = None,
                 capture_factory: Callable | None = None,
                 store: store_mod.TranscriptStore | None = None,
                 home: Path | None = None,
                 sink: EventSink | None = None,
                 post: Callable[[Callable[[], None]], None] | None = None,
                 route_poll_s: float = ROUTE_POLL_S) -> None:
        self._engine_factory = engine_factory
        self._resolve_plan = resolve_plan or (lambda source: {})
        self._capture_factory = capture_factory
        self._store = store
        self._home = home
        self._sink = sink
        self._post = post or _post_main
        self._route_poll_s = route_poll_s
        self._lock = threading.Lock()
        self._engine = None
        self._captures: list = []
        self._pump: threading.Thread | None = None
        self._chunks: queue.Queue = queue.Queue()
        self._active = False
        self._source = ""
        self._call_id: str | None = None
        self._tracker: capture_mod.RouteTracker | None = None
        self._last_final = ""

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def source(self) -> str:
        with self._lock:
            return self._source

    @property
    def last_final(self) -> str:
        with self._lock:
            return self._last_final

    def _emit(self, callback: Callable[[], None]) -> None:
        try:
            self._post(callback)
        except Exception:
            pass

    def _sink_text(self, text: str, final: bool) -> None:
        sink = self._sink
        if sink is None:
            return
        self._emit(lambda: sink.on_text(text, final))

    def _sink_active(self, active: bool, source: str) -> None:
        sink = self._sink
        if sink is None:
            return
        self._emit(lambda: sink.on_active(active, source))

    def start(self, source: str, peer: str = "") -> None:
        """Begin capture+transcription; same-source Start is a no-op."""
        validate_source(source)
        with self._lock:
            if self._active and self._source == source:
                return
            already = self._active
        if already:
            self.stop()
        if source == "call" and not config.load(
                self._home).get("call_transcription_enabled", False):
            raise NotPermittedError(
                "call transcription needs the opt-in setting")
        plan = self._resolve_plan(source)
        if not plan:
            raise NoAudioError(f"no capture target for {source!r}")
        try:
            engine = self._engine_factory()
        except EngineError:
            raise
        except Exception as exc:
            raise NoAudioError(f"no speech engine: {exc}") from exc
        call_id = None
        if source == "call" and self._store is not None:
            call_id = self._store.begin_call(peer)
        with self._lock:
            self._engine = engine
            self._call_id = call_id
            self._source = source
            self._active = True
            self._last_final = ""
            self._chunks = queue.Queue()
            self._tracker = capture_mod.RouteTracker(
                lambda: self._resolve_plan(source))
            self._tracker.check()
        try:
            engine.start(source, self._on_segment)
        except Exception:
            with self._lock:
                self._engine = None
                self._call_id = None
                self._active = False
                self._source = ""
            raise
        self._start_captures(plan)
        with self._lock:
            self._pump = threading.Thread(target=self._run_pump,
                                          daemon=True)
            self._pump.start()
        self._sink_active(True, source)

    def _start_captures(self, plan: dict[str, audio.CaptureTarget]) -> None:
        factory = self._capture_factory or _default_capture_factory
        started: list = []
        try:
            for role, target in plan.items():
                cap = factory({role: target}, self._on_bytes,
                              self._on_capture_end)
                cap.start()
                started.append(cap)
        except Exception:
            for cap in started:
                try:
                    cap.stop()
                except Exception:
                    pass
            raise
        with self._lock:
            self._captures = started

    def _on_bytes(self, chunk: capture_mod.Chunk) -> None:
        try:
            self._chunks.put_nowait(chunk)
        except queue.Full:
            pass

    def _on_capture_end(self) -> None:
        pass

    def _on_segment(self, segment: Segment) -> None:
        try:
            text, final = validate_text(segment.text, segment.final)
        except ContractError:
            return
        offset_ms = int(segment.offset_ms)
        call_id = None
        with self._lock:
            if not self._active:
                return
            if final:
                self._last_final = text
                call_id = self._call_id
        if final and call_id is not None and self._store is not None:
            try:
                self._store.add_line(call_id, text, offset_ms)
            except (store_mod.NotFoundError, ValueError):
                pass
        self._sink_text(text, final)

    def _run_pump(self) -> None:
        feed = getattr(self._engine, "feed", None)
        poll = self._route_poll_s if self._route_poll_s > 0 else None
        while True:
            try:
                chunk = self._chunks.get(timeout=poll)
            except queue.Empty:
                chunk = None
            if chunk is _STOP:
                return
            with self._lock:
                running = self._active and self._pump is not None and (
                    threading.current_thread() is self._pump)
                engine = self._engine
            if not running:
                return
            if chunk is not None and feed is not None and engine is not None:
                try:
                    samples = resample.pcm16_to_mono_float32_16k(
                        chunk.raw, chunk.rate, chunk.channels)
                except ValueError:
                    continue
                try:
                    feed(samples)
                except EngineError:
                    return
            if chunk is None:
                self.recheck_route()

    def recheck_route(self) -> bool:
        """Re-resolve the plan; restart capture on change. For polls."""
        with self._lock:
            if not self._active:
                return False
            tracker = self._tracker
        if tracker is None:
            return False
        plan, changed = tracker.check()
        if not changed:
            return bool(plan)
        with self._lock:
            old = self._captures
            self._captures = []
            call_id = self._call_id
        for cap in old:
            try:
                cap.stop()
            except Exception:
                pass
        if plan:
            try:
                self._start_captures(plan)
            except (capture_mod.CaptureError, NoAudioError):
                pass
        if call_id is not None and self._store is not None:
            try:
                self._store.add_line(call_id, ROUTE_NOTE, 0)
            except (store_mod.NotFoundError, ValueError):
                pass
        return bool(plan)

    def stop(self) -> str:
        """End the session, flush final text. Idempotent."""
        with self._lock:
            if not self._active:
                return ""
            pump = self._pump
            captures = self._captures
            engine = self._engine
            call_id = self._call_id
            wake = self._chunks
            self._pump = None
            self._captures = []
            source = self._source
        try:
            wake.put_nowait(_STOP)
        except queue.Full:
            pass
        for cap in captures:
            try:
                cap.stop()
            except Exception:
                pass
        if pump is not None and pump is not threading.current_thread():
            pump.join(timeout=5.0)
        flush = getattr(engine, "flush", None)
        if flush is not None:
            try:
                flush()
            except EngineError:
                pass
        if engine is not None:
            try:
                engine.stop()
            except EngineError:
                pass
        if call_id is not None and self._store is not None:
            try:
                self._store.end_call(call_id)
            except store_mod.NotFoundError:
                pass
        with self._lock:
            self._engine = None
            self._call_id = None
            self._active = False
            self._source = ""
            final = self._last_final
        self._sink_active(False, source)
        return final

    def list_transcripts(self) -> list[tuple[str, str, int, int]]:
        if self._store is None:
            return []
        return self._store.list_transcripts()

    def get_transcript(self, call_id: str):
        if self._store is None:
            raise store_mod.NotFoundError(
                f"unknown transcript {call_id!r}")
        return self._store.get_transcript(call_id)


def _default_capture_factory(plan: dict[str, audio.CaptureTarget],
                             on_bytes, on_end):
    if os.environ.get("XX_CAPTIONS_FAKE_CAPTURE") == "1":
        captures = [capture_mod.FakeCapture(role, on_bytes,
                                            pace=False)
                    for role in plan]
    else:
        captures = [
            capture_mod.PipeCapture(
                capture_mod.pw_record_argv_for(target.node), role,
                on_bytes, on_end)
            for role, target in plan.items()
        ]
    return capture_mod.CaptureSet(captures)


def _default_engine_factory():
    from xx_captions.engine import FakeEngine
    from xx_captions.engines import select_engine

    if os.environ.get("XX_CAPTIONS_FAKE_ENGINE") == "1":
        return FakeEngine()
    selection = select_engine()
    if selection.engine is None:
        raise NoAudioError(f"no speech engine installed: {selection.reason}")
    return selection.engine


def _default_resolve(source: str) -> dict[str, audio.CaptureTarget]:
    if os.environ.get("XX_CAPTIONS_FAKE_CAPTURE") == "1":
        if source == "call":
            return {"downlink": audio.CaptureTarget("fake-downlink",
                                                    "far end"),
                    "uplink": audio.CaptureTarget("fake-mic", "mic")}
        return {"playback": audio.CaptureTarget("fake-playback",
                                                "system playback")}
    if source == "call":
        return capture_mod.CallPlanResolver().resolve()
    sink = capture_mod.default_sink_from_pactl()
    return capture_mod.resolve_playback(sink,
                                        capture_mod.graph_from_pw_dump())


class CaptionsService(EventSink):
    """Gio wrapper: owns BUS_NAME, answers the pinned interface."""

    def __init__(self, controller: SessionController | None = None,
                 home: Path | None = None) -> None:
        from gi.repository import Gio  # noqa: F401

        self._controller = controller or SessionController(
            _default_engine_factory, _default_resolve,
            _default_capture_factory,
            store_mod.TranscriptStore(home), home, sink=self,
            route_poll_s=ROUTE_POLL_S)
        self._bus = None
        self._reg_id = 0
        self._name_id = 0
        self._owns_name = False

    @property
    def controller(self) -> SessionController:
        return self._controller

    def owns_name(self) -> bool:
        return self._owns_name

    def acquire(self, bus=None) -> None:
        """Register object + own name on the session bus. Test entry."""
        from gi.repository import Gio

        self._bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        iface = node.lookup_interface(INTERFACE)
        self._reg_id = self._bus.register_object(
            OBJECT_PATH, iface, self._on_method_call, None, None)
        self._name_id = Gio.bus_own_name_on_connection(
            self._bus, BUS_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
            self._on_name_acquired, self._on_name_lost)

    def wait_for_name(self, timeout_s: float = 5.0) -> bool:
        """Pump the main context until the bus name resolves or not."""
        import time

        from gi.repository import GLib

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not self._owns_name:
            GLib.main_context_default().iteration(False)
            time.sleep(0.01)
        return self._owns_name

    def _on_name_acquired(self, _conn, _name: str) -> None:
        self._owns_name = True

    def _on_name_lost(self, _conn, _name: str) -> None:
        self._owns_name = False

    def release(self) -> None:
        from gi.repository import Gio

        if self._name_id:
            Gio.bus_unown_name(self._name_id)
            self._name_id = 0
        if self._reg_id and self._bus is not None:
            try:
                self._bus.unregister_object(self._reg_id)
            except Exception:
                pass
            self._reg_id = 0
        self._bus = None
        self._owns_name = False

    def on_text(self, text: str, final: bool) -> None:
        bus = self._bus
        if bus is None:
            return
        try:
            from gi.repository import GLib

            bus.emit_signal(None, OBJECT_PATH, INTERFACE, "Text",
                            GLib.Variant("(sb)", (text, final)))
        except Exception:
            pass

    def on_active(self, active: bool, source: str) -> None:
        bus = self._bus
        if bus is None:
            return
        try:
            from gi.repository import GLib

            bus.emit_signal(
                None, OBJECT_PATH, "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                GLib.Variant("(sa{sv}as)", (
                    INTERFACE,
                    {"Active": GLib.Variant("b", active),
                     "Source": GLib.Variant("s", source)},
                    [])))
        except Exception:
            pass

    def _properties(self) -> dict:
        from gi.repository import GLib

        return {"Active": GLib.Variant("b", self._controller.active),
                "Source": GLib.Variant("s", self._controller.source)}

    def _on_method_call(self, _conn, _sender: str, _path: str,
                        _iface: str, method: str, params,
                        invocation) -> None:
        from gi.repository import Gio, GLib

        try:
            if _iface == "org.freedesktop.DBus.Properties":
                self._handle_properties(method, params, invocation)
            elif method == "Start":
                (source,) = params.unpack()
                try:
                    self._controller.start(str(source))
                except ContractError as exc:
                    invocation.return_error_literal(
                        Gio.dbus_error_quark(),
                        Gio.DBusError.INVALID_ARGS, str(exc))
                    return
                except NotPermittedError as exc:
                    invocation.return_dbus_error(ERR_NOT_PERMITTED,
                                                 str(exc))
                    return
                except (NoAudioError, EngineError) as exc:
                    invocation.return_error_literal(
                        Gio.dbus_error_quark(), Gio.DBusError.FAILED,
                        str(exc))
                    return
                invocation.return_value(None)
            elif method == "Stop":
                self._controller.stop()
                invocation.return_value(None)
            elif method == "ListTranscripts":
                rows = self._controller.list_transcripts()
                invocation.return_value(GLib.Variant(
                    "(a(ssxx))", ([(i, p, s, e) for i, p, s, e in rows],)))
            elif method == "GetTranscript":
                (call_id,) = params.unpack()
                try:
                    peer, started, ended, lines = \
                        self._controller.get_transcript(str(call_id))
                except store_mod.NotFoundError as exc:
                    invocation.return_dbus_error(ERR_NOT_FOUND, str(exc))
                    return
                invocation.return_value(GLib.Variant(
                    "(sxxa(sx))", (peer, started, ended, list(lines))))
            else:
                invocation.return_error_literal(
                    Gio.dbus_error_quark(),
                    Gio.DBusError.UNKNOWN_METHOD,
                    f"Unknown method: {method}")
        except Exception as exc:
            try:
                invocation.return_error_literal(
                    Gio.dbus_error_quark(), Gio.DBusError.FAILED,
                    str(exc))
            except Exception:
                pass

    def _handle_properties(self, method: str, params, invocation) -> None:
        from gi.repository import Gio, GLib

        props = self._properties()
        if method == "Get":
            (iface, name) = params.unpack()
            if iface != INTERFACE or name not in props:
                invocation.return_error_literal(
                    Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_PROPERTY,
                    f"Unknown property: {name}")
                return
            invocation.return_value(GLib.Variant("(v)", (props[name],)))
        elif method == "GetAll":
            (iface,) = params.unpack()
            if iface != INTERFACE:
                invocation.return_error_literal(
                    Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_PROPERTY,
                    f"Unknown interface: {iface}")
                return
            invocation.return_value(GLib.Variant("(a{sv})", (dict(props),)))
        else:
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.PROPERTY_READ_ONLY,
                "Captions properties are read-only")


def create_service(home: Path | None = None) -> CaptionsService:
    """Production service: real engine, PipeWire capture, stored calls."""
    return CaptionsService(home=home)


def serve_forever(home: Path | None = None) -> None:
    """Own the bus name and run the GLib main loop (activation entry)."""
    from gi.repository import GLib

    service = create_service(home)
    service.acquire()
    if not service.wait_for_name():
        raise SystemExit(f"cannot own {BUS_NAME}: another service runs it")
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        service.controller.stop()
        service.release()


__all__ = [
    "ROUTE_NOTE",
    "ROUTE_POLL_S",
    "CaptionsService",
    "EventSink",
    "NoAudioError",
    "NotPermittedError",
    "SessionController",
    "create_service",
    "serve_forever",
]