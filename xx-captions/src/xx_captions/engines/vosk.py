"""Vosk small-model adapter (fallback engine).

Same contract as the sherpa adapter: 16 kHz mono float32 chunks in,
partial Segments from PartialResult, one final Segment per utterance
from Result, word times retained on last_words. Vosk emits lowercase
words without punctuation.

Thread-safety: the engine is driven from ONE worker thread. start,
feed, reset, and stop must all be called from that same thread; they
are not safe against each other from different threads.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from xx_captions.audio import ENGINE_RATE_HZ
from xx_captions.dbus_api import validate_source
from xx_captions.engine import EngineError, Segment

_SENTINELS = ("am/final.mdl", "conf/model.conf")


def model_files_present(model_dir: str | Path) -> bool:
    root = Path(model_dir)
    return all((root / name).is_file() for name in _SENTINELS)


def _float_to_s16_bytes(samples) -> bytes:
    try:
        import numpy as np

        pcm = np.asarray(list(samples), dtype=np.float64)
        if pcm.ndim != 1:
            raise EngineError("samples must be mono (1-D)")
        clipped = np.clip(pcm, -1.0, 1.0)
        return (clipped * 32767).astype(np.int16).tobytes()
    except ImportError:
        import struct

        flat = [float(v) for v in samples]
        clamped = [max(-1.0, min(1.0, v)) for v in flat]
        return struct.pack(f"<{len(clamped)}h",
                           *(int(v * 32767) for v in clamped))


def _default_recognizer_factory(model_dir: Path):
    from vosk import KaldiRecognizer, Model

    model = Model(str(model_dir))
    recognizer = KaldiRecognizer(model, ENGINE_RATE_HZ)
    recognizer.SetWords(True)
    recognizer.SetPartialWords(True)
    return recognizer


class VoskEngine:
    """Kaldi streaming recognizer behind the lane-0 Engine protocol."""

    name = "vosk"

    def __init__(self, model_dir: str | Path,
                 recognizer_factory=None) -> None:
        self._model_dir = Path(model_dir)
        if recognizer_factory is None:
            if not model_files_present(self._model_dir):
                raise EngineError(f"vosk model missing in {self._model_dir}")
            try:
                recognizer = _default_recognizer_factory(self._model_dir)
            except ImportError as exc:
                raise EngineError("vosk package not installed") from exc
        else:
            recognizer = recognizer_factory(self._model_dir)
        self._recognizer = recognizer
        self._on_text: Callable[[Segment], None] | None = None
        self._source: str | None = None
        self._fed_samples = 0
        self._last_partial = ""
        self._last_words: list[dict] = []
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def source(self) -> str | None:
        return self._source

    @property
    def last_words(self) -> list[dict]:
        """Word times ({word, start, end}) from the most recent final."""
        return list(self._last_words)

    def start(self, source: str,
              on_text: Callable[[Segment], None]) -> None:
        validate_source(source)
        if on_text is None or not callable(on_text):
            raise EngineError("on_text callback is required")
        if self._running:
            raise EngineError("engine already running")
        self._recognizer.Reset()
        self._on_text = on_text
        self._source = source
        self._fed_samples = 0
        self._last_partial = ""
        self._last_words = []
        self._running = True

    def feed(self, samples, sample_rate: int = ENGINE_RATE_HZ) -> None:
        """Accept one 16 kHz mono float32 chunk; emits via on_text."""
        if not self._running or self._on_text is None:
            raise EngineError("engine is not running")
        if sample_rate != ENGINE_RATE_HZ:
            raise EngineError(
                f"samples must be {ENGINE_RATE_HZ} Hz, got {sample_rate}")
        blob = _float_to_s16_bytes(samples)
        if not blob:
            return
        self._fed_samples += len(blob) // 2
        offset_ms = int(self._fed_samples * 1000 / ENGINE_RATE_HZ)
        if self._recognizer.AcceptWaveform(blob):
            try:
                result = json.loads(self._recognizer.Result())
            except ValueError as exc:
                raise EngineError(f"vosk returned bad JSON: {exc}") from exc
            text = str(result.get("text", "")).strip()
            words = result.get("result", [])
            self._last_partial = ""
            if text:
                self._last_words = list(words) if isinstance(words, list) else []
                self._on_text(Segment(text, True, offset_ms))
        else:
            try:
                partial = json.loads(self._recognizer.PartialResult())
            except ValueError as exc:
                raise EngineError(f"vosk returned bad JSON: {exc}") from exc
            text = str(partial.get("partial", "")).strip()
            if text and text != self._last_partial:
                self._last_partial = text
                self._on_text(Segment(text, False, offset_ms))

    def reset(self) -> None:
        """Drop the current utterance state; next feed starts fresh."""
        if not self._running:
            raise EngineError("engine is not running")
        self._recognizer.Reset()
        self._last_partial = ""

    def flush(self) -> None:
        """Emit the current hypothesis as final and reset the recognizer."""
        if not self._running or self._on_text is None:
            raise EngineError("engine is not running")
        try:
            result = json.loads(self._recognizer.FinalResult())
        except ValueError as exc:
            raise EngineError(f"vosk returned bad JSON: {exc}") from exc
        text = str(result.get("text", "")).strip()
        words = result.get("result", [])
        self._recognizer.Reset()
        self._last_partial = ""
        if text:
            self._last_words = list(words) if isinstance(words, list) else []
            offset_ms = int(self._fed_samples * 1000 / ENGINE_RATE_HZ)
            self._on_text(Segment(text, True, offset_ms))

    def stop(self) -> None:
        self._on_text = None
        self._source = None
        self._running = False


__all__ = ["VoskEngine", "model_files_present"]
