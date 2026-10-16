"""Real-speech verification for both engines (skips cleanly offline).

Needs the downloaded models in ./models/<id> (or the XDG install dir)
plus either the fixture wavs extracted from the sherpa archive or
espeak-ng to synthesize clips. No network is ever used here.
"""
from __future__ import annotations

import shutil
import string
import subprocess
import wave
from pathlib import Path

import pytest

from xx_captions import models as model_mgr
from xx_captions import resample
from xx_captions import config

ROOT = Path(__file__).resolve().parent.parent
LOCAL_MODELS = ROOT / "models"

FOX_TEXT = "the quick brown fox jumps over the lazy dog"
TWO_TEXT = ("the weather is pleasant today. "
            "we will meet in the lobby at noon")


def _model_dir(model_id: str) -> Path:
    local = LOCAL_MODELS / model_id
    if all((local / name).is_file()
           for name in config.MODEL_CATALOG[model_id]["files"]):
        return local
    home = model_mgr.model_dir(model_id)
    if all((home / name).is_file()
           for name in config.MODEL_CATALOG[model_id]["files"]):
        return home
    pytest.skip(f"model {model_id!r} not downloaded")


def _fixture(name: str) -> Path:
    path = LOCAL_MODELS / "fixtures" / name
    if not path.is_file():
        pytest.skip(f"fixture {name!r} not extracted under ./models")
    return path


def _need_tts() -> str:
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if binary is None:
        pytest.skip("no espeak-ng/espeak on PATH")
    return binary


def _synthesize(binary: str, text: str, target: Path) -> None:
    proc = subprocess.run(
        [binary, "-v", "en", "-s", "150", "-w", str(target), text],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def _load_16k(path: Path) -> list[float]:
    with wave.open(str(path)) as wav:
        raw = wav.readframes(wav.getnframes())
        return resample.pcm16_to_mono_float32_16k(
            raw, wav.getframerate(), wav.getnchannels())


def _norm(text: str) -> str:
    return " ".join("".join(c for c in word.lower()
                            if c not in string.punctuation)
                    for word in text.split())


def _wer(reference: str, hypothesis: str) -> float:
    ref, hyp = _norm(reference).split(), _norm(hypothesis).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i in range(1, len(ref) + 1):
        cur = [i]
        for j in range(1, len(hyp) + 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ref[i - 1] != hyp[j - 1])))
        prev = cur
    return prev[len(hyp)] / len(ref)


def _stream(engine, samples: list[float], chunk: int = 1600) -> tuple[str, int]:
    """Feed chunks plus trailing quiet, return (final_text, partials)."""
    seen = []
    engine.start("playback", seen.append)
    for i in range(0, len(samples), chunk):
        engine.feed(samples[i:i + chunk])
    engine.feed([0.0] * 8000)
    engine.flush()
    engine.stop()
    finals = " ".join(segment.text for segment in seen if segment.final)
    partials = sum(1 for segment in seen if not segment.final)
    return finals, partials


def test_sherpa_fixture_word_error_rate() -> None:
    sherpa_onnx = pytest.importorskip("sherpa_onnx")  # noqa: F841
    from xx_captions.engines.sherpa import SherpaEngine

    path = _fixture("0.wav")
    engine = SherpaEngine(_model_dir(config.SHERPA_ID))
    finals, partials = _stream(engine, _load_16k(path))
    assert partials >= 1
    assert finals
    ref = ("AFTER EARLY NIGHTFALL THE YELLOW LAMPS WOULD LIGHT UP HERE "
           "AND THERE THE SQUALID QUARTER OF THE BROTHELS")
    assert _wer(ref, finals) <= 0.15


