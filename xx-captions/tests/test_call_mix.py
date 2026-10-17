"""Call mode: downlink+uplink are mixed to one real-time stream.

Regression test: feeding the two 100 ms directions into one engine one
after the other runs the recognizer at twice real time (garbage
transcripts, drifting timestamps). The pump must pair the two sides by
role and feed their mean instead.
"""

import struct
import threading
import time
from pathlib import Path

from xx_captions import capture as capture_mod
from xx_captions import config
from xx_captions.audio import CaptureTarget
from xx_captions.service import SessionController


def _wait(pred, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


class PushCapture:
    """One-role stub; the test pushes chunks with explicit labels."""

    def __init__(self, plan, on_bytes, on_end) -> None:
        assert len(plan) == 1
        self._role = next(iter(plan))
        self._on_bytes = on_bytes
        self._on_end = on_end

    @property
    def running(self) -> bool:
        return True

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def emit(self, raw: bytes, label: str) -> None:
        self._on_bytes(capture_mod.Chunk(raw, 16000, 1, label))


class MixEngine:
    """feed-capable fake that records every fed sample."""

    name = "mix-recorder"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.fed: list[float] = []
        self._running = False

    def start(self, source: str, on_text) -> None:
        self._running = True

    def feed(self, samples) -> None:
        with self._lock:
            self.fed.extend(float(v) for v in samples)

    def stop(self) -> None:
        self._running = False


def _call_plan(source: str) -> dict[str, CaptureTarget]:
    assert source == "call"
    return {"downlink": CaptureTarget("sink", "far end"),
            "uplink": CaptureTarget("mic", "mic")}


def _make(home: Path, engine: MixEngine):
    made: list[PushCapture] = []

    def factory(plan, on_bytes, on_end):
        cap = PushCapture(plan, on_bytes, on_end)
        made.append(cap)
        return cap

    data = config.load(home)
    data["call_transcription_enabled"] = True
    config.save(data, home)
    controller = SessionController(
        lambda: engine, _call_plan, factory, None, home, sink=None,
        post=lambda fn: fn(), route_poll_s=0.0)
    return controller, made


def _tone(value: int, frames: int = 1600) -> bytes:
    return struct.pack("<%dh" % frames, *([value] * frames))


def test_call_directions_are_mixed_not_sequenced(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    engine = MixEngine()
    controller, made = _make(home, engine)
    controller.start("call")
    try:
        assert len(made) == 2
        made[0].emit(_tone(1000), "downlink")
        made[1].emit(_tone(3000), "uplink")
        assert _wait(lambda: len(engine.fed) >= 1600)
        time.sleep(0.3)
        assert len(engine.fed) == 1600
        expected = (1000 / 32768.0 + 3000 / 32768.0) / 2.0
        assert all(abs(v - expected) < 1e-9 for v in engine.fed)
    finally:
        controller.stop()


def test_single_role_still_feeds_directly(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    engine = MixEngine()
    made: list[PushCapture] = []

    def factory(plan, on_bytes, on_end):
        cap = PushCapture(plan, on_bytes, on_end)
        made.append(cap)
        return cap

    controller = SessionController(
        lambda: engine,
        lambda source: {"playback": CaptureTarget("sink", "play")},
        factory, None, home, sink=None, post=lambda fn: fn(),
        route_poll_s=0.0)
    controller.start("playback")
    try:
        made[0].emit(_tone(2000), "playback")
        assert _wait(lambda: len(engine.fed) >= 1600)
        time.sleep(0.2)
        assert len(engine.fed) == 1600
        expected = 2000 / 32768.0
        assert all(abs(v - expected) < 1e-9 for v in engine.fed)
    finally:
        controller.stop()
