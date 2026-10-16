from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from xx_captions import capture as capture_mod
from xx_captions import config
from xx_captions.audio import CaptureTarget
from xx_captions.dbus_api import ContractError
from xx_captions.engine import EngineError, FakeEngine, Segment
from xx_captions.service import (
    ROUTE_NOTE,
    NoAudioError,
    NotPermittedError,
    SessionController,
)
from xx_captions.store import TranscriptStore


def _wait(pred, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[tuple[str, bool]] = []
        self.active_events: list[tuple[bool, str]] = []
        self._lock = threading.Lock()

    def on_text(self, text: str, final: bool) -> None:
        with self._lock:
            self.texts.append((text, final))

    def on_active(self, active: bool, source: str) -> None:
        with self._lock:
            self.active_events.append((active, source))


class StubCapture:
    """Silent capture-like; the test pushes bytes explicitly."""

    def __init__(self, plan, on_bytes, on_end) -> None:
        self.plan = dict(plan)
        self._on_bytes = on_bytes
        self._on_end = on_end
        self._running = False
        self.stops = 0

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self.stops += 1
        self._running = False

    def emit(self, raw: bytes = b"\x00\x00" * 1600, rate: int = 16000,
             channels: int = 1, label: str = "playback") -> None:
        self._on_bytes(capture_mod.Chunk(raw, rate, channels, label))


class FlushEngine:
    """feed/flush-capable fake: chunks in, one final out on flush()."""

    name = "flush-fake"

    def __init__(self) -> None:
        self._on_text = None
        self._source: str | None = None
        self._fed = 0
        self._flushed = False
        self.feed_threads: set[int] = set()
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._on_text is not None

    def start(self, source: str, on_text) -> None:
        if self._on_text is not None:
            raise EngineError("engine already running")
        self._on_text = on_text
        self._source = source

    def feed(self, samples) -> None:
        with self._lock:
            if self._on_text is None:
                raise EngineError("engine is not running")
            self.feed_threads.add(threading.get_ident())
            self._fed += len(list(samples))

    def flush(self) -> None:
        with self._lock:
            if self._on_text is None:
                raise EngineError("engine is not running")
            if self._fed and not self._flushed:
                self._flushed = True
                offset = int(self._fed * 1000 / 16000)
                self._on_text(Segment("flushed final", True, offset))

    def stop(self) -> None:
        self._on_text = None
        self._source = None


def _playback_plan(source: str) -> dict[str, CaptureTarget]:
    return {"playback": CaptureTarget("sink.monitor", "system playback")}


def _make(home: Path, engine_factory, resolve=None,
          captures: list | None = None,
          store: TranscriptStore | bool | None = None,
          route_poll_s: float = 0.0) -> tuple[SessionController,
                                              RecordingSink]:
    made: list = [] if captures is None else captures
    sink = RecordingSink()

    def factory(plan, on_bytes, on_end):
        cap = StubCapture(plan, on_bytes, on_end)
        made.append(cap)
        return cap

    if store is True:
        store = TranscriptStore(home)
    controller = SessionController(
        engine_factory, resolve or _playback_plan, factory, store, home,
        sink=sink, post=lambda fn: fn(), route_poll_s=route_poll_s)
    return controller, sink


@pytest.fixture
def home(tmp_path: Path) -> Path:
    fake = tmp_path / "home"
    fake.mkdir()
    return fake


def test_start_emits_partial_then_final(home: Path) -> None:
    controller, sink = _make(home, FakeEngine)
    controller.start("playback")
    try:
        assert _wait(lambda: len(sink.texts) >= 2)
        assert sink.texts[0] == ("hello", False)
        assert sink.texts[1] == ("hello world", True)
        assert controller.active is True
        assert controller.source == "playback"
        assert sink.active_events[0] == (True, "playback")
    finally:
        assert controller.stop() == "hello world"
    assert sink.active_events[-1] == (False, "playback")
    assert controller.active is False


def test_stop_flushes_final_text(home: Path) -> None:
    engine = FlushEngine()
    made: list[StubCapture] = []
    controller, sink = _make(home, lambda: engine, captures=made)
    controller.start("playback")
    try:
        made[0].emit()
        assert _wait(lambda: engine._fed > 0)
    finally:
        final = controller.stop()
    assert final == "flushed final"
    assert ("flushed final", True) in sink.texts


def test_engine_fed_from_one_worker_thread(home: Path) -> None:
    engine = FlushEngine()
    made: list[StubCapture] = []
    controller, sink = _make(home, lambda: engine, captures=made)
    controller.start("mic")
    try:
        for _ in range(5):
            made[0].emit()
        assert _wait(lambda: engine._fed >= 5 * 1600)
    finally:
        controller.stop()
    assert len(engine.feed_threads) == 1
    assert next(iter(engine.feed_threads)) != threading.get_ident()


def test_bad_source_rejected(home: Path) -> None:
    controller, _ = _make(home, FakeEngine)
    with pytest.raises(ContractError):
        controller.start("bluetooth")
    assert controller.active is False


def test_missing_audio_refuses_start(home: Path) -> None:
    controller, _ = _make(home, FakeEngine, resolve=lambda source: {})
    with pytest.raises(NoAudioError):
        controller.start("playback")
    assert controller.active is False


def test_call_needs_opt_in(home: Path) -> None:
    controller, _ = _make(home, FakeEngine,
                          resolve=lambda source: {
                              "downlink": CaptureTarget("sink.monitor",
                                                        "far end"),
                              "uplink": CaptureTarget("mic", "mic")})
    with pytest.raises(NotPermittedError):
        controller.start("call")
    data = config.load(home)
    data["call_transcription_enabled"] = True
    config.save(data, home)
    controller.start("call")
    try:
        assert controller.active is True
    finally:
        controller.stop()


def test_only_calls_with_opt_in_are_stored(home: Path) -> None:
    data = config.load(home)
    data["call_transcription_enabled"] = True
    config.save(data, home)
    db = TranscriptStore(home)
    try:
        def resolve(source: str) -> dict[str, CaptureTarget]:
            if source == "call":
                return {"downlink": CaptureTarget("sink.monitor",
                                                  "far end"),
                        "uplink": CaptureTarget("mic", "mic")}
            return {"playback": CaptureTarget("sink.monitor", "system")}

        controller, sink = _make(home, FakeEngine, resolve=resolve,
                                 store=db)
        controller.start("playback")
        assert _wait(lambda: len(sink.texts) >= 2)
        controller.stop()
        assert db.list_transcripts() == []
        controller.start("call")
        assert _wait(lambda: len(sink.texts) >= 4)
        controller.stop()
        rows = db.list_transcripts()
        assert len(rows) == 1
        peer, started, ended, lines = db.get_transcript(rows[0][0])
        assert lines == [("hello world", 500)]
        assert ended >= started
    finally:
        db.close()


def test_same_source_start_is_noop(home: Path) -> None:
    created: list[FakeEngine] = []
    controller, sink = _make(home, lambda: created.append(FakeEngine())
                             or created[-1])
    controller.start("playback")
    try:
        assert _wait(lambda: len(sink.texts) >= 2)
        count = len(sink.texts)
        controller.start("playback")
        time.sleep(0.2)
        assert len(created) == 1
        assert len(sink.texts) == count
    finally:
        controller.stop()


def test_different_source_restarts(home: Path) -> None:
    controller, sink = _make(home, FakeEngine)
    controller.start("playback")
    try:
        assert _wait(lambda: len(sink.texts) >= 2)
        controller.start("mic")
        assert controller.source == "mic"
        assert _wait(lambda: len(sink.texts) >= 4)
    finally:
        controller.stop()


def test_stop_is_idempotent(home: Path) -> None:
    controller, _ = _make(home, FakeEngine)
    assert controller.stop() == ""
    controller.start("playback")
    controller.stop()
    assert controller.stop() == ""


def test_route_change_restarts_capture_and_notes_gap(home: Path) -> None:
    data = config.load(home)
    data["call_transcription_enabled"] = True
    config.save(data, home)
    db = TranscriptStore(home)
    plan_a = {"downlink": CaptureTarget("sink-a.monitor", "far end"),
              "uplink": CaptureTarget("mic", "mic")}
    plan_b = {"downlink": CaptureTarget("sink-b.monitor", "far end"),
              "uplink": CaptureTarget("mic", "mic")}
    plans = [plan_a, plan_a, plan_b]
    made: list[StubCapture] = []
    controller, _ = _make(home, FakeEngine,
                          resolve=lambda source: plans.pop(0)
                          if plans else plan_b,
                          captures=made, store=db, route_poll_s=0.0)
    try:
        controller.start("call")
        first = list(made)
        assert len(first) == 2
        assert controller.recheck_route() is True
        assert len(made) == 4
        assert [cap.stops for cap in first] == [1, 1]
        rows = db.list_transcripts()
        assert len(rows) == 1
        _, _, _, lines = db.get_transcript(rows[0][0])
        assert (ROUTE_NOTE, 0) in lines
    finally:
        controller.stop()
        db.close()
