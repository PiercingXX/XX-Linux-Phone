from __future__ import annotations

import json
from pathlib import Path

import pytest

from xx_captions import engine as engine_base
from xx_captions.dbus_api import ContractError
from xx_captions.engine import EngineError, Segment
from xx_captions.engines import select_engine, sherpa_status, vosk_status
from xx_captions.engines import sherpa as sherpa_mod
from xx_captions.engines import vosk as vosk_mod


class FakeSherpaStream:
    """Mirrors sherpa_onnx.OnlineStream: accept_waveform(rate, samples)."""

    def __init__(self) -> None:
        self.fed: list = []
        self.finished = False

    def accept_waveform(self, sample_rate, samples) -> None:
        assert sample_rate == 16000
        self.fed.append(list(samples))

    def input_finished(self) -> None:
        self.finished = True


class FakeSherpaRecognizer:
    """Mirrors OnlineRecognizer: ready/decode/result/endpoint/reset."""

    def __init__(self, endpoint_after: int = 3) -> None:
        self.stream = FakeSherpaStream()
        self.feeds = 0
        self.endpoint_after = endpoint_after
        self.resets = 0
        self.decodes = 0

    def create_stream(self, hotwords=None):
        self.stream = FakeSherpaStream()
        return self.stream

    def is_ready(self, stream) -> bool:
        ready = self._pending > 0
        self._pending = 0
        return ready

    def decode_stream(self, stream) -> None:
        self.decodes += 1

    def get_result(self, stream) -> str:
        if self.feeds == 0:
            return ""
        return " ".join(f"word{i}" for i in range(self.feeds))

    def get_result_all(self, stream):
        from collections import namedtuple

        Result = namedtuple("Result", ["text"])
        return Result(" ".join(f"word{i}" for i in range(self.feeds)))

    def is_endpoint(self, stream) -> bool:
        return self.feeds >= self.endpoint_after

    def reset(self, stream) -> None:
        self.resets += 1
        self.feeds = 0

    def note_feed(self) -> None:
        self.feeds += 1
        self._pending = 1

    _pending = 0


class ScriptedSherpaRecognizer(FakeSherpaRecognizer):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.streams_fed: list = []

    def create_stream(self, hotwords=None):
        stream = FakeSherpaStream()
        orig = stream.accept_waveform

        def accept(sample_rate, samples):
            orig(sample_rate, samples)
            self.note_feed()

        stream.accept_waveform = accept  # type: ignore[method-assign]
        return stream


def _sherpa_factory(recognizer):
    def factory(model_dir):
        return recognizer
    return factory


def _start(recognizer_factory, cls, **kwargs):
    seen: list[Segment] = []
    inst = cls("/nonexistent", recognizer_factory=recognizer_factory, **kwargs)
    inst.start("playback", seen.append)
    return inst, seen


def test_sherpa_partial_then_final_with_reset() -> None:
    rec = ScriptedSherpaRecognizer(endpoint_after=3)
    inst, seen = _start(lambda d: rec, sherpa_mod.SherpaEngine)
    inst.feed([0.0] * 1600)
    assert seen == [Segment("word0", False, 100)]
    inst.feed([0.0] * 1600)
    assert seen[-1] == Segment("word0 word1", False, 200)
    inst.feed([0.0] * 1600)
    assert seen[-1] == Segment("word0 word1 word2", True, 300)
    assert rec.resets == 1
    inst.feed([0.0] * 1600)
    assert seen[-1] == Segment("word0", False, 400)
    inst.stop()


def test_sherpa_duplicate_partials_suppressed() -> None:
    rec = ScriptedSherpaRecognizer(endpoint_after=99)
    inst, seen = _start(lambda d: rec, sherpa_mod.SherpaEngine)
    inst.feed([0.0] * 1600)
    first = list(seen)
    rec.feeds -= 1
    inst.feed([0.0] * 1600)
    assert seen == first
    inst.stop()


def test_sherpa_reset_clears_utterance() -> None:
    rec = ScriptedSherpaRecognizer(endpoint_after=99)
    inst, seen = _start(lambda d: rec, sherpa_mod.SherpaEngine)
    inst.feed([0.0] * 1600)
    assert seen
    inst.reset()
    assert rec.resets == 1
    with pytest.raises(EngineError):
        sherpa_mod.SherpaEngine("/x",
                                recognizer_factory=lambda d: rec).reset()


