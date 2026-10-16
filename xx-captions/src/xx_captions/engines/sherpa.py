"""sherpa-onnx streaming Zipformer adapter (default engine).

Feeds 16 kHz mono float32 chunks into an online transducer stream and
emits partial Segments as words arrive plus one final Segment per
utterance when the recognizer's endpoint rules fire, resetting the
stream for the next utterance.

Thread-safety: the engine is driven from ONE worker thread. start,
feed, reset, and stop must all be called from that same thread; they
are not safe against each other from different threads.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from xx_captions.audio import ENGINE_RATE_HZ
from xx_captions.dbus_api import validate_source
from xx_captions.engine import EngineError, Segment

def model_files_present(model_dir: str | Path) -> bool:
    root = Path(model_dir)
    return all((root / name).is_file()
               for name in ("encoder.onnx", "decoder.onnx",
                            "joiner.onnx", "tokens.txt"))


def _default_recognizer(model_dir: Path, num_threads: int,
                        rule1: float, rule2: float, rule3: float):
    import sherpa_onnx

    root = str(model_dir)
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=f"{root}/tokens.txt",
        encoder=f"{root}/encoder.onnx",
        decoder=f"{root}/decoder.onnx",
        joiner=f"{root}/joiner.onnx",
        num_threads=num_threads,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=rule1,
        rule2_min_trailing_silence=rule2,
        rule3_min_utterance_length=rule3,
    )


class SherpaEngine:
    """Online Zipformer behind the lane-0 Engine protocol plus feed()."""

    name = "sherpa-onnx"

    def __init__(self, model_dir: str | Path, num_threads: int = 2,
                 rule1_min_trailing_silence: float = 2.4,
                 rule2_min_trailing_silence: float = 1.2,
                 rule3_min_utterance_length: float = 20.0,
                 recognizer_factory=None) -> None:
        self._model_dir = Path(model_dir)
        if recognizer_factory is None:
            if not model_files_present(self._model_dir):
                raise EngineError(
                    f"sherpa model missing in {self._model_dir}")
            try:
                recognizer = _default_recognizer(
                    self._model_dir, num_threads,
                    rule1_min_trailing_silence,
                    rule2_min_trailing_silence,
                    rule3_min_utterance_length)
            except ImportError as exc:
                raise EngineError("sherpa_onnx package not installed") from exc
        else:
            recognizer = recognizer_factory(self._model_dir)
        self._recognizer = recognizer
        self._stream = None
        self._on_text: Callable[[Segment], None] | None = None
        self._source: str | None = None
        self._fed_samples = 0
        self._last_partial = ""

    @property
    def running(self) -> bool:
        return self._stream is not None

    @property
    def source(self) -> str | None:
        return self._source

    def start(self, source: str,
              on_text: Callable[[Segment], None]) -> None:
        validate_source(source)
        if on_text is None or not callable(on_text):
            raise EngineError("on_text callback is required")
        if self._stream is not None:
            raise EngineError("engine already running")
        self._stream = self._recognizer.create_stream()
        self._on_text = on_text
        self._source = source
        self._fed_samples = 0
        self._last_partial = ""

    def feed(self, samples, sample_rate: int = ENGINE_RATE_HZ) -> None:
        """Accept one 16 kHz mono float32 chunk; emits via on_text."""
        if self._stream is None or self._on_text is None:
            raise EngineError("engine is not running")
        if sample_rate != ENGINE_RATE_HZ:
            raise EngineError(
                f"samples must be {ENGINE_RATE_HZ} Hz, got {sample_rate}")
        try:
            import numpy as np

            chunk = np.asarray(list(samples), dtype=np.float32)
            if chunk.ndim != 1:
                raise EngineError("samples must be mono (1-D)")
        except ImportError:
            chunk = [float(v) for v in samples]
        if len(chunk) == 0:
            return
        self._fed_samples += len(chunk)
        offset_ms = int(self._fed_samples * 1000 / ENGINE_RATE_HZ)
        stream = self._stream
        stream.accept_waveform(ENGINE_RATE_HZ, chunk)
        while self._recognizer.is_ready(stream):
            self._recognizer.decode_stream(stream)
        partial = self._recognizer.get_result(stream).strip()
        if partial and partial != self._last_partial:
            self._last_partial = partial
            self._on_text(Segment(partial, False, offset_ms))
        if self._recognizer.is_endpoint(stream):
            final = self._recognizer.get_result_all(stream).text.strip()
            self._recognizer.reset(stream)
            self._last_partial = ""
            if final:
                self._on_text(Segment(final, True, offset_ms))

    def reset(self) -> None:
        """Drop the current utterance state; next feed starts fresh."""
        if self._stream is None:
            raise EngineError("engine is not running")
        self._recognizer.reset(self._stream)
        self._last_partial = ""

    def flush(self) -> None:
        """Emit the current hypothesis as final and reset the stream."""
        if self._stream is None or self._on_text is None:
            raise EngineError("engine is not running")
        final = self._recognizer.get_result_all(self._stream).text.strip()
        self._recognizer.reset(self._stream)
        self._last_partial = ""
        if final:
            offset_ms = int(self._fed_samples * 1000 / ENGINE_RATE_HZ)
            self._on_text(Segment(final, True, offset_ms))

    def stop(self) -> None:
        self._stream = None
        self._on_text = None
        self._source = None


__all__ = ["SherpaEngine", "model_files_present"]
