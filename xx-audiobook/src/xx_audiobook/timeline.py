"""Book-level timeline over tracks (M2).

Mirrors playback/TrackMath.kt (BookTimeline: locate/globalOf/chapterAt/
chapterStart/chapterEndAt, the 1 s normalize rule, mediaId), plus
playback/PlayerCommands.kt transport math, BrowseTree.resumeMs and
AutoRewind. Pure functions and small dataclasses; no I/O, no threads.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

MIN_SPEED = 0.5
MAX_SPEED = 3.0
RESTART_THRESHOLD_MS = 3000
SKIP_CHOICES = (10, 15, 30)
SPEED_STEPS = (0.5, 0.75, 1.0, 1.1, 1.2, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
NORMALIZE_TOLERANCE_MS = 1000
AUTO_REWIND_MIN_PAUSE_MS = 10000


def seconds_to_ms(value: float) -> int:
    """Kotlin (s * 1000).roundToLong(): halves round up, never banker's."""
    return int(math.floor(value * 1000.0 + 0.5))


@dataclass
class Track:
    file_id: str
    uri: str
    start_ms: int = 0
    duration_ms: int = 0
    mime: str = ""

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


@dataclass
class Chapter:
    index: int
    title: str
    start_ms: int = 0
    end_ms: int = 0


@dataclass
class Position:
    track_index: int = 0
    offset_ms: int = 0


def normalize_offsets(tracks: list[Track]) -> list[Track]:
    """Trust server start offsets within 1 s of the running sum, else relay."""
    expected = 0
    for track in tracks:
        if abs(track.start_ms - expected) > NORMALIZE_TOLERANCE_MS:
            break
        expected = track.start_ms + track.duration_ms
    else:
        return tracks
    laid: list[Track] = []
    start = 0
    for track in tracks:
        laid.append(Track(track.file_id, track.uri, start,
                          track.duration_ms, track.mime))
        start += track.duration_ms
    return laid


def chapters_of(dtos: list) -> list[Chapter]:
    """Chapter DTOs (api.Chapter: index/title/start_s/end_s) to book ms."""
    return [Chapter(index=c.index, title=c.title,
                    start_ms=seconds_to_ms(c.start_s),
                    end_ms=seconds_to_ms(c.end_s)) for c in dtos]


class BookTimeline:
    """One book as one timeline over its tracks."""

    def __init__(self, tracks: list[Track],
                 chapters: list[Chapter] | None = None) -> None:
        self.tracks = normalize_offsets(list(tracks))
        self.chapters = sorted(chapters or [], key=lambda c: c.start_ms)

    @property
    def duration_ms(self) -> int:
        if not self.tracks:
            return 0
        return self.tracks[-1].end_ms

    @classmethod
    def from_play(cls, play, resolve_uri) -> BookTimeline:
        tracks = [Track(t.file_id, resolve_uri(t.file_id, t.url),
                        seconds_to_ms(t.start_offset_s),
                        seconds_to_ms(t.duration_s), t.mime)
                  for t in play.tracks]
        return cls(tracks, chapters_of(play.chapters))

    def locate(self, global_ms: int) -> Position:
        if not self.tracks:
            return Position(0, 0)
        moment = min(max(global_ms, 0), self.duration_ms)
        index = 0
        for i, track in enumerate(self.tracks):
            if track.start_ms <= moment:
                index = i
        track = self.tracks[index]
        offset = min(max(moment - track.start_ms, 0), track.duration_ms)
        return Position(index, offset)

    def global_of(self, track_index: int, offset_ms: int) -> int:
        if track_index < 0 or track_index >= len(self.tracks):
            return 0
        track = self.tracks[track_index]
        offset = min(max(offset_ms, 0), track.duration_ms)
        return min(max(track.start_ms + offset, 0), self.duration_ms)

    def chapter_at(self, global_ms: int) -> int:
        if not self.chapters:
            return -1
        moment = min(max(global_ms, 0), self.duration_ms)
        index = 0
        for i, chapter in enumerate(self.chapters):
            if chapter.start_ms <= moment:
                index = i
        return index

    def chapter_start(self, chapter_index: int) -> Position | None:
        if chapter_index < 0 or chapter_index >= len(self.chapters):
            return None
        return self.locate(self.chapters[chapter_index].start_ms)

    def chapter_end_at(self, global_ms: int) -> int:
        index = self.chapter_at(global_ms)
        if index < 0:
            return self.duration_ms
        return min(self.chapters[index].end_ms, self.duration_ms)


