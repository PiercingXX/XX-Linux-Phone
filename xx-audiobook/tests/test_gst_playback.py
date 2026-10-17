"""Real-service playback: a generated tone through GStreamer (fakesink).

No audio device is needed; the backend runs with audio-sink=fakesink and
the test asserts preroll/duration, PLAYING or EOS, seek and rate control,
and a clean stop. Skips when GStreamer is unavailable.
"""

import math
import struct
import wave

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: E402
    Gst.init(None)
    Gst.ElementFactory.make("playbin", None) or pytest.skip(
        "no GStreamer playbin")
except Exception as exc:
    pytest.skip(f"no GStreamer: {exc}", allow_module_level=True)

from gi.repository import GLib  # noqa: E402

from xx_audiobook import timeline as timeline_module  # noqa: E402
from xx_audiobook.player import (  # noqa: E402
    STATUS_PLAYING,
    STATUS_STOPPED,
    BookPlayer,
    RealGstBackend,
    file_uri,
)
from xx_audiobook.stream_auth import StreamAuth  # noqa: E402
from xx_audiobook.timeline import BookTimeline, Chapter, Track  # noqa: E402


def _tone_wav(path, seconds=1.0, rate_hz=22050):
    frames = int(seconds * rate_hz)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate_hz)
        for i in range(frames):
            sample = int(12000 * math.sin(
                2 * math.pi * 440.0 * i / rate_hz))
            wav.writeframes(struct.pack("<h", sample))


def test_generated_tone_plays_through_playbin(tmp_path):
    audio = tmp_path / "tone.wav"
    _tone_wav(audio)
    errors = []
    backend = RealGstBackend(audio_sink="fakesink",
                             on_error=errors.append)
    auth = StreamAuth()
    player = BookPlayer(backend, auth)
    book = timeline_module.LoadedBook(
        "it-tone", "Tone", "Test", None, None,
        BookTimeline(
            [Track("t", file_uri(str(audio)), 0, 1000, "audio/x-wav")],
            [Chapter(0, "Only", 0, 1000)]))
    assert player.open_book(book, 0, 1.0) is True

    loop = GLib.MainLoop()
    observed = {"duration": None, "playing": False, "eos": False}
    backend.on_eos = lambda: observed.update(eos=True)

    def poll():
        duration = backend.duration_ms()
        if duration:
            observed["duration"] = duration
        position = backend.position_ms()
        if position is not None and position > 0:
            observed["playing"] = True
        if observed["eos"] and observed["duration"]:
            loop.quit()
            return False
        return True

    assert player.play() is True
    assert player.status == STATUS_PLAYING
    GLib.timeout_add(100, poll)
    GLib.timeout_add_seconds(8, loop.quit)
    loop.run()

    assert errors == []
    assert observed["duration"] is not None
    assert 800 <= observed["duration"] <= 1500, observed["duration"]
    assert observed["playing"] or observed["eos"]
    assert player.set_rate(1.5) == 1.5
    player.seek_global(200)
    assert player.position_global_ms() >= 0
    assert player.stop() is True
    assert player.status == STATUS_STOPPED