def test_sherpa_rejects_bad_calls() -> None:
    rec = ScriptedSherpaRecognizer()
    inst = sherpa_mod.SherpaEngine("/x", recognizer_factory=lambda d: rec)
    with pytest.raises(ContractError):
        inst.start("bluetooth", lambda s: None)
    with pytest.raises(EngineError):
        inst.start("playback", None)  # type: ignore[arg-type]
    with pytest.raises(EngineError):
        inst.feed([0.0] * 16)
    inst.start("mic", lambda s: None)
    with pytest.raises(EngineError):
        inst.start("mic", lambda s: None)
    with pytest.raises(EngineError):
        inst.feed([0.0] * 16, sample_rate=44100)
    assert inst.running is True
    assert inst.source == "mic"
    inst.feed([])
    inst.stop()
    inst.stop()
    assert inst.running is False
    assert inst.source is None


def test_sherpa_missing_model_dir_raises() -> None:
    with pytest.raises(EngineError):
        sherpa_mod.SherpaEngine("/nonexistent-model-dir")


class FakeVoskRecognizer:
    """Mirrors vosk KaldiRecognizer JSON methods and word times."""

    def __init__(self, finals: int = 99) -> None:
        self.calls = 0
        self.finals = finals
        self.resets = 0

    def Reset(self) -> None:
        self.resets += 1
        self.calls = 0

    def SetWords(self, flag: bool) -> None:
        pass

    def SetPartialWords(self, flag: bool) -> None:
        pass

    def AcceptWaveform(self, data: bytes) -> bool:
        self.calls += 1
        return self.calls >= self.finals

    def PartialResult(self) -> str:
        return json.dumps({"partial": "hello there"})

    def Result(self) -> str:
        return json.dumps({"text": "hello world", "result": [
            {"word": "hello", "start": 0.1, "end": 0.4},
            {"word": "world", "start": 0.4, "end": 0.8},
        ]})

    def FinalResult(self) -> str:
        return json.dumps({"text": "", "result": []})


def test_vosk_partials_then_final_keeps_words() -> None:
    rec = FakeVoskRecognizer(finals=2)
    inst, seen = _start(lambda d: rec, vosk_mod.VoskEngine)
    inst.feed([0.1] * 1600)
    assert seen == [Segment("hello there", False, 100)]
    inst.feed([0.1] * 1600)
    assert seen[-1] == Segment("hello world", True, 200)
    assert inst.last_words == [
        {"word": "hello", "start": 0.1, "end": 0.4},
        {"word": "world", "start": 0.4, "end": 0.8},
    ]
    inst.stop()


def test_vosk_empty_results_emit_nothing() -> None:
    rec = FakeVoskRecognizer(finals=1)
    rec.PartialResult = lambda: json.dumps({"partial": ""})
    rec.Result = lambda: json.dumps({"text": "", "result": []})
    inst, seen = _start(lambda d: rec, vosk_mod.VoskEngine)
    inst.feed([0.0] * 1600)
    assert seen == []
    assert inst.last_words == []
    inst.stop()


def test_vosk_bad_json_is_engine_error() -> None:
    rec = FakeVoskRecognizer()
    rec.PartialResult = lambda: "{broken"
    inst, seen = _start(lambda d: rec, vosk_mod.VoskEngine)
    with pytest.raises(EngineError):
        inst.feed([0.0] * 1600)
    inst.stop()


def test_vosk_rejects_bad_calls() -> None:
    rec = FakeVoskRecognizer()
    inst = vosk_mod.VoskEngine("/x", recognizer_factory=lambda d: rec)
    with pytest.raises(ContractError):
        inst.start("bluetooth", lambda s: None)
    with pytest.raises(EngineError):
        inst.feed([0.0] * 16)
    assert inst.name == "vosk"
    inst.start("call", lambda s: None)
    with pytest.raises(EngineError):
        inst.start("call", lambda s: None)
    with pytest.raises(EngineError):
        inst.feed([0.0] * 16, sample_rate=48000)
    inst.reset()
    assert rec.resets == 2
    inst.stop()
    with pytest.raises(EngineError):
        inst.reset()