def test_vosk_fixture_word_error_rate() -> None:
    pytest.importorskip("vosk")
    from xx_captions.engines.vosk import VoskEngine

    path = _fixture("0.wav")
    engine = VoskEngine(_model_dir(config.VOSK_ID))
    finals, partials = _stream(engine, _load_16k(path))
    assert partials >= 1
    assert finals
    ref = ("AFTER EARLY NIGHTFALL THE YELLOW LAMPS WOULD LIGHT UP HERE "
           "AND THERE THE SQUALID QUARTER OF THE BROTHELS")
    assert _wer(ref, finals) <= 0.15
    assert engine.last_words and engine.last_words[0]["word"] == "after"


def test_sherpa_upsampled_8k_fixture() -> None:
    pytest.importorskip("sherpa_onnx")
    from xx_captions.engines.sherpa import SherpaEngine

    path = _fixture("8k.wav")
    engine = SherpaEngine(_model_dir(config.SHERPA_ID))
    finals, _ = _stream(engine, _load_16k(path))
    ref = ("YET THESE THOUGHTS AFFECTED HESTER PRYNNE LESS WITH HOPE "
           "THAN APPREHENSION")
    assert _wer(ref, finals) <= 0.15


def test_synthesized_fox_both_engines(tmp_path: Path) -> None:
    pytest.importorskip("sherpa_onnx")
    pytest.importorskip("vosk")
    from xx_captions.engines.sherpa import SherpaEngine
    from xx_captions.engines.vosk import VoskEngine

    clip = tmp_path / "fox.wav"
    _synthesize(_need_tts(), FOX_TEXT, clip)
    samples = _load_16k(clip)
    sherpa_finals, _ = _stream(SherpaEngine(_model_dir(config.SHERPA_ID)),
                               samples)
    vosk_finals, _ = _stream(VoskEngine(_model_dir(config.VOSK_ID)), samples)
    assert _wer(FOX_TEXT, sherpa_finals) <= 0.4
    assert _wer(FOX_TEXT, vosk_finals) <= 0.4


def test_sherpa_20m_fixture_word_error_rate() -> None:
    """Phone-default small model stays inside lane 1's WER ceiling."""
    pytest.importorskip("sherpa_onnx")
    from xx_captions.engines.sherpa import SherpaEngine

    engine = SherpaEngine(_model_dir(config.SHERPA_20M_ID))
    for name, ref in (
        ("0.wav", "AFTER EARLY NIGHTFALL THE YELLOW LAMPS WOULD LIGHT UP "
                   "HERE AND THERE THE SQUALID QUARTER OF THE BROTHELS"),
        ("8k.wav", "YET THESE THOUGHTS AFFECTED HESTER PRYNNE LESS WITH "
                    "HOPE THAN APPREHENSION"),
    ):
        # Live capture runs before speech starts; the lead silence keeps
        # the small model's first-word onset, trailing silence its endpoint.
        samples = [0.0] * 8000 + _load_16k(_fixture(name))
        seen = []
        engine.start("playback", seen.append)
        for i in range(0, len(samples), 800):
            engine.feed(samples[i:i + 800])
        engine.feed([0.0] * 32000)
        engine.flush()
        engine.stop()
        finals = " ".join(segment.text for segment in seen if segment.final)
        assert sum(1 for segment in seen if not segment.final) >= 1
        assert finals
        assert _wer(ref, finals) <= 0.15


def test_synthesized_two_sentence_smoke(tmp_path: Path) -> None:
    pytest.importorskip("sherpa_onnx")
    pytest.importorskip("vosk")
    from xx_captions.engines.sherpa import SherpaEngine
    from xx_captions.engines.vosk import VoskEngine

    clip = tmp_path / "two.wav"
    _synthesize(_need_tts(), TWO_TEXT, clip)
    samples = _load_16k(clip)
    sherpa_finals, _ = _stream(SherpaEngine(_model_dir(config.SHERPA_ID)),
                               samples)
    vosk_finals, _ = _stream(VoskEngine(_model_dir(config.VOSK_ID)), samples)
    assert sherpa_finals.strip()
    assert vosk_finals.strip()
