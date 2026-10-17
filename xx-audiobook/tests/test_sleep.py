"""Sleep timer: 10 s fade, pause-aware countdown, end-of-chapter mode."""

import pytest

from xx_audiobook.sleep import (
    FADE_MS,
    PRESETS_MINUTES,
    SleepCounting,
    SleepOff,
    SleepTimer,
)


class FakeTarget:
    def __init__(self) -> None:
        self.volume = 1.0
        self.pauses = 0
        self.playing = False
        self.position = 0
        self.chapter_end = 0
        self.rate = 1.0

    def set_volume(self, volume: float) -> None:
        self.volume = volume

    def pause(self) -> None:
        self.pauses += 1
        self.playing = False

    def position_ms(self) -> int:
        return self.position

    def chapter_end_ms(self) -> int:
        return self.chapter_end

    def speed(self) -> float:
        return self.rate

    def is_playing(self) -> bool:
        return self.playing


def _timer(target=None, now=None):
    target = target or FakeTarget()
    clock = now if now is not None else [0]
    return SleepTimer(target, now_ms=lambda: clock[0]), target, clock


def test_presets_and_fade_constant():
    assert FADE_MS == 10000
    assert PRESETS_MINUTES == (5, 10, 15, 30, 45, 60, 90)


def test_start_rejects_non_positive():
    timer, _target, _clock = _timer()
    with pytest.raises(ValueError):
        timer.start(0)
    with pytest.raises(ValueError):
        timer.start(-5)


def test_countdown_pauses_with_playback():
    timer, target, clock = _timer()
    target.playing = False
    timer.start(60000)
    clock[0] += 30000
    state = timer.tick()
    assert isinstance(state, SleepCounting)
    assert state.remaining_ms == 60000
    assert state.fading is False
    target.playing = True
    clock[0] += 5000
    assert timer.tick().remaining_ms == 55000


def test_fade_ramps_volume_then_pauses_and_restores():
    timer, target, clock = _timer()
    target.playing = True
    timer.start(12000)
    clock[0] += 2000
    state = timer.tick()
    assert state.fading is True
    assert abs(target.volume - 1.0) < 0.001
    clock[0] += 5000
    state = timer.tick()
    assert state.remaining_ms == 5000
    assert abs(target.volume - 0.5) < 0.001
    clock[0] += 5000
    state = timer.tick()
    assert isinstance(state, SleepOff)
    assert target.pauses == 1
    assert abs(target.volume - 1.0) < 0.001
    assert timer.is_running is False


def test_seek_back_out_of_fade_restores_volume():
    timer, target, clock = _timer()
    target.playing = True
    target.chapter_end = 3600000
    timer.start_end_of_chapter()
    target.position = 3596000
    timer.tick()
    assert target.volume < 1.0
    target.position = 1000
    state = timer.tick()
    assert isinstance(state, SleepCounting)
    assert state.fading is False
    assert abs(target.volume - 1.0) < 0.001


def test_end_of_chapter_captures_end_once_and_scales_speed():
    timer, target, clock = _timer()
    target.playing = True
    target.position = 3500000
    target.chapter_end = 3600000
    target.rate = 2.0
    timer.start_end_of_chapter()
    assert timer.tick().remaining_ms == 50000
    target.chapter_end = 7200000
    clock[0] += 10000
    assert timer.tick().remaining_ms == 50000
    target.position = 3550000
    assert timer.tick().remaining_ms == 25000


def test_end_of_chapter_pauses_at_zero_without_clock_drift():
    timer, target, clock = _timer()
    target.playing = True
    target.position = 3599000
    target.chapter_end = 3600000
    timer.start_end_of_chapter()
    clock[0] += 60000
    assert isinstance(timer.tick(), SleepCounting)
    assert target.pauses == 0
    target.position = 3600000
    state = timer.tick()
    assert isinstance(state, SleepOff)
    assert target.pauses == 1
    assert abs(target.volume - 1.0) < 0.001


def test_cancel_restores_volume_and_stops():
    timer, target, clock = _timer()
    target.playing = True
    timer.start(11000)
    clock[0] += 5000
    timer.tick()
    assert target.volume < 1.0
    timer.cancel()
    assert isinstance(timer.state, SleepOff)
    assert abs(target.volume - 1.0) < 0.001
    assert timer.remaining_ms() == 0


def test_restart_while_running_resets():
    timer, target, clock = _timer()
    target.playing = True
    timer.start(60000)
    clock[0] += 10000
    timer.tick()
    timer.start(30000)
    assert timer.tick().remaining_ms == 30000
