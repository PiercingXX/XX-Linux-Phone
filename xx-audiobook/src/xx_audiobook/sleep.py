"""Sleep timer (M2).

Mirrors playback/SleepTimer.kt: presets, end-of-chapter with its end
captured once and speed-scaled, a 10 s fade to pause with the volume
restored for next time, and a countdown that pauses with playback.

Synchronous state machine: the owner polls tick() about every 250 ms
(the service wires a GLib timeout; tests drive it with a fake clock).
No threads, no sleeps, so the GTK main loop is never blocked.
"""

from __future__ import annotations

from dataclasses import dataclass

FADE_MS = 10000
PRESETS_MINUTES = (5, 10, 15, 30, 45, 60, 90)


@dataclass
class SleepOff:
    off: bool = True


@dataclass
class SleepCounting:
    remaining_ms: int
    end_of_chapter: bool = False
    fading: bool = False


class SleepTarget:
    """The bit of the player the timer drives."""

    def set_volume(self, volume: float) -> None:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def position_ms(self) -> int:
        raise NotImplementedError

    def chapter_end_ms(self) -> int:
        raise NotImplementedError

    def speed(self) -> float:
        raise NotImplementedError

    def is_playing(self) -> bool:
        raise NotImplementedError


class SleepTimer:
    def __init__(self, target: SleepTarget, now_ms) -> None:
        self._target = target
        self._now_ms = now_ms
        self._state: SleepOff | SleepCounting = SleepOff()
        self._left_ms = 0
        self._last_ms = 0
        self._end_of_chapter = False
        self._chapter_end_ms = 0
        self._faded = False
        self._running = False

    @property
    def state(self) -> SleepOff | SleepCounting:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running

    def remaining_ms(self) -> int:
        if isinstance(self._state, SleepCounting):
            return self._state.remaining_ms
        return 0

    def start(self, duration_ms: int) -> None:
        if duration_ms <= 0:
            raise ValueError("duration must be positive")
        self._begin(end_of_chapter=False, left_ms=duration_ms,
                    chapter_end_ms=0)

    def start_minutes(self, minutes: int) -> None:
        self.start(minutes * 60000)

    def start_end_of_chapter(self) -> None:
        end = self._target.chapter_end_ms()
        self._begin(end_of_chapter=True, left_ms=0, chapter_end_ms=end)
        self.tick()

    def cancel(self) -> None:
        self._running = False
        self._restore_volume()
        self._state = SleepOff()

    def _begin(self, end_of_chapter: bool, left_ms: int,
               chapter_end_ms: int) -> None:
        self._restore_volume()
        self._end_of_chapter = end_of_chapter
        self._chapter_end_ms = chapter_end_ms
        self._left_ms = left_ms
        self._last_ms = self._now_ms()
        self._running = True
        self._state = SleepCounting(
            max(self._remaining_locked(), 0), end_of_chapter, False)

    def _restore_volume(self) -> None:
        self._faded = False
        self._target.set_volume(1.0)

    def _remaining_locked(self) -> int:
        if self._end_of_chapter:
            speed = self._target.speed()
            if not speed or speed <= 0:
                speed = 1.0
            return int((self._chapter_end_ms
                        - self._target.position_ms()) / speed)
        return self._left_ms

    def tick(self) -> SleepOff | SleepCounting:
        """Advance once; returns the new state."""
        if not self._running:
            return self._state
        now = self._now_ms()
        if not self._end_of_chapter:
            if self._target.is_playing():
                self._left_ms -= max(now - self._last_ms, 0)
            self._last_ms = now
        left = max(self._remaining_locked(), 0)
        fading = left <= FADE_MS
        self._state = SleepCounting(left, self._end_of_chapter, fading)
        if fading:
            self._faded = True
            volume = min(max(left / FADE_MS, 0.0), 1.0)
            self._target.set_volume(volume)
        elif self._faded:
            self._restore_volume()
        if left <= 0:
            self._target.pause()
            self._restore_volume()
            self._state = SleepOff()
            self._running = False
        return self._state