@dataclass
class LoadedBook:
    item_id: str
    title: str = ""
    author: str = ""
    cover_url: str | None = None
    session_id: str | None = None
    timeline: BookTimeline = field(
        default_factory=lambda: BookTimeline([]))

    def media_id(self, track_index: int) -> str:
        return f"{self.item_id}/{self.timeline.tracks[track_index].file_id}"


def track_index_of(book: LoadedBook, media_id: str) -> int:
    """"{item_id}/{file_id}" to a track index, else -1."""
    head, sep, file_id = media_id.partition("/")
    if not sep or head != book.item_id or not file_id:
        return -1
    for i, track in enumerate(book.timeline.tracks):
        if track.file_id == file_id:
            return i
    return -1


def skip_to(timeline: BookTimeline, global_ms: int, delta_ms: int) -> int:
    return min(max(global_ms + delta_ms, 0), timeline.duration_ms)


def next_chapter(timeline: BookTimeline, global_ms: int) -> int | None:
    index = timeline.chapter_at(global_ms)
    if index < 0:
        return None
    following = timeline.chapters[index + 1] if (
        index + 1 < len(timeline.chapters)) else None
    return following.start_ms if following is not None else None


def previous_chapter(timeline: BookTimeline, global_ms: int,
                     threshold_ms: int = RESTART_THRESHOLD_MS) -> int:
    index = timeline.chapter_at(global_ms)
    if index < 0:
        return 0
    start = timeline.chapters[index].start_ms
    if global_ms - start > threshold_ms or index == 0:
        return start
    return timeline.chapters[index - 1].start_ms


def clamp_speed(speed: float) -> float:
    if isinstance(speed, float) and math.isnan(speed):
        return 1.0
    return min(max(speed, MIN_SPEED), MAX_SPEED)


def step_speed(current: float, up: bool) -> float:
    clamped = clamp_speed(current)
    if up:
        for step in SPEED_STEPS:
            if step > clamped + 0.001:
                return step
        return MAX_SPEED
    for step in reversed(SPEED_STEPS):
        if step < clamped - 0.001:
            return step
    return MIN_SPEED


def initial_speed(remembered: float | None, default: float) -> float:
    return clamp_speed(default if remembered is None else remembered)


def skip_ms(seconds: int) -> int:
    return min(max(seconds, 5), 120) * 1000


def resume_ms(local, last_item_id: str | None, last_position_s: float,
               item_id: str, duration_ms: int) -> int:
    """Where a book starts: 0 when finished, else local progress, else the
    last-played pointer for this book."""
    if local is not None and local.is_finished:
        return 0
    fallback = last_position_s if last_item_id == item_id else 0.0
    position_s = local.position_s if local is not None else fallback
    moment = seconds_to_ms(position_s)
    if duration_ms > 0:
        return min(max(moment, 0), duration_ms)
    return max(moment, 0)


def auto_rewind_ms(paused_at_ms: int, now_ms: int, seconds: int) -> int:
    """Resume earlier after a pause of 10 s or more; else no rewind."""
    if paused_at_ms <= 0 or seconds <= 0:
        return 0
    if now_ms - paused_at_ms < AUTO_REWIND_MIN_PAUSE_MS:
        return 0
    return seconds * 1000