def test_vosk_missing_model_dir_raises() -> None:
    with pytest.raises(EngineError):
        vosk_mod.VoskEngine("/nonexistent-model-dir")


def test_sherpa_flush_emits_final_and_resets() -> None:
    rec = ScriptedSherpaRecognizer(endpoint_after=99)
    inst, seen = _start(lambda d: rec, sherpa_mod.SherpaEngine)
    inst.feed([0.0] * 1600)
    inst.flush()
    assert seen[-1] == Segment("word0", True, 100)
    assert rec.resets == 1
    inst.feed([0.0] * 1600)
    assert seen[-1] == Segment("word0", False, 200)
    with pytest.raises(EngineError):
        sherpa_mod.SherpaEngine("/x",
                                recognizer_factory=lambda d: rec).flush()
    inst.stop()


def test_vosk_flush_emits_final_result() -> None:
    rec = FakeVoskRecognizer(finals=99)
    rec.FinalResult = lambda: json.dumps({"text": "hello world", "result": [
        {"word": "hello", "start": 0.0, "end": 0.3}]})
    inst, seen = _start(lambda d: rec, vosk_mod.VoskEngine)
    inst.feed([0.1] * 1600)
    partials = [s for s in seen if not s.final]
    assert partials
    inst.flush()
    assert seen[-1] == Segment("hello world", True, 100)
    assert inst.last_words == [{"word": "hello", "start": 0.0, "end": 0.3}]
    with pytest.raises(EngineError):
        vosk_mod.VoskEngine("/x",
                            recognizer_factory=lambda d: rec).flush()
    inst.stop()


def test_fake_engine_still_conformant() -> None:
    fake = engine_base.FakeEngine()
    assert fake.name == "fake"
    assert fake.running is False


def test_selection_prefers_sherpa(tmp_path: Path) -> None:
    home = tmp_path / "home"
    sentinel = {"sherpa": False, "vosk": False}

    def sherpa_factory(model_dir):
        sentinel["sherpa"] = True
        return object()

    def vosk_factory(model_dir):
        sentinel["vosk"] = True
        return object()

    import xx_captions.engines as engines_pkg

    orig_status = (engines_pkg.sherpa_status, engines_pkg.vosk_status)
    engines_pkg.sherpa_status = lambda home=None: (True, "ready")  # type: ignore
    engines_pkg.vosk_status = lambda home=None: (True, "ready")  # type: ignore
    try:
        sel = select_engine(home, sherpa_factory=sherpa_factory,
                            vosk_factory=vosk_factory)
    finally:
        engines_pkg.sherpa_status, engines_pkg.vosk_status = orig_status
    assert sel.name == "sherpa-onnx"
    assert sel.engine is not None
    assert sentinel == {"sherpa": True, "vosk": False}


def test_selection_falls_back_to_vosk(tmp_path: Path) -> None:
    home = tmp_path / "home"
    import xx_captions.engines as engines_pkg

    orig = (engines_pkg.sherpa_status, engines_pkg.vosk_status)
    engines_pkg.sherpa_status = lambda home=None: (False, "no sherpa")  # type: ignore
    engines_pkg.vosk_status = lambda home=None: (True, "ready")  # type: ignore
    try:
        sel = select_engine(home, vosk_factory=lambda d: object())
    finally:
        engines_pkg.sherpa_status, engines_pkg.vosk_status = orig
    assert sel.name == "vosk"
    assert sel.engine is not None


def test_selection_none_state_never_crashes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert sherpa_status(home) == (False, sherpa_status(home)[1])
    assert vosk_status(home) == (False, vosk_status(home)[1])
    sel = select_engine(home)
    assert sel.name == "none"
    assert sel.engine is None
    assert "no engine installed" in sel.reason


def test_selection_survives_missing_packages(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    import xx_captions.engines as engines_pkg

    monkeypatch.setattr(engines_pkg, "_have_package", lambda name: False)
    sel = select_engine(tmp_path / "home")
    assert sel.name == "none"
    assert sel.engine is None


def test_probe_helpers_exist_for_both_backends() -> None:
    assert Path(sherpa_mod.__file__).name == "sherpa.py"
    assert Path(vosk_mod.__file__).name == "vosk.py"
    assert sherpa_mod.model_files_present("/nonexistent") is False
    assert vosk_mod.model_files_present("/nonexistent") is False
