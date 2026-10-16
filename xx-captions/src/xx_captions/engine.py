"""Speech-to-text engine adapter interface plus a scripted fake.

Real backends (sherpa-onnx default, Vosk fallback) live behind this
interface in lane 1; the fake lets the D-Bus service, overlay, and
transcript store develop and test with no model and no audio hardware.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from xx_captions.dbus_api import SOURCES, validate_source


@dataclass(frozen=True)
class Segment:
    text: str
    final: bool
    offset_ms: int = 0


class Engine(Protocol):
    name: str

    def start(self, source: str, on_text: Callable[[Segment], None]) -> None:
        ...

    def stop(self) -> None:
        ...

    @property
    def running(self) -> bool:
        ...


class EngineError(RuntimeError):
    pass


class FakeEngine:
    """Deterministic scripted engine: each queued segment is emitted once,
    partials before finals, on a worker thread so callers never block."""

    name = "fake"

    def __init__(self, script: list[Segment] | None = None) -> None:
        self._script = list(script) if script is not None else [
            Segment("hello", False, 0),
            Segment("hello world", True, 500),
        ]
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._source: str | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def source(self) -> str | None:
        with self._lock:
            return self._source

    def start(self, source: str, on_text: Callable[[Segment], None]) -> None:
        validate_source(source)
        if on_text is None or not callable(on_text):
            raise EngineError("on_text callback is required")
        with self._lock:
            if self._thread is not None:
                raise EngineError("engine already running")
            self._source = source
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, args=(on_text,), daemon=True,
            )
            self._thread.start()

    def _run(self, on_text: Callable[[Segment], None]) -> None:
        for segment in self._script:
            if self._stop_event.is_set():
                break
            try:
                on_text(segment)
            except Exception:
                break

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
            self._source = None
        self._stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def wait(self, timeout: float = 5.0) -> bool:
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()


__all__ = ["SOURCES", "Engine", "EngineError", "FakeEngine", "Segment"]
